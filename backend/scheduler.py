"""
Automation engine — runs active strategies on their configured timeframe,
enforces stop-loss and take-profit on every tick, and records P&L.
"""
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, func
from database import SessionLocal
from models import Strategy, StrategyStatus, Trade, TradeStatus, TradeSide, BotSettings

log = logging.getLogger("scheduler")

# Lazy import to avoid circular deps at module load time
def _write_event_sync(type, title, severity="info", body=None, symbol=None, pnl=None, meta=None):
    """Fire-and-forget event write from synchronous context (spawns a task)."""
    import asyncio
    from routers.events import write_event
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(write_event(type=type, title=title, severity=severity,
                                              body=body, symbol=symbol, pnl=pnl, meta=meta))
    except Exception:
        pass

INTERVAL_MAP = {
    "1Min":  1,
    "5Min":  5,
    "15Min": 15,
    "1Hour": 60,
    "1Day":  1440,
}

scheduler = AsyncIOScheduler(timezone="UTC")

_last_run: dict[int, datetime] = {}
_last_signal: dict[str, tuple[str, datetime]] = {}


def get_scheduler() -> AsyncIOScheduler:
    return scheduler


def _close_trade(trade: Trade, current_price: float, reason: str, strategy: Strategy):
    """Mark trade closed, calculate P&L, update strategy totals."""
    trade.exit_price  = current_price
    trade.exit_reason = reason
    trade.closed_at   = datetime.utcnow()
    trade.status      = TradeStatus.closed

    if trade.entry_price:
        multiplier = 1 if trade.side == TradeSide.buy else -1
        trade.pnl     = round(multiplier * (current_price - trade.entry_price) * trade.qty, 2)
        trade.pnl_pct = round(multiplier * (current_price - trade.entry_price) / trade.entry_price * 100, 2)
        if trade.initial_risk and trade.initial_risk > 0:
            trade.r_multiple = round(trade.pnl / trade.initial_risk, 2)

    strategy.total_pnl = round((strategy.total_pnl or 0) + (trade.pnl or 0), 2)
    if trade.pnl and trade.pnl > 0:
        strategy.winning_trades = (strategy.winning_trades or 0) + 1

    log.info(
        f"[{strategy.name}] CLOSED trade #{trade.id} via {reason} "
        f"@ ${current_price} | P&L: ${trade.pnl} ({trade.pnl_pct}%)"
    )
    reason_label = {"stop_loss": "stop-loss hit", "take_profit": "take-profit hit",
                    "signal": "signal exit", "end_of_data": "end of data"}.get(reason, reason)
    pnl_val = trade.pnl or 0.0
    _write_event_sync(
        type="trade_close",
        severity="success" if pnl_val >= 0 else "error",
        title=f"{trade.symbol} closed — {reason_label}",
        body=f"P&L: {'+' if pnl_val >= 0 else ''}${pnl_val:.2f} ({trade.pnl_pct or 0:+.2f}%) · Strategy: {strategy.name}",
        symbol=trade.symbol,
        pnl=pnl_val,
        meta={"trade_id": trade.id, "exit_reason": reason, "strategy": strategy.name},
    )


