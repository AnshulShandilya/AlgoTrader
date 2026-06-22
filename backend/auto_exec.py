"""
auto_exec.py — Autonomous trade execution engine.

Three independent jobs:
  1. sl_tp_monitor()    — every 30 s: check ALL open trades vs live price → close at SL/TP
  2. grok_auto_entries()— every 60 s: if grok_auto_trade=True, execute pending Grok setups
  3. Helpers for placing OCO orders on Binance (fills the gap the broker module lacks)
"""
import logging
import asyncio
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from database import SessionLocal
from models import Trade, TradeStatus, TradeSide, BotSettings
from broker.factory import create_broker, create_alpaca_broker
from broker.alpaca import AlpacaBroker
from broker.binance import BinanceBroker, to_binance_symbol, LOT_SIZE_OVERRIDE

log = logging.getLogger("auto_exec")

_CRYPTO_SUFFIXES = ("/USDT", "/USD", "/BTC", "/ETH", "/BNB", "/BUSD")

_grok_setup_ids_executed: set[str] = set()


def _is_crypto(symbol: str) -> bool:
    return any(symbol.upper().endswith(s) for s in _CRYPTO_SUFFIXES)


def _write_event(type: str, title: str, severity: str = "info",
                 body: str = None, symbol: str = None, pnl: float = None,
                 meta: dict = None):
    from routers.events import write_event
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(write_event(
                type=type, title=title, severity=severity,
                body=body, symbol=symbol, pnl=pnl, meta=meta,
            ))
    except Exception:
        pass


def _close_trade_record(trade: Trade, price: float, reason: str):
    """Mutate trade fields to closed state; caller must db.commit()."""
    trade.exit_price  = price
    trade.exit_reason = reason
    trade.closed_at   = datetime.utcnow()
    trade.status      = TradeStatus.closed

    if trade.entry_price:
        mult = 1 if trade.side == TradeSide.buy else -1
        trade.pnl     = round(mult * (price - trade.entry_price) * trade.qty, 2)
        trade.pnl_pct = round(mult * (price - trade.entry_price) / trade.entry_price * 100, 2)
        if trade.initial_risk and trade.initial_risk > 0:
            trade.r_multiple = round(trade.pnl / trade.initial_risk, 2)

    pnl_val = trade.pnl or 0.0
    reason_label = {
        "stop_loss":   "stop-loss hit",
        "take_profit": "take-profit hit",
    }.get(reason, reason)
    _write_event(
        type="trade_close",
        severity="success" if pnl_val >= 0 else "error",
        title=f"{trade.symbol} auto-closed — {reason_label}",
        body=(f"P&L: {'+' if pnl_val >= 0 else ''}${pnl_val:.2f} "
              f"({trade.pnl_pct or 0:+.2f}%) · auto_exec monitor"),
        symbol=trade.symbol,
        pnl=pnl_val,
        meta={"trade_id": trade.id, "exit_reason": reason, "monitor": "auto_exec"},
    )


def _place_oco_binance(broker: BinanceBroker, symbol: str, qty: float,
                       take_profit: float, stop_loss: float) -> Optional[dict]:
    """
    Place a Binance OCO sell order (take-profit limit + stop-loss stop-limit).
    Returns the order dict or None on failure.
    """
    bsym = to_binance_symbol(symbol)
    precision = LOT_SIZE_OVERRIDE.get(bsym, 4)
    coin_qty = round(qty, precision)
    tp_price = round(take_profit, 2)
    sl_price = round(stop_loss, 2)
    # stop-limit must be 0.1% below stop trigger to guarantee fill on fast moves
    sl_limit = round(sl_price * 0.999, 2)
    try:
        order = broker.client.create_oco_order(
            symbol=bsym,
            side="SELL",
            quantity=coin_qty,
            price=str(tp_price),
            stopPrice=str(sl_price),
            stopLimitPrice=str(sl_limit),
            stopLimitTimeInForce="GTC",
        )
        log.info(f"[auto_exec] OCO placed for {symbol}: TP={tp_price} SL={sl_price}")
        return order
    except Exception as e:
        log.warning(f"[auto_exec] OCO order failed for {symbol}: {e}")
        return None


