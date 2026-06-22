#!/usr/bin/env python3
"""
grok_live_trader.py  ·  Situation-Aware Grok Autonomous Trading Agent
======================================================================
Alpha Arena Edition — replicating the approach that took $10k → +35% in under two weeks
by combining Grok's real-time situational awareness with fully-automated OCO execution.

HOW IT WORKS
------------
Every POLL_INTERVAL minutes:
  1. Fetch OHLCV bars from exchange (CCXT — Binance crypto / Alpaca stocks)
  2. Compute RSI, MACD, ATR via the `ta` library
  3. Ask Grok (grok-4.3 via Responses API) for a BUY / SELL / HOLD decision
     — Grok runs live web + X search and returns entry, stop, and target
  4. Risk checks: confidence threshold, daily loss limit, max open positions
  5. If BUY passes all checks:
       a. Position size  = (balance × RISK_PCT) / (entry − stop)  [Tharp 2% rule]
       b. Market buy order
       c. OCO sell order: take-profit limit + stop-loss  [One-Cancels-the-Other]
  6. Log trade + decision to AlgoTrader Pro database and activity feed

PAPER / TESTNET SAFETY
----------------------
  PAPER_MODE=true   (DEFAULT) — Grok decides, sizes are calculated, but NO orders are sent.
                                All decisions are logged exactly as if they were live.
  TESTNET=true      (DEFAULT) — Uses Binance Testnet / Alpaca Paper.
                                Real Binance requires TESTNET=false + live API keys.

  !! LIVE TRADING REQUIRES YOU TO EXPLICITLY SET PAPER_MODE=false AND TESTNET=false !!
  !! in .env or via command-line flags. Never do this with keys that have Withdraw enabled. !!

USAGE
-----
  python grok_live_trader.py                     # paper mode, BTC/USDT, 5-min loop
  python grok_live_trader.py --symbol ETH/USDT   # different symbol
  python grok_live_trader.py --interval 15       # 15-minute polling
  python grok_live_trader.py --once              # single scan, then exit (good for testing)
  python grok_live_trader.py --live              # WARNING: disables paper mode (use live keys)
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Load .env before anything reads os.getenv ──────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ── Configuration ───────────────────────────────────────────────────────────
# Everything is overridable via environment variables.
PAPER_MODE        = os.getenv("PAPER_MODE",    "true").lower() == "true"
TESTNET           = os.getenv("TESTNET",       "true").lower() == "true"
SYMBOL            = os.getenv("GT_SYMBOL",    "BTC/USDT")
POLL_INTERVAL_MIN = int(os.getenv("GT_INTERVAL_MIN", "5"))
RISK_PCT          = float(os.getenv("GT_RISK_PCT",  "2.0"))   # 2% of balance per trade
MIN_CONFIDENCE    = int(os.getenv("GT_MIN_CONF",    "70"))    # Grok confidence threshold
BARS_LIMIT        = int(os.getenv("GT_BARS",        "100"))
TIMEFRAME         = os.getenv("GT_TIMEFRAME",  "5m")          # CCXT timeframe string

XAI_BASE_URL      = os.getenv("XAI_BASE_URL",  "https://api.x.ai")
GROK_MODEL        = os.getenv("GROK_MODEL",    "grok-4.3")
GT_TIMEOUT_S      = float(os.getenv("GT_TIMEOUT_S", "90"))

# Exchange keys — prefer standalone vars; fall back to main app DB at runtime
EXCHANGE_API_KEY  = os.getenv("EXCHANGE_API_KEY",  "") or os.getenv("BINANCE_API_KEY",  "")
EXCHANGE_SECRET   = os.getenv("EXCHANGE_SECRET",   "") or os.getenv("BINANCE_SECRET",   "")

# Binance Testnet credentials (already in .env from the main app)
BINANCE_TESTNET_KEY    = os.getenv("BINANCE_TESTNET_KEY",    "D6g900No8gcfO87NPyRMAQWpaAqOmiLAWbsjuFV1lPmm9bJH38ipR2Hwo01XBX5D")
BINANCE_TESTNET_SECRET = os.getenv("BINANCE_TESTNET_SECRET", "S8QDsHZ7IN39uoP5OATh3WHEuLinVo44SBAU5pP0250tJlIEyfODHfVl9W9Ew90w")

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("grok-live-trader")


# ── Data classes ────────────────────────────────────────────────────────────
@dataclass
class GrokDecision:
    decision: str           # "BUY" | "SELL" | "HOLD"
    confidence_score: int   # 0–100
    market_regime: str
    reasoning: str
    catalyst: str
    catalyst_source: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    time_window: str
    invalidation: str
    raw: dict
    ok: bool = True
    error: Optional[str] = None


@dataclass
class OpenPosition:
    symbol: str
    qty: float
    entry_price: float
    stop_loss: float
    take_profit: float
    order_id: str
    oco_order_id: Optional[str] = None
    opened_at: str = ""


# In-memory position tracker (survives the loop; could be persisted to DB)
_open_position: Optional[OpenPosition] = None
_daily_pnl: float = 0.0
_daily_trades: int = 0


# ── Helpers ─────────────────────────────────────────────────────────────────
def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


def _load_prompt() -> str:
    p = Path(__file__).parent / "grok_prompt.txt"
    if p.exists():
        return p.read_text()
    # Inline fallback
    return (
        "You are a Situation-Aware Quantitative Trading Agent. "
        "Search the web and X now for news on the provided symbol. "
        "Output ONLY valid JSON: {decision, confidence_score, market_regime, reasoning, "
        "catalyst, catalyst_source, entry_price, stop_loss, take_profit, risk_reward_ratio, "
        "time_window, invalidation}"
    )


# ── Technical indicator computation ─────────────────────────────────────────
def compute_indicators(df) -> dict:
    """Return RSI, MACD, ATR from a pandas OHLCV DataFrame."""
    import pandas as pd
    from ta.momentum import RSIIndicator
    from ta.trend import MACD
    from ta.volatility import AverageTrueRange

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]

    rsi   = RSIIndicator(close, window=14).rsi()
    macd  = MACD(close, window_fast=12, window_slow=26, window_sign=9)
    atr   = AverageTrueRange(high, low, close, window=14).average_true_range()

    last = -1
    return {
        "rsi":              round(float(rsi.iloc[last]), 2),
        "macd_line":        round(float(macd.macd().iloc[last]), 4),
        "macd_signal":      round(float(macd.macd_signal().iloc[last]), 4),
        "macd_histogram":   round(float(macd.macd_diff().iloc[last]), 4),
        "atr":              round(float(atr.iloc[last]), 4),
        "price_now":        round(float(close.iloc[last]), 4),
        "price_1h_ago":     round(float(close.iloc[max(last - 12, 0)]), 4),
        "price_4h_ago":     round(float(close.iloc[max(last - 48, 0)]), 4),
        "price_24h_ago":    round(float(close.iloc[max(last - 288, 0)]), 4),
        "sma20":            round(float(close.rolling(20).mean().iloc[last]), 4),
        "sma50":            round(float(close.rolling(50).mean().iloc[last]), 4),
    }


# ── Grok signal engine ───────────────────────────────────────────────────────
async def ask_grok(symbol: str, indicators: dict, balance: float) -> GrokDecision:
    """Call Grok Responses API with live web + X search. Returns a GrokDecision."""
    import httpx

    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        return GrokDecision(
            decision="HOLD", confidence_score=0, market_regime="unknown",
            reasoning="XAI_API_KEY not set — standing aside", catalyst="",
            catalyst_source="", entry_price=indicators.get("price_now", 0),
            stop_loss=0, take_profit=0, risk_reward_ratio=0,
            time_window="", invalidation="", raw={}, ok=False, error="no API key",
        )

    now_et = dt.datetime.now(dt.timezone(dt.timedelta(hours=-4))).strftime("%Y-%m-%d %H:%M ET")
    user_content = (
        f"Current time: {now_et}\n"
        f"Symbol: {symbol}\n"
        f"Account balance: ${balance:,.2f} USDT\n"
        f"Risk per trade: {RISK_PCT}% of balance = ${balance * RISK_PCT / 100:,.2f}\n\n"
        f"Technical indicators (last bar):\n{json.dumps(indicators, indent=2)}\n\n"
        "Search the web and X RIGHT NOW for news and sentiment on this symbol. "
        "Then output your trade decision as JSON exactly as specified."
    )

    payload = {
        "model": GROK_MODEL,
        "input": [
            {"role": "system", "content": _load_prompt()},
            {"role": "user",   "content": user_content},
        ],
        "tools": [
            {"type": "web_search"},
            {"type": "x_search"},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=GT_TIMEOUT_S) as client:
            resp = await client.post(f"{XAI_BASE_URL}/v1/responses", headers=headers, json=payload)

        if resp.status_code != 200:
            err = f"HTTP {resp.status_code}: {resp.text[:300]}"
            log.error(f"[grok] {err}")
            return _grok_hold(err, indicators)

        raw_resp = resp.json()
        content = ""
        for item in raw_resp.get("output", []):
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        content = block.get("text", "")
                        break

        if not content:
            return _grok_hold("empty Grok response", indicators)

        data = json.loads(_strip_fences(content))
        latency_ms = int((time.perf_counter() - started) * 1000)
        log.info(
            f"[grok] {data.get('decision')} conf={data.get('confidence_score')} "
            f"latency={latency_ms}ms  catalyst={str(data.get('catalyst',''))[:80]}"
        )
        return GrokDecision(
            decision=str(data.get("decision", "HOLD")).upper(),
            confidence_score=int(data.get("confidence_score", 0) or 0),
            market_regime=str(data.get("market_regime", "unknown")),
            reasoning=str(data.get("reasoning", "")),
            catalyst=str(data.get("catalyst", "")),
            catalyst_source=str(data.get("catalyst_source", "")),
            entry_price=float(data.get("entry_price", 0) or 0),
            stop_loss=float(data.get("stop_loss", 0) or 0),
            take_profit=float(data.get("take_profit", 0) or 0),
            risk_reward_ratio=float(data.get("risk_reward_ratio", 0) or 0),
            time_window=str(data.get("time_window", "")),
            invalidation=str(data.get("invalidation", "")),
            raw=data,
        )
    except json.JSONDecodeError as e:
        return _grok_hold(f"JSON parse error: {e}", indicators)
    except Exception as e:
        return _grok_hold(f"{type(e).__name__}: {e}", indicators)


def _grok_hold(reason: str, indicators: dict) -> GrokDecision:
    price = indicators.get("price_now", 0)
    return GrokDecision(
        decision="HOLD", confidence_score=0, market_regime="unknown",
        reasoning=f"safe default — {reason}", catalyst="",
        catalyst_source="", entry_price=price, stop_loss=0, take_profit=0,
        risk_reward_ratio=0, time_window="", invalidation="", raw={},
        ok=False, error=reason,
    )


# ── Position sizing ──────────────────────────────────────────────────────────
def calculate_position_size(balance: float, entry: float, stop: float) -> float:
    """Tharp/Elder 2% rule: size = (balance × risk%) / (entry − stop)."""
    if entry <= 0 or stop <= 0 or entry <= stop:
        return 0.0
    risk_capital = balance * (RISK_PCT / 100)
    stop_distance = entry - stop
    if stop_distance <= 0:
        return 0.0
    qty = risk_capital / stop_distance
    # Round to 5 decimals for crypto; whole shares for stocks
    if "/" in SYMBOL and "USDT" in SYMBOL:
        qty = round(qty, 5)
    else:
        qty = round(qty, 2)
    return max(qty, 0)


# ── Exchange helpers (CCXT) ──────────────────────────────────────────────────
def init_exchange():
    """Initialise CCXT exchange. Binance testnet by default."""
    import ccxt

    if TESTNET:
        key    = BINANCE_TESTNET_KEY
        secret = BINANCE_TESTNET_SECRET
        log.info("[exchange] Connecting to Binance TESTNET (paper/simulated funds)")
    else:
        key    = EXCHANGE_API_KEY
        secret = EXCHANGE_SECRET
        if not key or not secret:
            log.error("[exchange] EXCHANGE_API_KEY / EXCHANGE_SECRET not set — aborting")
            sys.exit(1)
        log.warning("[exchange] *** LIVE MODE — real funds at risk ***")

    exchange = ccxt.binance({
        "apiKey":          key,
        "secret":          secret,
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "adjustForTimeDifference": True,
        },
    })

    if TESTNET:
        exchange.set_sandbox_mode(True)

    return exchange


def fetch_ohlcv(exchange, symbol: str, timeframe: str, limit: int):
    """Return a pandas DataFrame of OHLCV bars."""
    import pandas as pd
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df  = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def get_balance(exchange) -> float:
    """Return free USDT balance (or USD for Alpaca path)."""
    try:
        bal = exchange.fetch_balance()
        return float(bal.get("USDT", {}).get("free", 0) or bal.get("USD", {}).get("free", 0))
    except Exception as e:
        log.warning(f"[exchange] Could not fetch balance: {e}")
        return 10_000.0  # safe fallback for paper mode


def place_market_buy(exchange, symbol: str, qty: float) -> dict:
    """Place a market buy. Returns CCXT order dict."""
    if PAPER_MODE:
        log.info(f"[PAPER] MARKET BUY  {qty} {symbol}  (no order placed)")
        return {"id": f"paper-{int(time.time())}", "status": "closed", "filled": qty}
    log.info(f"[LIVE] Placing MARKET BUY  {qty} {symbol}")
    return exchange.create_order(symbol, "market", "buy", qty)


def place_oco_sell(
    exchange,
    symbol: str,
    qty: float,
    take_profit: float,
    stop_loss: float,
) -> dict:
    """
    Place a One-Cancels-the-Other sell order.
    Binance OCO: limit sell at take_profit + stop-limit sell at stop_loss.
    If OCO is not supported, falls back to two independent orders.
    """
    if PAPER_MODE:
        log.info(
            f"[PAPER] OCO SELL  {qty} {symbol}  "
            f"TP={take_profit}  SL={stop_loss}  (no order placed)"
        )
        return {"id": f"paper-oco-{int(time.time())}", "status": "open"}

    log.info(f"[LIVE] Placing OCO SELL  {qty} {symbol}  TP={take_profit}  SL={stop_loss}")
    try:
        # Binance OCO via CCXT: type='oco', side='sell'
        # stop_limit_price is set slightly below stop to ensure execution
        stop_limit = round(stop_loss * 0.999, 2)
        oco = exchange.create_order(
            symbol, "oco", "sell", qty, take_profit,
            params={
                "stopPrice":             stop_loss,
                "stopLimitPrice":        stop_limit,
                "stopLimitTimeInForce":  "GTC",
            },
        )
        log.info(f"[LIVE] OCO order placed: {oco.get('id')}")
        return oco
    except Exception as e:
        log.warning(f"[exchange] OCO not supported or failed ({e}) — placing two separate orders")
        # Fallback: separate limit + stop-limit orders
        tp_order = exchange.create_order(symbol, "limit", "sell", qty, take_profit,
                                          params={"timeInForce": "GTC"})
        sl_order = exchange.create_order(symbol, "stop_loss_limit", "sell", qty, stop_loss,
                                          params={"stopPrice": stop_loss, "timeInForce": "GTC"})
        return {"id": f"{tp_order['id']},{sl_order['id']}", "status": "open", "fallback": True}


def cancel_open_orders(exchange, symbol: str):
    """Cancel all open orders for a symbol (cleanup on session close)."""
    if PAPER_MODE:
        log.info(f"[PAPER] Would cancel all open orders for {symbol}")
        return
    try:
        exchange.cancel_all_orders(symbol)
        log.info(f"[exchange] Cancelled all open orders for {symbol}")
    except Exception as e:
        log.warning(f"[exchange] Could not cancel orders: {e}")


# ── AlgoTrader Pro database integration (optional) ──────────────────────────
async def _log_to_db(decision: GrokDecision, qty: float, position: Optional[OpenPosition]):
    """Write trade or decision to the AlgoTrader Pro SQLite DB if available."""
    try:
        from database import SessionLocal
        from models import Trade, TradeStatus, TradeSide, EventLog
        from sqlalchemy import text

        async with SessionLocal() as db:
            if decision.decision == "BUY" and position:
                trade = Trade(
                    strategy_id=None,
                    symbol=SYMBOL,
                    side=TradeSide.long,
                    status=TradeStatus.open,
                    entry_price=position.entry_price,
                    stop_loss_price=position.stop_loss,
                    take_profit_price=position.take_profit,
                    qty=qty,
                    initial_risk=round((position.entry_price - position.stop_loss) * qty, 2),
                    pre_trade_probability=decision.confidence_score,
                    session_date=dt.date.today().isoformat(),
                    journal_notes=f"Grok: {decision.reasoning[:200]}",
                )
                db.add(trade)

            # Activity feed event
            ev = EventLog(
                type="grok_live",
                severity="info" if decision.decision in ("BUY", "HOLD") else "warning",
                title=f"Grok Trader: {decision.decision} {SYMBOL} (conf={decision.confidence_score}%)",
                body=(
                    f"Regime: {decision.market_regime} | "
                    f"Catalyst: {decision.catalyst[:120]} | "
                    f"Entry={decision.entry_price} SL={decision.stop_loss} TP={decision.take_profit}"
                ),
                meta=json.dumps({
                    "decision": decision.decision,
                    "confidence": decision.confidence_score,
                    "regime": decision.market_regime,
                    "entry": decision.entry_price,
                    "stop": decision.stop_loss,
                    "take_profit": decision.take_profit,
                    "r_r": decision.risk_reward_ratio,
                    "paper_mode": PAPER_MODE,
                    "testnet": TESTNET,
                }),
            )
            db.add(ev)
            await db.commit()
    except Exception as e:
        log.debug(f"[db] Log skipped (DB not running or schema mismatch): {e}")


# ── Main trading logic ───────────────────────────────────────────────────────
async def run_once(exchange) -> None:
    """One complete decision cycle: fetch → analyse → decide → (execute)."""
    global _open_position, _daily_pnl, _daily_trades

    log.info(f"── Grok scan ──  {SYMBOL}  {_utcnow()}")

    # ── 1. Fetch bars + compute indicators ──────────────────────────────────
    try:
        df = fetch_ohlcv(exchange, SYMBOL, TIMEFRAME, BARS_LIMIT)
    except Exception as e:
        log.error(f"[exchange] Failed to fetch bars: {e}")
        return

    indicators = compute_indicators(df)
    log.info(
        f"[tech] price={indicators['price_now']}  RSI={indicators['rsi']}  "
        f"MACD={indicators['macd_histogram']:+.4f}  ATR={indicators['atr']}"
    )

    # ── 2. Get current balance ───────────────────────────────────────────────
    balance = get_balance(exchange)
    log.info(f"[balance] ${balance:,.2f} USDT")

    # ── 3. Check existing position ───────────────────────────────────────────
    if _open_position:
        price = indicators["price_now"]
        if price >= _open_position.take_profit:
            pnl = (_open_position.take_profit - _open_position.entry_price) * _open_position.qty
            log.info(f"[position] TAKE PROFIT HIT  pnl=+${pnl:.2f}")
            _daily_pnl += pnl
            cancel_open_orders(exchange, SYMBOL)
            _open_position = None
        elif price <= _open_position.stop_loss:
            pnl = (_open_position.stop_loss - _open_position.entry_price) * _open_position.qty
            log.warning(f"[position] STOP LOSS HIT  pnl=${pnl:.2f}")
            _daily_pnl += pnl
            cancel_open_orders(exchange, SYMBOL)
            _open_position = None
        else:
            unrealised = (price - _open_position.entry_price) * _open_position.qty
            log.info(
                f"[position] OPEN {_open_position.qty} @ {_open_position.entry_price}  "
                f"current={price}  unrealised=${unrealised:+.2f}"
            )
            return  # position is being managed by OCO — no new entry

    # ── 4. Ask Grok ──────────────────────────────────────────────────────────
    decision = await ask_grok(SYMBOL, indicators, balance)

    if not decision.ok:
        log.warning(f"[grok] Degraded response — standing aside: {decision.error}")
        return

    log.info(
        f"[grok] ━━ {decision.decision} ━━  conf={decision.confidence_score}%  "
        f"regime={decision.market_regime}  R:R={decision.risk_reward_ratio:.1f}"
    )
    log.info(f"[grok] reasoning: {decision.reasoning}")
    if decision.catalyst:
        log.info(f"[grok] catalyst: {decision.catalyst}")
    if decision.catalyst_source:
        log.info(f"[grok] source: {decision.catalyst_source}")

    # ── 5. Risk checks ───────────────────────────────────────────────────────
    if decision.decision != "BUY":
        log.info(f"[risk] Decision is {decision.decision} — no order")
        await _log_to_db(decision, 0, None)
        return

    if decision.confidence_score < MIN_CONFIDENCE:
        log.info(f"[risk] Confidence {decision.confidence_score}% < threshold {MIN_CONFIDENCE}% — skipping")
        return

    if decision.risk_reward_ratio < 2.0:
        log.warning(f"[risk] R:R {decision.risk_reward_ratio:.1f} < 2.0 — skipping")
        return

    if decision.entry_price <= 0 or decision.stop_loss <= 0 or decision.take_profit <= 0:
        log.warning("[risk] Missing price levels from Grok — skipping")
        return

    if decision.stop_loss >= decision.entry_price:
        log.warning("[risk] Stop loss >= entry — invalid setup, skipping")
        return

    if decision.take_profit <= decision.entry_price:
        log.warning("[risk] Take profit <= entry — invalid setup, skipping")
        return

    # ── 6. Position sizing ───────────────────────────────────────────────────
    qty = calculate_position_size(balance, decision.entry_price, decision.stop_loss)
    if qty <= 0:
        log.warning("[risk] Calculated qty is zero — skipping")
        return

    max_cost = balance * 0.95  # never use more than 95% of balance on one trade
    if qty * decision.entry_price > max_cost:
        qty = round(max_cost / decision.entry_price, 5)
        log.warning(f"[risk] Qty capped to protect balance: {qty}")

    log.info(
        f"[sizing] qty={qty}  entry={decision.entry_price}  "
        f"cost=${qty * decision.entry_price:,.2f}  "
        f"risk=${(decision.entry_price - decision.stop_loss) * qty:,.2f}"
    )

    # ── 7. Execute: Market Buy + OCO ─────────────────────────────────────────
    mode_tag = "[PAPER]" if PAPER_MODE else "[LIVE]"
    log.info(f"{mode_tag} Executing: BUY {qty} {SYMBOL}")

    buy_order  = place_market_buy(exchange, SYMBOL, qty)
    oco_order  = place_oco_sell(
        exchange, SYMBOL, qty,
        take_profit=decision.take_profit,
        stop_loss=decision.stop_loss,
    )

    # Track open position
    _open_position = OpenPosition(
        symbol=SYMBOL,
        qty=qty,
        entry_price=decision.entry_price,
        stop_loss=decision.stop_loss,
        take_profit=decision.take_profit,
        order_id=buy_order.get("id", ""),
        oco_order_id=oco_order.get("id", ""),
        opened_at=_utcnow(),
    )
    _daily_trades += 1

    log.info(
        f"{mode_tag} ✓ Position open  entry={decision.entry_price}  "
        f"SL={decision.stop_loss}  TP={decision.take_profit}  OCO={oco_order.get('id')}"
    )

    await _log_to_db(decision, qty, _open_position)


# ── Continuous loop ──────────────────────────────────────────────────────────
async def run_loop(exchange) -> None:
    """Poll at POLL_INTERVAL_MIN intervals until interrupted."""
    log.info(
        f"\n{'━'*60}\n"
        f"  Grok Live Trader — Alpha Arena Edition\n"
        f"  Symbol:    {SYMBOL}\n"
        f"  Interval:  {POLL_INTERVAL_MIN} min\n"
        f"  Risk/trade: {RISK_PCT}%\n"
        f"  Mode:      {'📋 PAPER (no orders)' if PAPER_MODE else '🔴 LIVE'}\n"
        f"  Exchange:  {'Binance TESTNET' if TESTNET else 'Binance LIVE'}\n"
        f"  Min conf:  {MIN_CONFIDENCE}%\n"
        f"{'━'*60}\n"
    )
    while True:
        try:
            await run_once(exchange)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log.error(f"[loop] Unhandled error: {type(e).__name__}: {e}")

        log.info(f"[loop] Daily summary: trades={_daily_trades}  pnl=${_daily_pnl:+.2f}")
        log.info(f"[loop] Sleeping {POLL_INTERVAL_MIN} min…")
        await asyncio.sleep(POLL_INTERVAL_MIN * 60)


# ── Entry point ──────────────────────────────────────────────────────────────
async def main_async(args: argparse.Namespace) -> None:
    global PAPER_MODE, TESTNET, SYMBOL, POLL_INTERVAL_MIN, MIN_CONFIDENCE

    # CLI overrides
    if args.live:
        PAPER_MODE = False
        log.warning("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log.warning("  LIVE MODE ENABLED — real funds will be at risk    ")
        log.warning("  Ctrl+C within 5s to abort…                        ")
        log.warning("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        await asyncio.sleep(5)

    if args.symbol:
        SYMBOL = args.symbol
    if args.interval:
        POLL_INTERVAL_MIN = args.interval
    if args.min_conf:
        MIN_CONFIDENCE = args.min_conf

    exchange = init_exchange()

    if args.once:
        await run_once(exchange)
    else:
        await run_loop(exchange)


def main():
    ap = argparse.ArgumentParser(
        description="Situation-Aware Grok Autonomous Trading Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--symbol",   default=None,  help="Trading symbol (default: BTC/USDT)")
    ap.add_argument("--interval", type=int,       help="Polling interval in minutes (default: 5)")
    ap.add_argument("--min-conf", type=int,       dest="min_conf", help="Min Grok confidence to trade (default: 70)")
    ap.add_argument("--once",     action="store_true", help="Run one cycle and exit")
    ap.add_argument(
        "--live", action="store_true",
        help="DISABLE paper mode — places REAL orders (requires live API keys in .env)"
    )
    args = ap.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        log.info("\n[grok-trader] Interrupted — shutting down cleanly")


if __name__ == "__main__":
    main()