async def run_strategy(strategy_id: int):
    """
    Core scheduled job.
    On every tick:
      1. Check all open trades for this strategy — trigger SL or TP if hit.
      2. Generate a fresh signal.
      3. If BUY and no open trade → place order, record trade with SL/TP prices.
      4. If SELL and open long trade → close via signal exit.
    """
    async with SessionLocal() as db:
        strat_result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
        strategy = strat_result.scalar_one_or_none()
        if not strategy or strategy.status != StrategyStatus.active:
            return

        settings_result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = settings_result.scalar_one_or_none()
        if not settings or (not settings.alpaca_api_key and not settings.binance_api_key):
            log.warning(f"[{strategy.name}] No API keys — skipping")
            return

        from broker import create_broker, create_alpaca_broker
        from broker.binance import BinanceBroker
        from broker.alpaca import AlpacaBroker
        from strategies import get_strategy as _get_strat

        try:
            sym = strategy.symbol
            _su = sym.upper()
            is_crypto    = "/" in sym or _su.endswith(("-USD", "USDT"))
            is_uk_stock  = _su.endswith(".L")
            is_eu_stock  = any(_su.endswith(sfx) for sfx in (
                ".DE", ".PA", ".AS", ".MI", ".MC", ".ST",
                ".SW", ".CO", ".HE", ".OL", ".LS", ".BR",
            )) and not is_uk_stock
            is_commodity = _su.endswith("=F")
            is_forex     = _su.endswith("=X")

            # ── Market session gate ───────────────────────────────────────────
            # Skip signal generation (not SL/TP checks) outside trading hours.
            from datetime import timezone
            import pytz as _pytz
            _now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
            _wd = _now_utc.weekday()  # 0=Mon … 6=Sun

            def _in_session(tz_name, open_h, open_m, close_h, close_m):
                _tz = _pytz.timezone(tz_name)
                _local = _now_utc.astimezone(_tz)
                _mins = _local.hour * 60 + _local.minute
                return 0 <= _wd <= 4 and (open_h * 60 + open_m) <= _mins < (close_h * 60 + close_m)

            if is_uk_stock and not _in_session("Europe/London", 8, 0, 16, 35):
                log.debug(f"[{strategy.name}] LSE closed — skipping tick")
                return
            if is_eu_stock and not _in_session("Europe/Paris", 9, 0, 17, 30):
                log.debug(f"[{strategy.name}] European market closed — skipping tick")
                return
            if not is_crypto and not is_uk_stock and not is_eu_stock \
                    and not is_commodity and not is_forex \
                    and not _in_session("America/New_York", 9, 30, 16, 0):
                log.debug(f"[{strategy.name}] US market closed (NYSE/NASDAQ) — skipping tick")
                return
            if is_commodity and not _in_session("America/New_York", 9, 0, 17, 30):
                log.debug(f"[{strategy.name}] CME closed — skipping tick")
                return
            # Crypto and Forex run 24/5; block only on weekends
            if (is_crypto or is_forex) and _wd >= 5:
                log.debug(f"[{strategy.name}] Weekend — skipping tick")
                return

            primary = create_broker(settings)

            if is_crypto and isinstance(primary, AlpacaBroker) and settings.binance_api_key:
                broker = BinanceBroker(settings.binance_api_key, settings.binance_secret_key,
                                       testnet=settings.binance_testnet)
            elif not is_crypto and isinstance(primary, BinanceBroker) and settings.alpaca_api_key:
                broker = create_alpaca_broker(settings)
            else:
                broker = primary

            timeframe = strategy.parameters.get("timeframe", "1Day")

            # ── Sub-session window gate ───────────────────────────────────────
            # Scalping (5Min/1Min): run full session per market.
            #   vol_ratio ≥ 1.3 in the strategy itself filters out low-quality
            #   midday setups — no need for a narrow sub-session gate on top.
            # Day trading (15Min): avoid midday chop per market.
            # 1Hour / 1Day: full session, no extra gate.
            if timeframe in ("1Min", "5Min"):
                if is_forex:
                    # Forex: full London + NY session
                    in_window = _in_session("UTC", 7, 0, 17, 0)
                elif is_crypto:
                    # Crypto: 24/5 — already weekend-gated above
                    in_window = True
                elif is_eu_stock:
                    # EU stocks: full Euronext/XETRA session
                    in_window = _in_session("Europe/Paris", 9, 0, 17, 30)
                elif is_uk_stock:
                    # UK stocks: full LSE session
                    in_window = _in_session("Europe/London", 8, 0, 16, 35)
                elif is_commodity:
                    # Commodities: full CME session
                    in_window = _in_session("America/New_York", 9, 0, 17, 30)
                else:
                    # US stocks: full NYSE/NASDAQ session
                    in_window = _in_session("America/New_York", 9, 30, 16, 0)
                if not in_window:
                    log.debug(f"[{strategy.name}] Outside scalping window — skipping tick")
                    return

            elif timeframe == "15Min":
                if is_forex:
                    in_window = _in_session("UTC", 7, 0, 17, 0)
                elif is_eu_stock:
                    in_window = _in_session("Europe/Paris", 9, 0, 17, 30)
                elif is_uk_stock:
                    in_window = _in_session("Europe/London", 8, 0, 16, 35)
                elif is_commodity:
                    in_window = _in_session("America/New_York", 9, 0, 17, 30)
                else:
                    in_window = _in_session("America/New_York", 9, 30, 16, 0)
                if not in_window:
                    log.debug(f"[{strategy.name}] Outside day-trading window — skipping tick")
                    return

            # ── Fetch current price + bars ────────────────────────────────────
            if is_uk_stock or is_eu_stock or is_commodity or is_forex:
                # yfinance-only symbols — fetch bars to get current price
                import yfinance as yf
                import pandas as pd
                yf_sym = sym.upper().replace("/", "-")
                raw = yf.Ticker(yf_sym).history(period="6mo", interval="1d", auto_adjust=True)
                if raw is None or raw.empty:
                    log.warning(f"[{strategy.name}] No yfinance data for {sym}")
                    return
                raw = raw.reset_index()
                raw.columns = [str(c).lower() for c in raw.columns]
                for alias in ("date", "index", "datetime"):
                    if alias in raw.columns:
                        raw = raw.rename(columns={alias: "datetime"})
                        break
                df = raw[["datetime", "open", "high", "low", "close", "volume"]].tail(200).reset_index(drop=True)
                current_price = float(df["close"].iloc[-1])
            else:
                df = broker.get_bars(sym, timeframe=timeframe, limit=200)
                if df is None or df.empty or len(df) < 20:
                    log.warning(f"[{strategy.name}] Not enough bars")
                    return
                current_price = float(df["close"].iloc[-1])

            # ── Step 1: Check open trades for SL / TP ────────────────────────
            open_result = await db.execute(
                select(Trade).where(Trade.status == TradeStatus.open,
                                    Trade.strategy_id == strategy.id)
            )
            open_trades = open_result.scalars().all()

            closed_by_risk = False
            _interval_mins = INTERVAL_MAP.get(timeframe, 60)
            for trade in open_trades:
                sl = trade.stop_loss_price
                tp = trade.take_profit_price

                # ── Time-stop (scalping + day trading only) ───────────────────
                # If a scalp hasn't moved in 6 bars (30 min) or a day trade in
                # 8 bars (2 hr), the thesis has failed — scratch it at current price.
                _time_stop_bars = None
                if timeframe in ("1Min", "5Min"):
                    _time_stop_bars = 6   # 6 × 5Min = 30 min
                elif timeframe == "15Min":
                    _time_stop_bars = 8   # 8 × 15Min = 2 hr

                if _time_stop_bars and trade.opened_at:
                    _elapsed = (datetime.utcnow() - trade.opened_at).total_seconds()
                    _bars_held = int(_elapsed / (_interval_mins * 60))
                    if _bars_held >= _time_stop_bars:
                        log.info(
                            f"[{strategy.name}] TIME-STOP: trade #{trade.id} held "
                            f"{_bars_held} bars (limit {_time_stop_bars}) — scratching"
                        )
                        if not (is_uk_stock or is_commodity):
                            close_side = "sell" if trade.side == TradeSide.buy else "buy"
                            try:
                                broker.place_market_order(sym, trade.qty, close_side)
                            except Exception as e:
                                log.error(f"[{strategy.name}] Time-stop order failed: {e}")
                        _close_trade(trade, current_price, "time_stop", strategy)
                        _write_event_sync(
                            type="trade_close", severity="warning",
                            title=f"{sym} time-stop — thesis stalled",
                            body=f"No move in {_bars_held} {timeframe} bars — scratched at ${current_price:.4f}",
                            symbol=sym,
                        )
                        closed_by_risk = True
                        continue

                if trade.side == TradeSide.buy:
                    if sl and current_price <= sl:
                        log.warning(f"[{strategy.name}] STOP LOSS hit for trade #{trade.id} — "
                                    f"price {current_price} <= SL {sl}")
                        if not (is_uk_stock or is_commodity):
                            try:
                                broker.place_market_order(sym, trade.qty, "sell")
                            except Exception as e:
                                log.error(f"[{strategy.name}] SL sell order failed: {e}")
                        _close_trade(trade, current_price, "stop_loss", strategy)
                        closed_by_risk = True

                    elif tp and current_price >= tp:
                        log.info(f"[{strategy.name}] TAKE PROFIT hit for trade #{trade.id} — "
                                 f"price {current_price} >= TP {tp}")
                        if not (is_uk_stock or is_commodity):
                            try:
                                broker.place_market_order(sym, trade.qty, "sell")
                            except Exception as e:
                                log.error(f"[{strategy.name}] TP sell order failed: {e}")
                        _close_trade(trade, current_price, "take_profit", strategy)
                        closed_by_risk = True

                elif trade.side == TradeSide.sell:
                    # For short trades: SL is above entry, TP is below entry
                    if sl and current_price >= sl:
                        if not (is_uk_stock or is_commodity):
                            try:
                                broker.place_market_order(sym, trade.qty, "buy")
                            except Exception as e:
                                log.error(f"[{strategy.name}] SL buy-back order failed: {e}")
                        _close_trade(trade, current_price, "stop_loss", strategy)
                        closed_by_risk = True
                    elif tp and current_price <= tp:
                        if not (is_uk_stock or is_commodity):
                            try:
                                broker.place_market_order(sym, trade.qty, "buy")
                            except Exception as e:
                                log.error(f"[{strategy.name}] TP buy-back order failed: {e}")
                        _close_trade(trade, current_price, "take_profit", strategy)
                        closed_by_risk = True

            if closed_by_risk:
                await db.commit()

            # ── Step 2: Generate fresh signal ────────────────────────────────
            strat_obj = _get_strat(strategy.template, strategy.parameters,
                                   strategy.risk_config, sym)
            signal = strat_obj.generate_signal(df)

            log.info(f"[{strategy.name}] {sym} → {signal.action.upper()} "
                     f"(conf: {signal.confidence:.2f}) price: ${current_price}")

            if signal.action == "hold":
                return

            # Cooldown guard
            symbol_key = f"{strategy.id}:{sym}"
            if symbol_key in _last_signal:
                last_action, last_time = _last_signal[symbol_key]
                interval_mins = INTERVAL_MAP.get(timeframe, 60)
                if last_action == signal.action and \
                   datetime.utcnow() - last_time < timedelta(minutes=interval_mins * 2):
                    log.info(f"[{strategy.name}] Cooldown — skipping duplicate {signal.action}")
                    return

            # Reload open trades after potential SL/TP closes above
            open_result2 = await db.execute(
                select(Trade).where(Trade.status == TradeStatus.open,
                                    Trade.strategy_id == strategy.id)
            )
            open_trades = open_result2.scalars().all()

            # ── Step 3: Close long on SELL signal ────────────────────────────
            if signal.action == "sell" and open_trades:
                for ot in open_trades:
                    if ot.side == TradeSide.buy:
                        if not (is_uk_stock or is_commodity):
                            try:
                                broker.place_market_order(sym, ot.qty, "sell")
                            except Exception as e:
                                log.error(f"[{strategy.name}] Signal-close sell failed: {e}")
                        _close_trade(ot, current_price, "signal", strategy)
                await db.commit()
                return

            # ── Step 4: Open BUY trade ────────────────────────────────────────
            if signal.action == "buy":
                # ── Pre-trade confidence gate ─────────────────────────────────
                # Minimum signal quality threshold before placing any live order.
                # Prevents weak signals (< 50% confidence) from firing automatically.
                MIN_CONFIDENCE = 0.50
                if signal.confidence < MIN_CONFIDENCE:
                    log.info(
                        f"[{strategy.name}] BUY signal rejected — confidence "
                        f"{signal.confidence:.2f} < {MIN_CONFIDENCE} threshold"
                    )
                    _write_event_sync(
                        type="signal", severity="info",
                        title=f"BUY signal filtered out: {sym} (conf {signal.confidence:.0%})",
                        body=(f"Signal confidence {signal.confidence:.0%} below {MIN_CONFIDENCE:.0%} "
                              f"minimum gate. No order placed. Strategy: {strategy.name}"),
                        symbol=sym,
                        meta={"confidence": round(signal.confidence, 3), "gate": MIN_CONFIDENCE,
                              "strategy": strategy.name, "reason": "below_confidence_gate"},
                    )
                    return

                # Guard: must have a valid price to compute SL
                if not current_price or current_price <= 0:
                    log.warning(f"[{strategy.name}] Invalid price {current_price} — cannot set SL, skipping")
                    return

                # Max open trades guard
                total_open_result = await db.execute(
                    select(Trade).where(Trade.status == TradeStatus.open)
                )
                total_open = len(total_open_result.scalars().all())
                max_open = settings.max_open_trades or 10
                if total_open >= max_open:
                    log.info(f"[{strategy.name}] Max open trades ({max_open}) reached — skip")
                    return

                # Daily trade limit guard
                today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                daily_result = await db.execute(
                    select(func.count(Trade.id)).where(Trade.opened_at >= today_start)
                )
                daily_count = daily_result.scalar() or 0
                max_daily = settings.max_daily_trades or 20
                if daily_count >= max_daily:
                    log.warning(
                        f"[{strategy.name}] Daily trade limit ({max_daily}) reached "
                        f"({daily_count} trades placed today) — skip"
                    )
                    _write_event_sync(
                        type="risk_alert", severity="warning",
                        title=f"Daily trade limit reached ({max_daily}/day)",
                        body=f"{strategy.name} tried to open {sym} but {daily_count} trades already placed today. Resets UTC midnight.",
                        symbol=sym,
                        meta={"daily_count": daily_count, "limit": max_daily},
                    )
                    return

                # Daily loss limit guard (P&L-based, separate from trade count)
                daily_pnl_res = await db.execute(
                    select(func.sum(Trade.pnl)).where(
                        Trade.status == TradeStatus.closed,
                        Trade.closed_at >= today_start,
                    )
                )
                daily_realized = daily_pnl_res.scalar() or 0.0
                daily_loss_limit_pct = getattr(settings, "daily_loss_limit_pct", None) or 3.0
                daily_loss_limit_amt = 100_000 * (daily_loss_limit_pct / 100)
                if daily_realized <= -daily_loss_limit_amt:
                    log.warning(
                        f"[{strategy.name}] Daily loss limit hit: ${daily_realized:.2f} "
                        f"(limit: -${daily_loss_limit_amt:.0f}) — pausing all strategies"
                    )
                    from sqlalchemy import update
                    await db.execute(
                        update(Strategy).where(Strategy.status == StrategyStatus.active)
                        .values(status=StrategyStatus.paused)
                    )
                    await db.commit()
                    _write_event_sync(
                        type="risk_alert", severity="error",
                        title=f"Daily loss limit hit — all strategies paused",
                        body=f"Today's realized P&L: ${daily_realized:.2f} exceeded -{daily_loss_limit_pct}% limit (${daily_loss_limit_amt:.0f}). Resets UTC midnight.",
                        meta={"daily_pnl": daily_realized, "limit_pct": daily_loss_limit_pct},
                    )
                    return

                # Don't double-enter if already long
                if any(t.side == TradeSide.buy for t in open_trades):
                    log.info(f"[{strategy.name}] Already long {sym} — skip")
                    return

                sl_pct = strategy.risk_config.get("stop_loss_pct", 1.5)
                tp_pct = strategy.risk_config.get("take_profit_pct", 3.0)

                # Enforce minimum SL — never trade without one
                if not sl_pct or sl_pct <= 0:
                    sl_pct = 1.5
                    log.warning(f"[{strategy.name}] No SL configured — defaulting to 1.5%")

                sl_price_val = round(current_price * (1 - sl_pct / 100), 6)
                tp_price_val = round(current_price * (1 + tp_pct / 100), 6)

                if is_uk_stock or is_commodity or is_forex:
                    # Alpaca doesn't support UK stocks / commodities / forex directly.
                    # Record as a tracked signal trade — SL/TP are monitored by auto_exec.
                    initial_risk = abs(current_price - sl_price_val) * 1  # qty=1 unit for signals
                    trade = Trade(
                        strategy_id=strategy.id,
                        strategy_name=strategy.name,
                        symbol=sym,
                        side=TradeSide.buy,
                        qty=1,
                        entry_price=current_price,
                        stop_loss_price=sl_price_val,
                        take_profit_price=tp_price_val,
                        initial_risk=round(initial_risk, 6),
                        status=TradeStatus.open,
                        session_date=datetime.utcnow().strftime("%Y-%m-%d"),
                        notes=f"Signal trade (paper tracked) | conf:{signal.confidence:.2f}",
                    )
                    db.add(trade)
                    strategy.total_trades += 1
                    _last_signal[symbol_key] = (signal.action, datetime.utcnow())
                    await db.commit()
                    _write_event_sync(
                        type="trade_open", severity="info",
                        title=f"BUY signal: {sym} @ {current_price:.4f}",
                        body=f"SL: {sl_price_val:.4f} | TP: {tp_price_val:.4f} | Strategy: {strategy.name}",
                        symbol=sym,
                        meta={"strategy": strategy.name, "confidence": signal.confidence},
                    )
                    log.info(f"[{strategy.name}] SIGNAL TRADE BUY {sym} @ {current_price:.4f} (paper tracked)")
                    return

                account = broker.get_account()
                equity = float(account.get("equity", 100_000))

                # Risk-derived position sizing (Tharp / Elder 2% rule):
                # risk_capital = equity × risk_per_trade_pct
                # qty = risk_capital / stop_distance_per_share
                risk_pct = strategy.risk_config.get(
                    "risk_per_trade_pct",
                    getattr(settings, "risk_per_trade_pct", None) or 1.0,
                )
                stop_distance = current_price - sl_price_val  # $ per share
                if stop_distance <= 0:
                    log.warning(f"[{strategy.name}] Invalid stop distance — skipping")
                    return
                risk_capital = equity * (risk_pct / 100)
                qty = round(risk_capital / stop_distance, 6)
                # Floor to minimum tradeable unit
                from broker.alpaca import AlpacaBroker
                if isinstance(broker, AlpacaBroker):
                    qty = max(1.0, round(qty))  # whole shares for equities
                else:
                    qty = max(0.000001, qty)    # fractional for crypto

                if qty <= 0:
                    log.warning(f"[{strategy.name}] Qty too small — skipping")
                    return

                initial_risk_amt = round(stop_distance * qty, 2)

                sl_price_order = round(sl_price_val, 2)
                tp_price_order = round(tp_price_val, 2)
                order = broker.place_market_order(sym, qty, "buy",
                                                  stop_loss_price=sl_price_order,
                                                  take_profit_price=tp_price_order,
                                                  strategy_name=strategy.name)

                trade = Trade(
                    strategy_id=strategy.id,
                    strategy_name=strategy.name,
                    symbol=sym,
                    side=TradeSide.buy,
                    qty=qty,
                    entry_price=current_price,
                    stop_loss_price=sl_price_val,
                    take_profit_price=tp_price_val,
                    initial_risk=initial_risk_amt,
                    status=TradeStatus.open,
                    alpaca_order_id=order.get("order_id"),
                    notes=f"Auto | conf:{signal.confidence:.2f} | {timeframe} | risk:{risk_pct}% SL:{sl_pct}% TP:{tp_pct}%",
                )
                db.add(trade)
                strategy.total_trades += 1
                _last_signal[symbol_key] = (signal.action, datetime.utcnow())
                await db.commit()
                log.info(
                    f"[{strategy.name}] ✅ BUY {qty} {sym} @ ${current_price} | "
                    f"SL: ${trade.stop_loss_price} | TP: ${trade.take_profit_price} | "
                    f"order: {order.get('order_id')}"
                )
                _write_event_sync(
                    type="trade_open", severity="info",
                    title=f"BUY {sym} @ ${current_price}",
                    body=f"Qty: {qty} · SL: ${trade.stop_loss_price} · TP: ${trade.take_profit_price} · {strategy.name} (conf: {signal.confidence:.2f})",
                    symbol=sym,
                    meta={"trade_id": trade.id, "strategy": strategy.name, "qty": qty,
                          "confidence": round(signal.confidence, 3)},
                )

        except Exception as e:
            log.error(f"[{strategy.name}] Error: {e}", exc_info=True)