async def _get_live_price_async(broker, symbol: str) -> Optional[float]:
    """Fetch live price in a thread so we don't block the async loop."""
    loop = asyncio.get_event_loop()
    try:
        price = await loop.run_in_executor(None, broker.get_live_price, symbol)
        return float(price)
    except Exception as e:
        log.warning(f"[auto_exec] get_live_price failed for {symbol}: {e}")
        return None


async def sl_tp_monitor():
    """
    Dedicated fast SL/TP monitor.
    Runs every 30 s via the APScheduler interval job in scheduler.py.
    Checks every open trade (ALL strategies) against the live price.
    Closes at SL or TP and places the broker order.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            select(Trade).where(Trade.status == TradeStatus.open)
        )
        open_trades = result.scalars().all()
        if not open_trades:
            return

        settings_result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = settings_result.scalar_one_or_none()
        if not settings:
            return

        try:
            broker = create_broker(settings)
        except ValueError:
            return

        price_cache: dict[str, float] = {}
        committed = False

        for trade in open_trades:
            sl = trade.stop_loss_price
            tp = trade.take_profit_price

            # Skip trades with no risk levels — they should never exist, but guard anyway
            if not sl and not tp:
                continue

            sym = trade.symbol
            if sym not in price_cache:
                price = await _get_live_price_async(broker, sym)

                # Tier 2: Alpaca for US stocks/ETFs (NYSE/NASDAQ)
                if price is None and not _is_crypto(sym):
                    try:
                        alpaca = create_alpaca_broker(settings)
                        price = await _get_live_price_async(alpaca, sym)
                    except Exception:
                        pass

                # Tier 3: yfinance for UK stocks (.L), commodities (=F), anything else
                if price is None:
                    try:
                        import yfinance as yf
                        loop = asyncio.get_event_loop()
                        def _yf_price():
                            t = yf.Ticker(sym.replace("/", "-"))
                            h = t.history(period="1d", interval="1m")
                            if h is not None and not h.empty:
                                return float(h["Close"].iloc[-1])
                            return None
                        price = await loop.run_in_executor(None, _yf_price)
                    except Exception:
                        pass

                if price is None:
                    log.debug(f"[auto_exec] Could not fetch live price for {sym} — skipping SL/TP check")
                    continue
                price_cache[sym] = price

            current_price = price_cache[sym]

            if trade.side == TradeSide.buy:
                if sl and current_price <= sl:
                    log.warning(f"[auto_exec] STOP LOSS hit: trade #{trade.id} {sym} "
                                f"price={current_price} SL={sl}")
                    await _execute_close(broker, trade, current_price, "stop_loss", settings)
                    _close_trade_record(trade, current_price, "stop_loss")
                    committed = True

                elif tp and current_price >= tp:
                    log.info(f"[auto_exec] TAKE PROFIT hit: trade #{trade.id} {sym} "
                             f"price={current_price} TP={tp}")
                    await _execute_close(broker, trade, current_price, "take_profit", settings)
                    _close_trade_record(trade, current_price, "take_profit")
                    committed = True

            elif trade.side == TradeSide.sell:
                if sl and current_price >= sl:
                    log.warning(f"[auto_exec] STOP LOSS (short) hit: trade #{trade.id} {sym} "
                                f"price={current_price} SL={sl}")
                    await _execute_close(broker, trade, current_price, "stop_loss", settings,
                                         close_side="buy")
                    _close_trade_record(trade, current_price, "stop_loss")
                    committed = True

                elif tp and current_price <= tp:
                    log.info(f"[auto_exec] TAKE PROFIT (short) hit: trade #{trade.id} {sym} "
                             f"price={current_price} TP={tp}")
                    await _execute_close(broker, trade, current_price, "take_profit", settings,
                                         close_side="buy")
                    _close_trade_record(trade, current_price, "take_profit")
                    committed = True

        if committed:
            await db.commit()


async def _execute_close(broker, trade: Trade, price: float, reason: str,
                          settings, close_side: str = "sell"):
    """Place the closing broker order, log failures but don't raise."""
    loop = asyncio.get_event_loop()
    sym = trade.symbol
    qty = trade.qty

    def _do_order():
        try:
            broker.place_market_order(sym, qty, close_side)
            log.info(f"[auto_exec] Closed {sym} qty={qty} side={close_side} reason={reason}")
        except Exception as e:
            log.error(f"[auto_exec] Close order failed for {sym}: {e}")

    await loop.run_in_executor(None, _do_order)


