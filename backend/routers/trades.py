from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from database import get_db
from models import Trade, TradeStatus, TradeSide, Strategy, BotSettings
from schemas import TradeOut
from datetime import datetime, timedelta
from typing import Optional

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("/", response_model=list[TradeOut])
async def list_trades(
    status: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(Trade).order_by(desc(Trade.opened_at)).limit(limit)
    if status:
        query = query.where(Trade.status == status)
    if symbol:
        query = query.where(Trade.symbol == symbol.upper())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats")
async def get_trade_stats(db: AsyncSession = Depends(get_db)):
    total = await db.execute(select(func.count(Trade.id)))
    total_count = total.scalar() or 0

    closed = await db.execute(select(Trade).where(Trade.status == TradeStatus.closed))
    closed_trades = closed.scalars().all()

    winning = [t for t in closed_trades if t.pnl and t.pnl > 0]
    losing = [t for t in closed_trades if t.pnl and t.pnl < 0]

    pnls = [t.pnl for t in closed_trades if t.pnl is not None]
    total_pnl = sum(pnls)
    avg_win  = sum(t.pnl for t in winning) / len(winning) if winning else 0
    avg_loss = sum(t.pnl for t in losing)  / len(losing)  if losing  else 0
    win_rate = len(winning) / len(closed_trades) * 100 if closed_trades else 0
    gross_profit = sum(t.pnl for t in winning)
    gross_loss   = abs(sum(t.pnl for t in losing))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else 0

    # Primary KPIs (CLAUDE.md): Expectancy, Profit Factor, Max Drawdown, Sharpe
    import numpy as np
    from metrics import expectancy as _expectancy, max_drawdown as _max_dd, sharpe as _sharpe
    exp = _expectancy(pnls) if pnls else 0.0

    # Build cumulative equity curve from closed trades ordered by close time
    sorted_trades = sorted(closed_trades, key=lambda t: t.closed_at or datetime.min)
    equity = np.array([100_000.0] + [100_000.0 + sum(
        t.pnl for t in sorted_trades[:i+1] if t.pnl
    ) for i in range(len(sorted_trades))])
    max_dd_pct = round(float(_max_dd(equity)) * 100, 2) if len(equity) > 1 else 0.0
    sharpe_val  = round(float(_sharpe(equity)), 3)      if len(equity) > 1 else 0.0

    # R-multiple distribution (Tharp)
    r_multiples = [t.r_multiple for t in closed_trades if t.r_multiple is not None]
    avg_r        = round(sum(r_multiples) / len(r_multiples), 2) if r_multiples else None
    max_r        = round(max(r_multiples), 2) if r_multiples else None
    min_r        = round(min(r_multiples), 2) if r_multiples else None
    positive_r   = len([r for r in r_multiples if r > 0])

    open_count = await db.execute(select(func.count(Trade.id)).where(Trade.status == TradeStatus.open))

    # Daily trade count + daily P&L for limit widgets
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    daily_result = await db.execute(
        select(func.count(Trade.id)).where(Trade.opened_at >= today_start)
    )
    daily_pnl_res = await db.execute(
        select(func.sum(Trade.pnl)).where(
            Trade.status == TradeStatus.closed,
            Trade.closed_at >= today_start,
        )
    )
    settings_result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = settings_result.scalar_one_or_none()
    daily_limit = (settings.max_daily_trades if settings else None) or 20
    daily_loss_limit_pct = getattr(settings, "daily_loss_limit_pct", 3.0) if settings else 3.0

    return {
        "total_trades": total_count,
        "closed_trades": len(closed_trades),
        "open_trades": open_count.scalar() or 0,
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "expectancy": round(exp, 2),
        "profit_factor": profit_factor,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_drawdown_pct": max_dd_pct,
        "sharpe_ratio": sharpe_val,
        "daily_trades_today": daily_result.scalar() or 0,
        "daily_trades_limit": daily_limit,
        "daily_pnl_today": round(daily_pnl_res.scalar() or 0.0, 2),
        "daily_loss_limit_pct": daily_loss_limit_pct,
        "r_multiples": {
            "count": len(r_multiples),
            "avg_r": avg_r,
            "max_r": max_r,
            "min_r": min_r,
            "positive_r_count": positive_r,
        },
    }


@router.get("/pre-trade-check/{strategy_id}")
async def pre_trade_check(strategy_id: int, db: AsyncSession = Depends(get_db)):
    """
    Compute a pre-trade probability score BEFORE placing an order.
    Call this to get a GO/NO-GO recommendation with breakdown.
    The score is also stored on the Trade record when the order executes.
    """
    from probability import PreTradeSignal, score_trade

    strat_result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy_row = strat_result.scalar_one_or_none()
    if not strategy_row:
        raise HTTPException(404, "Strategy not found")

    # Historical performance for this strategy
    perf_result = await db.execute(
        select(Trade).where(
            Trade.strategy_id == strategy_id,
            Trade.status == TradeStatus.closed,
        )
    )
    closed = perf_result.scalars().all()
    wins = [t for t in closed if (t.pnl or 0) > 0]
    r_vals = [t.r_multiple for t in closed if t.r_multiple is not None]
    pf_gross = sum(t.pnl for t in wins)
    pf_loss  = abs(sum(t.pnl for t in closed if (t.pnl or 0) < 0))

    # Fetch recent bars for regime detection
    recent_closes: list[float] = []
    settings_result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = settings_result.scalar_one_or_none()
    try:
        if settings and (settings.alpaca_api_key or settings.binance_api_key):
            import asyncio
            from routers.strategies import _pick_broker, _fetch_bars
            loop = asyncio.get_event_loop()
            def _bars():
                tf = strategy_row.parameters.get("timeframe", "1Day")
                broker = _pick_broker(settings, strategy_row.symbol)
                df = _fetch_bars(broker, strategy_row.symbol, tf, settings)
                return df["close"].tolist()[-60:] if df is not None and len(df) >= 5 else []
            recent_closes = await loop.run_in_executor(None, _bars)
    except Exception:
        recent_closes = []

    sl_pct = strategy_row.risk_config.get("stop_loss_pct", 1.5) or 1.5
    tp_pct = strategy_row.risk_config.get("take_profit_pct", 3.0) or 3.0

    signal_obj = PreTradeSignal(
        signal_confidence=0.5,           # neutral until signal generated
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        strategy_closed_trades=len(closed),
        strategy_win_rate=len(wins) / len(closed) if closed else 0.0,
        strategy_avg_r=round(sum(r_vals) / len(r_vals), 2) if r_vals else None,
        strategy_profit_factor=round(pf_gross / pf_loss, 2) if pf_loss else 0.0,
        recent_closes=recent_closes,
        symbol=strategy_row.symbol,
        timeframe=strategy_row.parameters.get("timeframe", "1Day"),
    )

    result = score_trade(signal_obj)
    return {
        "strategy_id": strategy_id,
        "strategy_name": strategy_row.name,
        "symbol": strategy_row.symbol,
        "score": result.score,
        "verdict": result.verdict,
        "go": result.go,
        "expected_r": result.expected_r,
        "components": result.components,
        "reasons": result.reasons,
        "historical": {
            "closed_trades": len(closed),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "avg_r": signal_obj.strategy_avg_r,
            "profit_factor": signal_obj.strategy_profit_factor,
        },
    }


@router.post("/execute/{strategy_id}")
async def execute_strategy(strategy_id: int, db: AsyncSession = Depends(get_db)):
    """Run a strategy and place a live paper order if signal is buy/sell."""
    strat_result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy_row = strat_result.scalar_one_or_none()
    if not strategy_row:
        raise HTTPException(404, "Strategy not found")

    settings_result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = settings_result.scalar_one_or_none()
    if not settings or (not settings.alpaca_api_key and not settings.binance_api_key):
        raise HTTPException(400, "No broker API keys configured. Go to Settings first.")

    import asyncio
    from strategies import get_strategy
    from routers.strategies import _pick_broker, _fetch_bars

    sym = strategy_row.symbol
    is_uk_stock  = sym.upper().endswith(".L")
    is_commodity = sym.upper().endswith("=F")

    def _run(settings, strategy_row):
        timeframe = strategy_row.parameters.get("timeframe", "1Day")
        broker = _pick_broker(settings, sym)
        df    = _fetch_bars(broker, sym, timeframe, settings)
        strat = get_strategy(strategy_row.template, strategy_row.parameters,
                             strategy_row.risk_config, sym)
        signal = strat.generate_signal(df)
        account = broker.get_account() if not (is_uk_stock or is_commodity) else {"equity": 100_000}
        return signal, broker, account

    try:
        loop = asyncio.get_event_loop()
        signal, broker, account = await loop.run_in_executor(None, _run, settings, strategy_row)
    except Exception as e:
        raise HTTPException(500, str(e))

    if signal.action == "hold":
        return {"action": "hold", "message": "No trade signal generated", "indicators": signal.indicators}

    # Daily trade limit guard
    if signal.action == "buy":
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        daily_result = await db.execute(
            select(func.count(Trade.id)).where(Trade.opened_at >= today_start)
        )
        daily_count = daily_result.scalar() or 0
        max_daily = settings.max_daily_trades or 20
        if daily_count >= max_daily:
            raise HTTPException(429, f"Daily trade limit ({max_daily}) reached — {daily_count} trades placed today. Resets at midnight UTC.")

    if is_uk_stock or is_commodity:
        return {
            "action": signal.action,
            "signal_confidence": signal.confidence,
            "message": f"{sym} is a {'UK stock' if is_uk_stock else 'commodity'} — live orders require a CFD broker. Signal recorded.",
            "indicators": signal.indicators,
        }

    current_price   = signal.indicators.get("close", 0)
    if not current_price or current_price <= 0:
        raise HTTPException(400, "Cannot get valid price — cannot set stop loss. Trade blocked.")

    sl_pct = strategy_row.risk_config.get("stop_loss_pct", 1.5) or 1.5
    tp_pct = strategy_row.risk_config.get("take_profit_pct", 3.0) or 3.0
    sl_price_val = round(current_price * (1 - sl_pct / 100), 6)

    # Risk-derived sizing: risk a fixed fraction of equity per trade
    risk_pct = strategy_row.risk_config.get(
        "risk_per_trade_pct",
        float(settings.risk_per_trade_pct) if hasattr(settings, "risk_per_trade_pct") else 1.0,
    )
    equity = float(account.get("equity", 100_000))
    stop_distance = current_price - sl_price_val
    if stop_distance <= 0:
        raise HTTPException(400, "Stop distance is zero — cannot size position.")
    risk_capital = equity * (risk_pct / 100)
    raw_qty = risk_capital / stop_distance
    # Whole shares for equities; fractional for crypto
    is_crypto = not (sym.upper().endswith(".L") or sym.upper().endswith("=F"))
    qty = max(1.0, round(raw_qty)) if not any(c in sym for c in ["-", "BTC", "ETH", "SOL", "BNB"]) else max(0.000001, raw_qty)
    initial_risk_amt = round(stop_distance * qty, 2)

    if qty <= 0:
        raise HTTPException(400, "Calculated quantity too small")

    tp_price_val = round(current_price * (1 + tp_pct / 100), 6) if current_price else None

    # Pre-trade probability score (stored on the trade for calibration learning)
    from probability import PreTradeSignal, score_trade
    from datetime import date as _date
    _closed_res = await db.execute(
        select(Trade).where(Trade.strategy_id == strategy_row.id, Trade.status == TradeStatus.closed)
    )
    _closed_strat = _closed_res.scalars().all()
    _wins_strat = [t for t in _closed_strat if (t.pnl or 0) > 0]
    _r_vals = [t.r_multiple for t in _closed_strat if t.r_multiple is not None]
    _pf_g = sum(t.pnl for t in _wins_strat)
    _pf_l = abs(sum(t.pnl for t in _closed_strat if (t.pnl or 0) < 0))
    pre_signal = PreTradeSignal(
        signal_confidence=signal.confidence,
        sl_pct=sl_pct, tp_pct=tp_pct,
        strategy_closed_trades=len(_closed_strat),
        strategy_win_rate=len(_wins_strat) / len(_closed_strat) if _closed_strat else 0.5,
        strategy_avg_r=round(sum(_r_vals) / len(_r_vals), 2) if _r_vals else None,
        strategy_profit_factor=round(_pf_g / _pf_l, 2) if _pf_l else 0.0,
        symbol=sym,
        timeframe=strategy_row.parameters.get("timeframe", "1Day"),
    )
    pre_result = score_trade(pre_signal)

    order = broker.place_market_order(sym, qty, signal.action,
                                      stop_loss_price=round(sl_price_val, 2),
                                      take_profit_price=round(tp_price_val, 2) if tp_price_val else None,
                                      strategy_name=strategy_row.name)

    trade = Trade(
        strategy_id=strategy_row.id,
        strategy_name=strategy_row.name,
        symbol=sym,
        side=TradeSide.buy if signal.action == "buy" else TradeSide.sell,
        qty=qty,
        entry_price=current_price,
        stop_loss_price=sl_price_val,
        take_profit_price=tp_price_val,
        initial_risk=initial_risk_amt,
        pre_trade_probability=float(pre_result.score),
        pre_trade_expected_r=pre_result.expected_r,
        setup_score=pre_result.score,
        session_date=_date.today().isoformat(),
        status=TradeStatus.open,
        alpaca_order_id=order.get("order_id"),
        notes=f"Manual execute | conf:{signal.confidence:.2f} | prob:{pre_result.score} ({pre_result.verdict}) | risk:{risk_pct}% SL:{sl_pct}% TP:{tp_pct}%",
    )
    db.add(trade)
    strategy_row.total_trades += 1
    await db.commit()

    from routers.events import write_event
    await write_event(
        type="trade_open", severity="info",
        title=f"{signal.action.upper()} {sym} @ ${current_price}",
        body=f"Manual execute · {strategy_row.name} · conf: {signal.confidence:.2f} · risk: {risk_pct}% (${initial_risk_amt}) · SL: ${trade.stop_loss_price} · TP: ${trade.take_profit_price}",
        symbol=sym,
        meta={"trade_id": trade.id, "strategy": strategy_row.name, "source": "manual"},
        db=db,
    )
    await db.commit()

    return {
        "action": signal.action,
        "signal_confidence": signal.confidence,
        "pre_trade_score": pre_result.score,
        "pre_trade_verdict": pre_result.verdict,
        "pre_trade_expected_r": pre_result.expected_r,
        "order": order,
        "trade_id": trade.id,
        "qty": qty,
        "initial_risk": initial_risk_amt,
        "risk_pct": risk_pct,
        "stop_loss_price": trade.stop_loss_price,
        "take_profit_price": trade.take_profit_price,
        "indicators": signal.indicators,
    }


@router.get("/equity-curve")
async def get_equity_curve(days: int = 30, db: AsyncSession = Depends(get_db)):
    """Running cumulative P&L curve from closed trade history, one point per day."""
    from datetime import date, timedelta
    today = date.today()
    start = today - timedelta(days=days)

    result = await db.execute(
        select(Trade).where(
            Trade.status == TradeStatus.closed,
            Trade.closed_at.isnot(None),
        ).order_by(Trade.closed_at)
    )
    closed = result.scalars().all()

    # Sum P&L per calendar day
    daily: dict[str, float] = {}
    for t in closed:
        if t.closed_at and t.pnl:
            d = t.closed_at.date().isoformat()
            daily[d] = daily.get(d, 0.0) + t.pnl

    # Build cumulative curve — only days within the window
    curve = []
    cum = 0.0
    for i in range(days):
        d = (start + timedelta(days=i + 1)).isoformat()
        cum += daily.get(d, 0.0)
        curve.append({"day": d, "pnl": round(cum, 2)})

    return curve


@router.get("/intraday-curve")
async def get_intraday_curve(db: AsyncSession = Depends(get_db)):
    """
    Minute-resolution P&L curve for today's session.
    Returns cumulative realized P&L at each trade-close event plus open unrealized P&L
    as the trailing point.
    """
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    result = await db.execute(
        select(Trade).where(
            Trade.status == TradeStatus.closed,
            Trade.closed_at >= today_start,
            Trade.closed_at.isnot(None),
        ).order_by(Trade.closed_at)
    )
    closed = result.scalars().all()

    open_result = await db.execute(
        select(Trade).where(Trade.status == TradeStatus.open)
    )
    open_trades = open_result.scalars().all()

    # Seed with session-open anchor
    points = [{"time": today_start.strftime("%H:%M"), "pnl": 0.0, "label": None}]
    cum = 0.0
    for t in closed:
        cum += t.pnl or 0.0
        points.append({
            "time": t.closed_at.strftime("%H:%M"),
            "pnl": round(cum, 2),
            "label": f"{t.symbol} {'+' if (t.pnl or 0) >= 0 else ''}${(t.pnl or 0):.2f}",
        })

    # Fetch live unrealized from broker snapshot
    unrealized = 0.0
    if open_trades:
        try:
            settings_res = await db.execute(select(BotSettings).where(BotSettings.id == 1))
            settings_snap = settings_res.scalar_one_or_none()
            if settings_snap and (settings_snap.alpaca_api_key or settings_snap.binance_api_key):
                import asyncio
                loop = asyncio.get_event_loop()
                from broker import create_alpaca_broker, create_broker
                from broker.binance import BinanceBroker

                def _fetch_unrealized():
                    total_unr = 0.0
                    if settings_snap.alpaca_api_key:
                        try:
                            b = create_alpaca_broker(settings_snap)
                            for p in b.get_positions():
                                total_unr += p.get("unrealized_pl", 0.0)
                        except Exception:
                            pass
                    if settings_snap.binance_api_key:
                        try:
                            b = create_broker(settings_snap)
                            if isinstance(b, BinanceBroker):
                                for p in b.get_positions():
                                    total_unr += p.get("unrealized_pl", 0.0)
                        except Exception:
                            pass
                    return total_unr

                unrealized = await loop.run_in_executor(None, _fetch_unrealized)
        except Exception:
            unrealized = 0.0

    now_time = datetime.utcnow().strftime("%H:%M")
    if open_trades:
        points.append({
            "time": now_time,
            "pnl": round(cum + unrealized, 2),
            "label": f"{len(open_trades)} open · unr: {'+' if unrealized >= 0 else ''}${unrealized:.2f}",
        })

    today_trades_result = await db.execute(
        select(func.count(Trade.id)).where(Trade.opened_at >= today_start)
    )

    return {
        "points": points,
        "today_realized": round(cum, 2),
        "today_closed_trades": len(closed),
        "today_total_trades": today_trades_result.scalar() or 0,
    }


@router.post("/{trade_id}/cancel")
async def cancel_trade(trade_id: int, db: AsyncSession = Depends(get_db)):
    """
    Cancel an open DB trade AND close the matching broker position so no orphan
    positions are ever left unprotected at the broker.
    """
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(404, "Trade not found")
    if trade.status != TradeStatus.open:
        raise HTTPException(400, f"Trade {trade_id} is already {trade.status.value} — cannot cancel")

    # Attempt to close the live broker position
    broker_result = None
    broker_error = None
    try:
        settings_res = await db.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = settings_res.scalar_one_or_none()
        if settings:
            from broker import create_broker
            broker = create_broker(settings)
            broker_result = broker.close_position(trade.symbol)
    except Exception as e:
        broker_error = str(e)

    # Mark DB record cancelled regardless of broker result (position may not exist)
    trade.status = TradeStatus.cancelled
    trade.exit_reason = "manually_cancelled"
    trade.closed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(trade)

    from routers.events import write_event
    await write_event(
        type="trade_close", severity="warning",
        title=f"Trade #{trade_id} cancelled — {trade.symbol}",
        body=f"Manual cancel · broker close: {broker_result or broker_error or 'no position found'}",
        symbol=trade.symbol,
        meta={"trade_id": trade_id, "broker_result": broker_result, "broker_error": broker_error},
    )

    return {
        "cancelled": True,
        "trade_id": trade_id,
        "symbol": trade.symbol,
        "broker_close": broker_result,
        "broker_error": broker_error,
    }


@router.patch("/{trade_id}/close")
async def close_trade(trade_id: int, exit_price: float, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(404, "Trade not found")

    trade.exit_price = exit_price
    trade.closed_at = datetime.utcnow()
    trade.status = TradeStatus.closed

    if trade.entry_price:
        multiplier = 1 if trade.side == TradeSide.buy else -1
        trade.pnl = round((exit_price - trade.entry_price) * trade.qty * multiplier, 2)
        trade.pnl_pct = round((exit_price - trade.entry_price) / trade.entry_price * 100 * multiplier, 2)

    if trade.strategy_id:
        strat_result = await db.execute(select(Strategy).where(Strategy.id == trade.strategy_id))
        strategy = strat_result.scalar_one_or_none()
        if strategy:
            strategy.total_pnl = round((strategy.total_pnl or 0) + (trade.pnl or 0), 2)
            if trade.pnl and trade.pnl > 0:
                strategy.winning_trades += 1

    await db.commit()
    await db.refresh(trade)
    return TradeOut.model_validate(trade, from_attributes=True)
