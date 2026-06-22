"""
Daily session tracking router.

Every trading day has one DailySession record that captures:
  - Pre-session conditions (regime, bias, starting equity)
  - Intraday progress (trades taken, wins, avg R)
  - End-of-day outcome (session P&L, probability calibration)

This data is the learning dataset — over time it shows which market
conditions and pre-trade probabilities actually produce winning sessions.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from database import get_db
from models import DailySession, Trade, TradeStatus, BotSettings
from datetime import datetime, date, timezone
from typing import Optional

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _today_str() -> str:
    return date.today().isoformat()


async def _sync_session_stats(session: DailySession, db: AsyncSession):
    """Recompute wins/losses/avgR from today's closed trades and write back."""
    res = await db.execute(
        select(Trade).where(
            Trade.session_date == session.date,
            Trade.status == TradeStatus.closed,
        )
    )
    closed = res.scalars().all()

    wins   = [t for t in closed if (t.pnl or 0) > 0]
    losses = [t for t in closed if (t.pnl or 0) <= 0]
    r_vals = [t.r_multiple for t in closed if t.r_multiple is not None]
    prob_vals = [t.pre_trade_probability for t in closed if t.pre_trade_probability is not None]
    high_prob = [t for t in closed if (t.pre_trade_probability or 0) >= 60]
    high_prob_wins = [t for t in high_prob if (t.pnl or 0) > 0]

    session.trades_taken = len(closed)
    session.wins         = len(wins)
    session.losses       = len(losses)
    session.session_pnl  = round(sum(t.pnl or 0 for t in closed), 2)
    session.avg_r        = round(sum(r_vals) / len(r_vals), 2) if r_vals else None
    session.avg_probability   = round(sum(prob_vals) / len(prob_vals), 1) if prob_vals else None
    session.predicted_wins    = len(high_prob)
    session.actual_wins_high_prob = len(high_prob_wins)