async def reload_jobs(force_immediate: bool = False):
    """
    Sync scheduler jobs to match active strategies in DB.

    force_immediate=True (triggered by the Sync button):
      Removes and re-adds ALL strategy jobs so they fire their first tick in 10 s.
      Use this after a manual rescan or after adding strategies outside market hours.

    force_immediate=False (startup / auto-run):
      Only adds jobs for strategies not yet scheduled. First tick in 30 s.
    """
    async with SessionLocal() as db:
        result = await db.execute(select(Strategy).where(Strategy.status == StrategyStatus.active))
        active = result.scalars().all()

    active_ids = {s.id for s in active}

    # Remove jobs for strategies that are no longer active
    for job in scheduler.get_jobs():
        if not job.id.startswith("strategy_"):
            continue
        try:
            job_id = int(job.id.replace("strategy_", ""))
        except ValueError:
            continue
        if job_id not in active_ids:
            job.remove()
            log.info(f"Removed job for strategy {job_id}")

    # When force_immediate: remove all existing strategy jobs so they get
    # re-added below with a near-term start_date (fire in 10 s).
    if force_immediate:
        for job in scheduler.get_jobs():
            if job.id.startswith("strategy_"):
                job.remove()
        log.info("force_immediate=True — cleared all strategy jobs, re-adding with 10s first tick")

    existing_ids = {
        int(j.id.replace("strategy_", ""))
        for j in scheduler.get_jobs()
        if j.id.startswith("strategy_") and j.id.replace("strategy_", "").isdigit()
    }

    from datetime import timezone as _tz
    from apscheduler.triggers.cron import CronTrigger as _CronTrigger

    for strategy in active:
        timeframe = strategy.parameters.get("timeframe", "1Day")
        interval_mins = INTERVAL_MAP.get(timeframe, 60)
        job_id = f"strategy_{strategy.id}"

        sym = strategy.symbol
        is_uk = sym.upper().endswith(".L")

        if strategy.id not in existing_ids:
            delay_s = 10 if force_immediate else 30

            if timeframe == "1Day":
                # Daily strategies run on a cron at market close so they always
                # see a complete daily bar.  UK stocks → 16:35 UTC (LSE close).
                # US stocks / crypto / commodities → 21:05 UTC (NYSE close + 5 min buffer).
                if is_uk:
                    trigger = _CronTrigger(
                        day_of_week="mon-fri", hour=16, minute=35, timezone="UTC"
                    )
                    label = "16:35 UTC (LSE close)"
                else:
                    trigger = _CronTrigger(
                        day_of_week="mon-fri", hour=21, minute=5, timezone="UTC"
                    )
                    label = "21:05 UTC (NYSE close)"

                # If force_immediate also add a one-shot job firing in delay_s
                # so the user gets a signal check NOW without waiting until close
                if force_immediate:
                    from apscheduler.triggers.date import DateTrigger as _DateTrigger
                    _first_run = datetime.now(_tz.utc) + timedelta(seconds=delay_s)
                    scheduler.add_job(
                        run_strategy,
                        trigger=_DateTrigger(run_date=_first_run),
                        args=[strategy.id],
                        id=f"{job_id}_immediate",
                        name=f"{strategy.name} ({strategy.symbol}) · immediate",
                        max_instances=1,
                        coalesce=True,
                        replace_existing=True,
                    )

                scheduler.add_job(
                    run_strategy,
                    trigger=trigger,
                    args=[strategy.id],
                    id=job_id,
                    name=f"{strategy.name} ({strategy.symbol}) · {timeframe}",
                    max_instances=1,
                    coalesce=True,
                    replace_existing=True,
                )
                log.info(f"Scheduled [{strategy.name}] daily at {label}")

            else:
                # Intraday strategies: interval trigger with near-term first tick
                _first_run = datetime.now(_tz.utc) + timedelta(seconds=delay_s)
                scheduler.add_job(
                    run_strategy,
                    trigger=IntervalTrigger(minutes=interval_mins, start_date=_first_run),
                    args=[strategy.id],
                    id=job_id,
                    name=f"{strategy.name} ({strategy.symbol}) · {timeframe}",
                    max_instances=1,
                    coalesce=True,
                    replace_existing=True,
                )
                log.info(f"Scheduled [{strategy.name}] every {interval_mins}min (first tick in {delay_s}s)")