# ─── Grok auto-entry execution ────────────────────────────────────────────────

async def grok_auto_entries():
    """
    If grok_auto_trade is enabled in BotSettings, look at the latest Grok
    scanner setups and auto-execute any that haven't been traded yet.

    SA integration:
    - If SA risk_posture == "risk_off": block all new longs; log the block.
    - If SA per_symbol bias contradicts the trade direction: add +10 pp to
      the confidence requirement (the setup must be significantly stronger).
    - If SA has a high-impact event_risk within 4h for the symbol's asset
      class: skip that setup regardless of confidence.
    """
    async with SessionLocal() as db:
        settings_result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = settings_result.scalar_one_or_none()
        if not settings or not getattr(settings, "grok_auto_trade", False):
            return

        min_conf = getattr(settings, "grok_min_confidence", 70)

    # ── Pull SA state ─────────────────────────────────────────────────────────
    sa_risk_posture     = "neutral"
    sa_per_symbol: dict = {}
    sa_event_risk: list = []
    sa_available        = False
    try:
        from situational_awareness import get_last_assessment
        sa = get_last_assessment()
        if sa and sa.ok:
            sa_risk_posture = sa.risk_posture
            sa_per_symbol   = sa.per_symbol or {}
            sa_event_risk   = sa.event_risk or []
            sa_available    = True
    except Exception:
        pass

    if sa_available and sa_risk_posture == "risk_off":
        log.info("[auto_exec] SA risk_posture=risk_off — blocking all new Grok entries this cycle")
        _write_event(
            type="signal", severity="warning",
            title="Grok auto-entries blocked — SA risk_off posture",
            body="Situational Awareness flagged risk_off market conditions. No new positions opened this cycle.",
            meta={"sa_risk_posture": sa_risk_posture, "source": "grok_auto_entries"},
        )
        return

    # ── Fetch latest Grok setups ──────────────────────────────────────────────
    try:
        from grok_trader import get_latest_setups
        setups = get_latest_setups()
    except Exception as e:
        log.warning(f"[auto_exec] Could not fetch Grok setups: {e}")
        return

    if not setups:
        return

    for setup in setups:
        setup_id = setup.get("id", "")
        if setup_id in _grok_setup_ids_executed:
            continue

        symbol    = setup.get("symbol", "")
        side      = setup.get("direction", "").lower()
        confidence = setup.get("confidence", 0)

        # ── SA per-symbol sentiment gate ──────────────────────────────────────
        # If SA has an opinion on this symbol that contradicts the trade direction,
        # require +10 pp extra confidence (the catalyst must be very strong to
        # override the macro/sentiment headwind).
        effective_min_conf = min_conf
        if sa_available and symbol.upper() in {k.upper() for k in sa_per_symbol}:
            sym_sa = next(
                (v for k, v in sa_per_symbol.items() if k.upper() == symbol.upper()), {}
            )
            sa_bias = sym_sa.get("bias", "neutral")
            if (side == "buy" and sa_bias == "bearish") or (side == "sell" and sa_bias == "bullish"):
                effective_min_conf = min(min_conf + 10, 95)
                log.info(
                    f"[auto_exec] {symbol}: SA bias='{sa_bias}' contradicts {side.upper()} — "
                    f"raising conf threshold {min_conf}% → {effective_min_conf}%"
                )

        if confidence < effective_min_conf:
            log.info(
                f"[auto_exec] {symbol}: conf {confidence}% < {effective_min_conf}% threshold — skip"
            )
            continue

        # ── SA event-risk gate ────────────────────────────────────────────────
        # Skip setups where SA flagged a high-impact event within the next 4h
        # that affects the same asset class as this trade.
        asset_class = setup.get("asset_class", "")
        blocked_by_event = False
        for ev in sa_event_risk:
            if ev.get("impact") != "high":
                continue
            affected = [a.lower() for a in ev.get("affected", [])]
            if asset_class.lower() in affected or symbol.lower() in affected or "all" in affected:
                log.info(
                    f"[auto_exec] {symbol}: blocked by SA event_risk '{ev.get('event')}' "
                    f"affecting {affected}"
                )
                blocked_by_event = True
                break
        if blocked_by_event:
            continue

        # ── Mathematical gate — verify via universe screener technical data ─────
        # Grok confidence alone is not enough.  We cross-check the candidate's
        # composite technical score (RVOL, RSI trend, MA position, momentum) from
        # the last universe screener run.  If the screener has no data for this
        # symbol, we proceed but apply a stricter confidence floor.
        try:
            from universe import get_universe_cache
            from autosetup import _quick_score
            _cache      = get_universe_cache()
            _candidates = _cache.get("filter_candidates_raw", [])
            _sym_data   = next(
                (c for c in _candidates if c.get("symbol", "").upper() == symbol.upper()),
                None
            )
            if _sym_data:
                math_score = _quick_score(_sym_data)
                screener_dir = _sym_data.get("direction", "up")

                # Reject if the screener says the stock is technically weak
                if math_score < 20:
                    log.info(
                        f"[auto_exec] {symbol}: SKIPPED — mathematical score "
                        f"{math_score} < 20 (technical quality too low)"
                    )
                    continue

                # Raise confidence bar if screener direction contradicts Grok direction
                if (side == "buy" and screener_dir == "down") or \
                   (side == "sell" and screener_dir == "up"):
                    _old = effective_min_conf
                    effective_min_conf = min(effective_min_conf + 15, 95)
                    log.info(
                        f"[auto_exec] {symbol}: screener direction '{screener_dir}' "
                        f"contradicts {side.upper()} — raising conf "
                        f"{_old}% → {effective_min_conf}%"
                    )

                # Scalping needs higher bar — too much noise at 5Min without quality signal
                trade_type = setup.get("trade_type", "")
                if not trade_type:
                    # Infer from screener if Grok setup doesn't declare it
                    from autosetup import _determine_trade_type
                    trade_type = _determine_trade_type(_sym_data)
                if trade_type == "scalping":
                    effective_min_conf = max(effective_min_conf, min(min_conf + 5, 95))
            else:
                # Symbol not in screener cache — raise bar since we can't mathematically verify
                effective_min_conf = min(effective_min_conf + 10, 95)
                log.info(
                    f"[auto_exec] {symbol}: not in screener cache — "
                    f"raising conf threshold to {effective_min_conf}%"
                )
        except Exception as _math_err:
            log.debug(f"[auto_exec] Mathematical gate error for {symbol}: {_math_err}")

        # Re-check confidence after all adjustments
        if confidence < effective_min_conf:
            log.info(
                f"[auto_exec] {symbol}: conf {confidence}% < {effective_min_conf}% "
                f"(after mathematical gate) — skip"
            )
            continue

        r_r = setup.get("r_r_ratio", 0.0)
        if r_r < 2.0:
            continue

        symbol  = setup.get("symbol", "")
        side    = setup.get("direction", "").lower()
        entry   = setup.get("entry_price")
        stop    = setup.get("stop_price")
        target  = setup.get("target_price")

        if not all([symbol, side in ("buy", "sell"), entry, stop, target]):
            log.warning(f"[auto_exec] Incomplete Grok setup, skipping: {setup}")
            continue

        # No trade without stop — enforce hard rule
        if not stop:
            log.warning(f"[auto_exec] Grok setup {setup_id} has no stop_price — skipping")
            continue

        log.info(f"[auto_exec] Auto-executing Grok setup {setup_id}: "
                 f"{side.upper()} {symbol} entry={entry} sl={stop} tp={target}")

        try:
            await _execute_grok_setup(settings, setup, symbol, side, entry, stop, target)
            _grok_setup_ids_executed.add(setup_id)
        except Exception as e:
            log.error(f"[auto_exec] Failed to execute Grok setup {setup_id}: {e}")