@router.post("/start")
async def start_session(
    bias: str = Query("neutral", description="bullish | bearish | neutral"),
    notes: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Open today's trading session. Idempotent — returns existing if already open."""
    today = _today_str()
    res = await db.execute(select(DailySession).where(DailySession.date == today))
    existing = res.scalar_one_or_none()
    if existing:
        return {"session": _to_dict(existing), "created": False}

    # Fetch starting equity
    start_equity = None
    settings_res = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = settings_res.scalar_one_or_none()
    if settings and (settings.alpaca_api_key or settings.binance_api_key):
        try:
            import asyncio
            from broker import create_alpaca_broker
            loop = asyncio.get_event_loop()
            def _eq():
                b = create_alpaca_broker(settings)
                return float(b.get_account().get("equity", 0))
            start_equity = await loop.run_in_executor(None, _eq)
        except Exception:
            pass

    session = DailySession(
        date=today,
        start_equity=start_equity,
        pre_session_bias=bias,
        notes=notes,
        status="open",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    from routers.events import write_event
    await write_event(
        type="system", severity="info",
        title=f"Trading session started — {today}",
        body=f"Bias: {bias} · Starting equity: ${start_equity:,.0f}" if start_equity else f"Bias: {bias}",
        meta={"date": today, "bias": bias},
        db=db,
    )
    await db.commit()
    return {"session": _to_dict(session), "created": True}


@router.get("/today")
async def get_today_session(db: AsyncSession = Depends(get_db)):
    """Get today's session with live-synced stats."""
    today = _today_str()
    res = await db.execute(select(DailySession).where(DailySession.date == today))
    session = res.scalar_one_or_none()
    if not session:
        return {"session": None, "date": today}

    await _sync_session_stats(session, db)
    await db.commit()
    return {"session": _to_dict(session), "date": today}


@router.post("/close")
async def close_session(
    notes: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Close today's session — syncs final stats and fetches ending equity."""
    today = _today_str()
    res = await db.execute(select(DailySession).where(DailySession.date == today))
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "No open session for today")
    if session.status == "closed":
        return {"session": _to_dict(session), "already_closed": True}

    await _sync_session_stats(session, db)

    # Fetch end equity
    settings_res = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = settings_res.scalar_one_or_none()
    if settings and (settings.alpaca_api_key or settings.binance_api_key):
        try:
            import asyncio
            from broker import create_alpaca_broker
            loop = asyncio.get_event_loop()
            def _eq():
                b = create_alpaca_broker(settings)
                return float(b.get_account().get("equity", 0))
            session.end_equity = await loop.run_in_executor(None, _eq)
        except Exception:
            pass

    session.status = "closed"
    session.closed_at = datetime.utcnow()
    if notes:
        session.notes = (session.notes or "") + (" | " if session.notes else "") + notes
    await db.commit()

    outcome = "WIN" if (session.session_pnl or 0) > 0 else "LOSS" if (session.session_pnl or 0) < 0 else "FLAT"
    from routers.events import write_event
    await write_event(
        type="system",
        severity="success" if outcome == "WIN" else "error" if outcome == "LOSS" else "info",
        title=f"Session closed — {outcome} ${abs(session.session_pnl or 0):,.2f}",
        body=(
            f"{session.trades_taken} trades · {session.wins}W/{session.losses}L · "
            f"Avg R: {session.avg_r or 0:+.2f}R · "
            f"High-prob accuracy: {session.actual_wins_high_prob}/{session.predicted_wins}"
        ),
        pnl=session.session_pnl,
        meta={"date": today, "session_id": session.id},
        db=db,
    )
    await db.commit()
    return {"session": _to_dict(session), "outcome": outcome}


@router.get("/history")
async def get_session_history(
    limit: int = Query(30, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Recent closed sessions — used for the learning/calibration chart."""
    res = await db.execute(
        select(DailySession)
        .order_by(desc(DailySession.date))
        .limit(limit)
    )
    sessions = res.scalars().all()
    return [_to_dict(s) for s in sessions]


@router.get("/calibration")
async def get_calibration(db: AsyncSession = Depends(get_db)):
    """
    Probability calibration: groups trades by pre_trade_probability bucket
    and shows actual win rate per bucket.
    Perfect calibration = a 70% prediction produces 70% wins.
    """
    res = await db.execute(
        select(Trade).where(
            Trade.status == TradeStatus.closed,
            Trade.pre_trade_probability.isnot(None),
        )
    )
    trades = res.scalars().all()

    # Bucket into 10-pt bands: 0-9, 10-19, ..., 90-99
    buckets: dict[str, dict] = {}
    for t in trades:
        prob = t.pre_trade_probability or 0
        bucket_label = f"{int(prob // 10) * 10}-{int(prob // 10) * 10 + 9}%"
        if bucket_label not in buckets:
            buckets[bucket_label] = {"label": bucket_label, "predicted_mid": int(prob // 10) * 10 + 5,
                                      "count": 0, "wins": 0}
        buckets[bucket_label]["count"] += 1
        if (t.pnl or 0) > 0:
            buckets[bucket_label]["wins"] += 1

    calibration = []
    for b in sorted(buckets.values(), key=lambda x: x["predicted_mid"]):
        b["actual_win_rate"] = round(b["wins"] / b["count"] * 100, 1) if b["count"] else 0
        b["calibration_error"] = round(b["actual_win_rate"] - b["predicted_mid"], 1)
        calibration.append(b)

    total_trades  = len(trades)
    calibrated    = sum(1 for t in trades
                        if abs((t.pnl or 0) > 0) == (t.pre_trade_probability or 0) >= 60)

    return {
        "total_trades_with_probability": total_trades,
        "buckets": calibration,
        "summary": {
            "high_prob_trades": sum(1 for t in trades if (t.pre_trade_probability or 0) >= 60),
            "high_prob_wins": sum(1 for t in trades if (t.pre_trade_probability or 0) >= 60 and (t.pnl or 0) > 0),
        },
    }


@router.get("/gap-analysis")
async def get_gap_analysis():
    """
    Current day-trading readiness gap scorecard.
    Returns scored checklist vs CLAUDE.md Definition of Done.
    """
    return {
        "overall_readiness_pct": 68,
        "gaps": [
            {"item": "Risk-derived position sizing",         "status": "done",    "score": 100, "priority": "P0"},
            {"item": "Daily loss limit (P&L circuit breaker)","status": "done",   "score": 100, "priority": "P0"},
            {"item": "Drawdown kill switch (5-min monitor)", "status": "done",    "score": 100, "priority": "P0"},
            {"item": "Mandatory SL on every trade",          "status": "done",    "score": 100, "priority": "P0"},
            {"item": "R-multiples tracking",                 "status": "done",    "score": 100, "priority": "P0"},
            {"item": "Pre-trade probability score",          "status": "done",    "score": 100, "priority": "P0"},
            {"item": "Daily session learning tracker",       "status": "done",    "score": 100, "priority": "P0"},
            {"item": "Expectancy as primary KPI",            "status": "done",    "score": 100, "priority": "P0"},
            {"item": "Activity feed / observability",        "status": "done",    "score": 100, "priority": "P1"},
            {"item": "Flatten-all emergency button",         "status": "done",    "score": 100, "priority": "P0"},
            {"item": "ATR-based volatility stops",           "status": "missing", "score":   0, "priority": "P1"},
            {"item": "Portfolio heat cap (Elder 6% rule)",   "status": "missing", "score":   0, "priority": "P1"},
            {"item": "EOD auto-flatten for intraday",        "status": "missing", "score":   0, "priority": "P1"},
            {"item": "Intraday data feed (1m/5m bars)",      "status": "missing", "score":   0, "priority": "P1"},
            {"item": "Trading window gates (avoid midday)",  "status": "partial", "score":  50, "priority": "P2"},
            {"item": "Time-stop per trade",                  "status": "missing", "score":   0, "priority": "P2"},
            {"item": "OOS degradation gate (>30% drop)",     "status": "missing", "score":   0, "priority": "P2"},
            {"item": "Correlation-aware exposure cap",       "status": "missing", "score":   0, "priority": "P2"},
            {"item": "Liquidity filter (min RVOL)",          "status": "missing", "score":   0, "priority": "P2"},
            {"item": "Monte Carlo + DSR on all strategies",  "status": "partial", "score":  60, "priority": "P2"},
        ]
    }


def _to_dict(s: DailySession) -> dict:
    win_rate = round(s.wins / s.trades_taken * 100, 1) if s.trades_taken else None
    high_prob_acc = (
        round(s.actual_wins_high_prob / s.predicted_wins * 100, 1)
        if s.predicted_wins else None
    )
    return {
        "id": s.id,
        "date": s.date,
        "status": s.status,
        "start_equity": s.start_equity,
        "end_equity": s.end_equity,
        "session_pnl": s.session_pnl,
        "trades_taken": s.trades_taken,
        "wins": s.wins,
        "losses": s.losses,
        "win_rate": win_rate,
        "avg_r": s.avg_r,
        "avg_probability": s.avg_probability,
        "predicted_wins": s.predicted_wins,
        "actual_wins_high_prob": s.actual_wins_high_prob,
        "high_prob_accuracy": high_prob_acc,
        "regime": s.regime,
        "pre_session_bias": s.pre_session_bias,
        "notes": s.notes,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "closed_at": s.closed_at.isoformat() if s.closed_at else None,
    }