def get_scheduler_status() -> dict:
    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": next_run.isoformat() if next_run else None,
            "next_run_in_seconds": max(0, int((next_run - datetime.now(next_run.tzinfo)).total_seconds())) if next_run else None,
        })
    return {
        "running": scheduler.running,
        "job_count": len(jobs),
        "jobs": jobs,
    }


async def check_drawdown_kill_switch():
    """
    Runs every 5 minutes.
    Fetches live account equity from the primary broker and compares it to the
    paper-trading starting equity ($100,000 for Alpaca / initial Binance balance).
    If drawdown >= settings.max_drawdown_pct, all active strategies are paused,
    all scheduler jobs are removed, and a CRITICAL risk_alert event is emitted.
    """
    async with SessionLocal() as db:
        settings_res = await db.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = settings_res.scalar_one_or_none()
        if not settings or (not settings.alpaca_api_key and not settings.binance_api_key):
            return

        max_dd = settings.max_drawdown_pct or 10.0

        try:
            from broker import create_broker, create_alpaca_broker
            broker = create_alpaca_broker(settings) if settings.alpaca_api_key else create_broker(settings)
            account = broker.get_account()
            current_equity = float(account.get("equity", 100_000))
        except Exception as e:
            log.warning(f"[kill-switch] Could not fetch account equity: {e}")
            return

        start_equity = 100_000.0
        dd_pct = max(0.0, (start_equity - current_equity) / start_equity * 100)

        if dd_pct < max_dd:
            return  # all good

        # ── KILL SWITCH TRIGGERED ─────────────────────────────────────────────
        log.error(f"🚨 DRAWDOWN KILL SWITCH: {dd_pct:.2f}% >= {max_dd}% — halting ALL strategies")

        # Pause every active strategy
        strats_res = await db.execute(
            select(Strategy).where(Strategy.status == StrategyStatus.active)
        )
        paused = strats_res.scalars().all()
        for s in paused:
            s.status = StrategyStatus.paused
        await db.commit()

        # Remove all scheduler jobs to stop any in-flight ticks
        for job in scheduler.get_jobs():
            if job.id.startswith("strategy_"):
                job.remove()

        from routers.events import write_event
        await write_event(
            type="risk_alert", severity="error",
            title=f"🚨 DRAWDOWN KILL SWITCH — {dd_pct:.1f}% drawdown exceeded {max_dd}% limit",
            body=(f"Portfolio equity dropped to ${current_equity:,.2f} from ${start_equity:,.2f}. "
                  f"{len(paused)} strategies paused. Go to Strategies page to resume after review."),
            meta={"drawdown_pct": round(dd_pct, 2), "max_dd_pct": max_dd,
                  "equity": current_equity, "strategies_paused": len(paused)},
        )