async def _execute_grok_setup(settings, setup: dict, symbol: str, side: str,
                               entry: float, stop: float, target: float):
    loop = asyncio.get_event_loop()

    try:
        broker = create_broker(settings)
    except ValueError as e:
        log.error(f"[auto_exec] No broker for Grok auto-trade: {e}")
        return

    # ── SA position-size multiplier (only active when SA_ENFORCE=true) ────────
    sa_size_multiplier = 1.0
    try:
        from situational_awareness import get_last_assessment, effective_risk_multiplier
        sa = get_last_assessment()
        if sa and sa.ok:
            sa_size_multiplier = effective_risk_multiplier(sa)
    except Exception:
        pass

    # Calculate position size using 2% risk rule, scaled by SA multiplier
    def _do_entry():
        try:
            acct = broker.get_account()
        except Exception:
            return None
        equity = acct.get("equity", acct.get("portfolio_value", 0.0))
        if equity <= 0:
            return None

        risk_capital = equity * 0.02 * sa_size_multiplier  # 2% × SA scaling
        risk_per_unit = abs(entry - stop)
        if risk_per_unit < 1e-8:
            return None

        qty = risk_capital / risk_per_unit

        strategy_label = f"Grok-{setup.get('trade_type', 'day_trading')[:5].upper()}:{setup.get('id','')[:8]}"
        try:
            order = broker.place_market_order(
                symbol, qty, side,
                stop_loss_price=stop,
                take_profit_price=target,
                strategy_name=strategy_label,
            )
        except Exception as e:
            log.error(f"[auto_exec] Entry order failed for {symbol}: {e}")
            return None

        return {"order": order, "qty": qty, "equity": equity}

    result = await loop.run_in_executor(None, _do_entry)
    if not result:
        return

    qty     = result["qty"]
    order   = result.get("order", {})
    equity  = result["equity"]

    # Record the trade in the database
    async with SessionLocal() as db:
        initial_risk = abs(entry - stop) * qty

        # Build structured trade_logic string from Grok's reasoning dict
        raw_logic = setup.get("trade_logic", {})
        logic_parts = []
        if raw_logic.get("why_now"):
            logic_parts.append(f"Why now: {raw_logic['why_now']}")
        if raw_logic.get("technical_confluence"):
            logic_parts.append(f"Technical: {raw_logic['technical_confluence']}")
        if raw_logic.get("catalyst_rationale"):
            logic_parts.append(f"Catalyst: {raw_logic['catalyst_rationale']}")
        if raw_logic.get("expected_timeline"):
            logic_parts.append(f"Timeline: {raw_logic['expected_timeline']}")
        if raw_logic.get("what_invalidates"):
            logic_parts.append(f"Invalidation: {raw_logic['what_invalidates']}")
        trade_logic_str = " | ".join(logic_parts) if logic_parts else setup.get("catalyst", "")

        # Expected profit at target
        expected_profit     = round(abs(target - entry) * qty, 2)
        expected_profit_pct = round(abs(target - entry) / entry * 100, 2) if entry > 0 else 0.0

        trade = Trade(
            strategy_id=None,
            strategy_name=f"Grok-{setup.get('trade_type','day')[:5].upper()}:{setup.get('id','')[:8]}",
            symbol=symbol,
            side=TradeSide.buy if side == "buy" else TradeSide.sell,
            qty=qty,
            entry_price=entry,
            stop_loss_price=stop,
            take_profit_price=target,
            initial_risk=round(initial_risk, 2),
            pre_trade_probability=setup.get("pre_trade_probability"),
            setup_score=int(setup.get("confidence", 0)),
            session_date=datetime.utcnow().strftime("%Y-%m-%d"),
            notes=(
                f"Grok auto-trade. Catalyst: {setup.get('catalyst','')[:200]}"
                + (f" | SA: {sa_size_multiplier:.2f}× sizing" if sa_size_multiplier < 1.0 else "")
            ),
            alpaca_order_id=str(order.get("order_id", "")) if order else None,
            # New enriched fields
            trade_type=setup.get("trade_type", "day_trading"),
            trade_logic=trade_logic_str[:2000],
            expected_profit=expected_profit,
            expected_profit_pct=expected_profit_pct,
            catalyst=setup.get("catalyst", "")[:500],
            catalyst_source=setup.get("catalyst_source", "")[:500],
        )
        db.add(trade)
        await db.commit()
        await db.refresh(trade)

    _write_event(
        type="trade_open",
        severity="info",
        title=f"Grok auto-trade: {side.upper()} {symbol}",
        body=(f"Entry: ${entry} | SL: ${stop} | TP: ${target} | "
              f"R:R: {setup.get('r_r_ratio',0):.1f} | Conf: {setup.get('confidence',0)}%"),
        symbol=symbol,
        meta={"setup_id": setup.get("id"), "trade_id": trade.id, "source": "grok_auto"},
    )