async def _build_sa_context() -> dict:
    """Build the context dict for Grok situational-awareness from live DB state."""
    async with SessionLocal() as db:
        settings_res = await db.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = settings_res.scalar_one_or_none()
        watchlist = []
        if settings and settings.watchlist_symbols:
            watchlist = [s.strip() for s in settings.watchlist_symbols.split(",") if s.strip()]

        # Fetch open trades for position context
        open_res = await db.execute(
            select(Trade).where(Trade.status == TradeStatus.open)
        )
        open_trades = open_res.scalars().all()
        positions = [
            {"symbol": t.symbol, "side": t.side, "entry": t.entry_price, "sl": t.stop_loss_price}
            for t in open_trades
        ]

        # Last 5 signals from event log
        from models import EventLog
        sig_res = await db.execute(
            select(EventLog)
            .where(EventLog.type == "trade_open")
            .order_by(EventLog.created_at.desc())
            .limit(5)
        )
        recent_signals = [
            {"symbol": e.symbol, "title": e.title}
            for e in sig_res.scalars().all()
        ]

    return {
        "asof_utc": datetime.utcnow().isoformat() + "Z",
        "asset_classes": ["us_stocks", "uk_stocks", "crypto", "commodities"],
        "watchlist": watchlist,
        "open_positions": positions,
        "recent_signals": recent_signals,
    }


async def run_situational_awareness_job():
    """
    Runs every 20 minutes. Calls Grok with live web search to assess market
    conditions, then writes a system event with the assessment.
    Advisory only by default (SA_ENFORCE=false) — never changes position size
    or stops without explicit opt-in.
    """
    from situational_awareness import assess_situation, effective_risk_multiplier, HEALTH
    from routers.events import write_event

    if not __import__("os").getenv("XAI_API_KEY"):
        return  # key not configured — skip silently

    try:
        context = await _build_sa_context()
        assessment = await assess_situation(context)

        severity = (
            "error"   if assessment.risk_posture == "risk_off" else
            "warning" if assessment.risk_posture == "neutral" and assessment.confidence > 0.5 else
            "info"
        )
        mult = effective_risk_multiplier(assessment)
        enforce_note = f" | enforcing ×{mult:.2f}" if mult < 1.0 else " | advisory only"

        await write_event(
            type="system",
            severity=severity,
            title=f"Grok SA: {assessment.market_regime} / {assessment.risk_posture}{enforce_note}",
            body=(
                f"{assessment.macro_summary[:200]}"
                + (f" | Confidence: {assessment.confidence:.0%}" if assessment.confidence else "")
                + (f" | Events: {', '.join(e['event'] for e in assessment.event_risk[:3])}"
                   if assessment.event_risk else "")
            ),
            meta={
                "regime": assessment.market_regime,
                "risk_posture": assessment.risk_posture,
                "risk_multiplier_raw": assessment.recommended_risk_multiplier,
                "risk_multiplier_effective": mult,
                "confidence": assessment.confidence,
                "sources_count": len(assessment.sources),
                "ok": assessment.ok,
            },
        )
        log.info(
            f"[SA] regime={assessment.market_regime} posture={assessment.risk_posture} "
            f"mult_raw={assessment.recommended_risk_multiplier} effective={mult} "
            f"conf={assessment.confidence:.2f} latency={assessment.latency_ms}ms"
        )
    except Exception as e:
        log.warning(f"[SA] Situational awareness job failed: {e}")