# ─── Grok quick-gate (non-streaming, fast decision check) ─────────────────────

async def grok_quick_gate(symbol: str, signal_direction: str,
                           entry: float, stop: float) -> dict:
    """
    Ask Grok a single yes/no: is this entry worth taking right now?
    Used by the strategy runner to get Grok approval before executing
    a new position when grok_auto_trade is enabled.

    Returns: {"approve": bool, "reason": str, "confidence": int}
    """
    import os, httpx

    api_key = os.getenv("XAI_API_KEY", "")
    model   = os.getenv("GROK_MODEL", "grok-4")
    if not api_key:
        return {"approve": True, "reason": "No XAI key — gate bypassed", "confidence": 0}

    r_r = (abs(entry - stop) * 2) / abs(entry - stop) if abs(entry - stop) > 0 else 0

    prompt = (
        f"Quick trade gate check. Symbol: {symbol}. "
        f"Signal: {signal_direction.upper()}. Entry: ${entry:.4f}. Stop: ${stop:.4f}. "
        f"R:R approx 2:1. Is there any breaking news, macro catalyst, or "
        f"technical reason RIGHT NOW that would make this trade HIGH risk or invalid? "
        f"Answer in JSON: {{\"approve\": true/false, \"reason\": \"...\", \"confidence\": 0-100}}"
    )

    payload = {
        "model": model,
        "input": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search"}],
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.x.ai/v1/responses",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        # Extract text from output
        text = ""
        for item in data.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text = c.get("text", "")
                    break

        import json, re
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        result = json.loads(text)
        return {
            "approve": bool(result.get("approve", True)),
            "reason":  str(result.get("reason", "")),
            "confidence": int(result.get("confidence", 50)),
        }
    except Exception as e:
        log.warning(f"[auto_exec] Grok quick-gate failed for {symbol}: {e}")
        return {"approve": True, "reason": f"gate error: {e}", "confidence": 0}