_EU_STOCK_SUFFIXES = (
    ".DE", ".PA", ".AS", ".MI", ".MC", ".ST",
    ".SW", ".CO", ".HE", ".OL", ".LS", ".BR",
)


async def eod_flatten(market: str = "us"):
    """
    EOD flatten — close all open scalping (5Min) and day-trading (15Min) positions
    before market close so intraday trades never carry overnight.

    market="us"  → closes US + crypto intraday positions at 15:55 ET
    market="uk"  → closes UK intraday positions at 16:25 BST
    market="eu"  → closes European stock intraday positions at 17:20 CET
    """
    intraday_tfs = {"1Min", "5Min", "15Min"}

    async with SessionLocal() as db:
        open_result = await db.execute(select(Trade).where(Trade.status == TradeStatus.open))
        open_trades = open_result.scalars().all()

        settings_result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = settings_result.scalar_one_or_none()

        flattened = 0
        for trade in open_trades:
            strat_result = await db.execute(
                select(Strategy).where(Strategy.id == trade.strategy_id)
            )
            strategy = strat_result.scalar_one_or_none()
            if not strategy:
                continue

            tf = strategy.parameters.get("timeframe", "1Day")
            if tf not in intraday_tfs:
                continue

            sym = trade.symbol
            _su = sym.upper()
            is_uk  = _su.endswith(".L")
            is_eu  = any(_su.endswith(sfx) for sfx in _EU_STOCK_SUFFIXES) and not is_uk
            is_fx  = _su.endswith("=X")
            is_cry = "/" in sym or _su.endswith(("-USD", "USDT"))

            if market == "us" and (is_uk or is_eu):
                continue
            if market == "uk" and not is_uk:
                continue
            if market == "eu" and not is_eu:
                continue

            # Fetch live price — broker where possible, yfinance for EU/UK/forex/commodity
            try:
                is_crypto_    = "/" in sym or sym.upper().endswith(("-USD", "USDT"))
                is_commodity_ = sym.upper().endswith("=F")
                current_price = None

                if is_crypto_ and settings and settings.binance_api_key:
                    from broker.binance import BinanceBroker
                    b_ = BinanceBroker(
                        settings.binance_api_key, settings.binance_secret_key,
                        testnet=settings.binance_testnet
                    )
                    current_price = b_.get_live_price(sym)
                elif not (is_uk or is_eu or is_commodity_ or is_fx or is_cry) \
                        and settings and settings.alpaca_api_key:
                    # US stocks via Alpaca
                    from broker.alpaca import AlpacaBroker
                    b_ = AlpacaBroker(
                        settings.alpaca_api_key, settings.alpaca_secret_key,
                        settings.paper_trading
                    )
                    current_price = b_.get_live_price(sym)
                else:
                    # UK, EU, forex, commodities — yfinance
                    import yfinance as _yf
                    _yf_sym = sym.replace("/", "-").replace("=", "-")
                    _hist = _yf.Ticker(_yf_sym).history(period="1d", interval="1m", auto_adjust=True)
                    if not _hist.empty:
                        current_price = float(_hist["Close"].iloc[-1])

                if current_price is None:
                    log.warning(f"[eod_flatten] Could not price {sym} — skipping")
                    continue

                # Close broker position (US stocks + crypto only; EU/UK/forex = paper record)
                if not (is_uk or is_eu or is_fx or is_commodity_) and settings:
                    try:
                        from broker.factory import create_broker, create_alpaca_broker
                        from broker.binance import BinanceBroker as _BB
                        if is_crypto_ and settings.binance_api_key:
                            _b = _BB(settings.binance_api_key, settings.binance_secret_key,
                                     testnet=settings.binance_testnet)
                        else:
                            _b = create_alpaca_broker(settings)
                        close_side = "sell" if trade.side == TradeSide.buy else "buy"
                        _b.place_market_order(sym, trade.qty, close_side)
                    except Exception as e:
                        log.error(f"[eod_flatten] Close order failed for {sym}: {e}")

                _close_trade(trade, current_price, "eod_flatten", strategy)
                flattened += 1
                log.info(f"[eod_flatten] Closed {sym} @ {current_price:.4f} (EOD {market.upper()})")

            except Exception as e:
                log.error(f"[eod_flatten] Error flattening {sym}: {e}")

        if flattened:
            await db.commit()
            try:
                from routers.events import write_event
                await write_event(
                    type="system", title=f"EOD flatten ({market.upper()}): {flattened} position(s) closed",
                    severity="info",
                    body=f"All scalping and day-trading positions closed before {market.upper()} market close.",
                    meta={"market": market, "flattened": flattened},
                )
            except Exception:
                pass

        log.info(f"[eod_flatten] {market.upper()}: {flattened} intraday position(s) closed")


async def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        log.info("Scheduler started")
    await reload_jobs()

    # Register drawdown monitor (every 5 minutes)
    scheduler.add_job(
        check_drawdown_kill_switch,
        trigger=IntervalTrigger(minutes=5),
        id="drawdown_monitor",
        name="Drawdown kill-switch monitor (5min)",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    log.info("Drawdown kill-switch monitor registered (every 5 min)")

    # Register Grok situational-awareness job (every 20 minutes)
    # Skips silently if XAI_API_KEY is not set
    scheduler.add_job(
        run_situational_awareness_job,
        trigger=IntervalTrigger(minutes=20),
        id="situational_awareness",
        name="Grok SA: market regime + risk posture (20min)",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    log.info("Grok situational-awareness job registered (every 20 min)")

    # Register Grok Trader morning scan (9:05 AM ET = 13:05 UTC, Mon–Fri)
    # Also registers a mid-session re-scan at 2:00 PM ET = 18:00 UTC
    from apscheduler.triggers.cron import CronTrigger
    from grok_trader import morning_scan_job

    scheduler.add_job(
        morning_scan_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=13, minute=5, timezone="UTC"),
        id="grok_trader_morning",
        name="Grok Trader: pre-market catalyst scan (9:05 AM ET)",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        morning_scan_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone="UTC"),
        id="grok_trader_midsession",
        name="Grok Trader: mid-session re-scan (2:00 PM ET)",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    log.info("Grok Trader scan jobs registered (9:05 AM ET + 2:00 PM ET, Mon–Fri)")

    # Register fast SL/TP monitor (every 30 seconds — independent of strategy ticks)
    from auto_exec import sl_tp_monitor, grok_auto_entries
    scheduler.add_job(
        sl_tp_monitor,
        trigger=IntervalTrigger(seconds=30),
        id="sl_tp_monitor",
        name="Auto SL/TP monitor — all open trades (30s)",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    log.info("Fast SL/TP monitor registered (every 30 s)")

    # Register Grok auto-entry executor (every 60 seconds, only active if grok_auto_trade=True)
    scheduler.add_job(
        grok_auto_entries,
        trigger=IntervalTrigger(seconds=60),
        id="grok_auto_entries",
        name="Grok auto-entry executor (60s)",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    log.info("Grok auto-entry executor registered (every 60 s)")

    # ── Universe screener + 4-layer stock filter ─────────────────────────────
    # Runs every 60 min throughout the day to keep the candidate list fresh.
    # Also runs at 06:00 UTC (07:00 BST) specifically to have candidates ready
    # before the pre-market Grok scan at 06:45 UTC (07:45 BST).
    def _run_universe_screener():
        try:
            from universe import screen_universe
            screen_universe()
        except Exception as e:
            log.warning(f"Universe screener error: {e}")

    scheduler.add_job(
        _run_universe_screener,
        trigger=IntervalTrigger(minutes=60),
        id="universe_screener",
        name="Full universe screener (60 min)",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    log.info("Universe screener registered (every 60 min)")

    # Dedicated pre-market run — 06:00 UTC (07:00 BST) Mon–Fri
    # Ensures fresh candidates are ready before the LSE open Grok scan
    scheduler.add_job(
        _run_universe_screener,
        trigger=CronTrigger(day_of_week="mon-fri", hour=6, minute=0, timezone="UTC"),
        id="universe_screener_premarket",
        name="Pre-market universe screener (07:00 BST)",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    log.info("Pre-market universe screener registered (06:00 UTC / 07:00 BST)")

    # EOD flatten jobs — close all scalping and day-trading positions before close
    # US: 15:55 ET (America/New_York handles DST automatically)
    # UK: 16:25 Europe/London (10 min before LSE 16:35 close)
    import asyncio as _asyncio

    scheduler.add_job(
        lambda: _asyncio.ensure_future(eod_flatten("us")),
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=55,
                            timezone="America/New_York"),
        id="eod_flatten_us",
        name="EOD flatten: US scalping + day-trading positions (15:55 ET)",
        replace_existing=True, coalesce=True, max_instances=1,
    )
    scheduler.add_job(
        lambda: _asyncio.ensure_future(eod_flatten("uk")),
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=25,
                            timezone="Europe/London"),
        id="eod_flatten_uk",
        name="EOD flatten: UK scalping + day-trading positions (16:25 BST)",
        replace_existing=True, coalesce=True, max_instances=1,
    )
    scheduler.add_job(
        lambda: _asyncio.ensure_future(eod_flatten("eu")),
        trigger=CronTrigger(day_of_week="mon-fri", hour=17, minute=20,
                            timezone="Europe/Paris"),
        id="eod_flatten_eu",
        name="EOD flatten: EU scalping + day-trading positions (17:20 CET)",
        replace_existing=True, coalesce=True, max_instances=1,
    )
    log.info("EOD flatten jobs registered (15:55 ET / 16:25 BST / 17:20 CET)")

    # ── Grok scans at market open windows ────────────────────────────────────
    # 06:45 UTC = 07:45 BST — 15 min before LSE open (candidates are ready)
    # 13:15 UTC = 14:15 BST — 15 min before NYSE open
    async def _run_grok_scan():
        try:
            from grok_trader import run_morning_scan
            await run_morning_scan()
        except Exception as e:
            log.warning(f"Scheduled Grok scan error: {e}")

    scheduler.add_job(
        _run_grok_scan,
        trigger=CronTrigger(day_of_week="mon-fri", hour=6, minute=45, timezone="UTC"),
        id="grok_scan_lse_open",
        name="Grok scan — LSE pre-open (07:45 BST)",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    log.info("Grok LSE pre-open scan registered (06:45 UTC / 07:45 BST)")

    scheduler.add_job(
        _run_grok_scan,
        trigger=CronTrigger(day_of_week="mon-fri", hour=13, minute=15, timezone="UTC"),
        id="grok_scan_nyse_open",
        name="Grok scan — NYSE pre-open (14:15 BST)",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    log.info("Grok NYSE pre-open scan registered (13:15 UTC / 14:15 BST)")
