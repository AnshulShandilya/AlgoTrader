from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Strategy, StrategyStatus, BotSettings
from scheduler import get_scheduler_status, reload_jobs, run_strategy, get_scheduler

router = APIRouter(prefix="/automation", tags=["automation"])


@router.get("/status")
async def get_status():
    return get_scheduler_status()


@router.post("/reload")
async def reload():
    """
    Re-sync scheduler jobs with active strategies in DB.
    Forces all existing strategy jobs to fire their first tick within 10 seconds
    so manual Sync gives immediate signal checks without waiting the full interval.
    """
    await reload_jobs(force_immediate=True)
    return get_scheduler_status()


@router.post("/run-now/{strategy_id}")
async def run_now(strategy_id: int, db: AsyncSession = Depends(get_db)):
    """Force-run a strategy immediately outside the schedule."""
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(404, "Strategy not found")
    await run_strategy(strategy_id)
    return {"triggered": True, "strategy": strategy.name}


@router.post("/pause-all")
async def pause_all(db: AsyncSession = Depends(get_db)):
    """Emergency stop — pause all active strategies and clear all jobs."""
    result = await db.execute(select(Strategy).where(Strategy.status == StrategyStatus.active))
    strategies = result.scalars().all()
    for s in strategies:
        s.status = StrategyStatus.paused
    await db.commit()

    scheduler = get_scheduler()
    for job in scheduler.get_jobs():
        job.remove()

    return {"paused": len(strategies), "message": "All strategies paused and jobs cleared"}


@router.post("/grok-auto-trade/toggle")
async def toggle_grok_auto_trade(enabled: bool, db: AsyncSession = Depends(get_db)):
    """Enable or disable Grok autonomous trade execution."""
    result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = result.scalar_one_or_none()
    if not settings:
        raise HTTPException(404, "Bot settings not found — visit Settings page first")
    settings.grok_auto_trade = enabled
    await db.commit()
    return {"grok_auto_trade": enabled,
            "message": f"Grok auto-trade {'ENABLED' if enabled else 'DISABLED'}"}


@router.post("/grok-auto-trade/min-confidence")
async def set_grok_min_confidence(confidence: int, db: AsyncSession = Depends(get_db)):
    """Set the minimum Grok confidence % required to auto-execute a setup."""
    if not (0 <= confidence <= 100):
        raise HTTPException(400, "confidence must be 0–100")
    result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = result.scalar_one_or_none()
    if not settings:
        raise HTTPException(404, "Bot settings not found")
    settings.grok_min_confidence = confidence
    await db.commit()
    return {"grok_min_confidence": confidence}


@router.get("/grok-auto-trade/status")
async def grok_auto_trade_status(db: AsyncSession = Depends(get_db)):
    """Return current Grok auto-trade settings and monitor state."""
    from auto_exec import _grok_setup_ids_executed
    result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = result.scalar_one_or_none()
    enabled = bool(getattr(settings, "grok_auto_trade", False)) if settings else False
    min_conf = int(getattr(settings, "grok_min_confidence", 70)) if settings else 70

    try:
        from grok_trader import get_latest_setups
        pending = [s for s in get_latest_setups() if s["id"] not in _grok_setup_ids_executed]
    except Exception:
        pending = []

    return {
        "grok_auto_trade": enabled,
        "grok_min_confidence": min_conf,
        "pending_setups": len(pending),
        "executed_setup_ids": list(_grok_setup_ids_executed),
    }


@router.get("/grok/candidates")
async def get_grok_candidates():
    """Return the current 4-layer filtered stock candidates queued for Grok."""
    try:
        from stock_filter import get_grok_candidates as _get, summarise
        candidates = _get()
        return {
            "count": len(candidates),
            "source": "4-layer funnel" if candidates else "screener not yet run",
            "candidates": [c.to_dict() for c in candidates],
            "summary": summarise(candidates) if candidates else "No candidates yet — screener runs every 60 min",
        }
    except Exception as e:
        return {"count": 0, "candidates": [], "error": str(e)}


@router.get("/grok/last-scan")
async def get_grok_last_scan():
    """Return the full last Grok scan including sentiment summary and contract watchlist."""
    from grok_trader import get_last_scan
    scan = get_last_scan()
    if not scan:
        return {"ok": False, "error": "No scan run yet", "setups": [], "contract_watchlist": []}
    return {
        "ok": scan.ok,
        "scan_time_utc": scan.scan_time_utc,
        "market_bias": scan.market_bias,
        "session_overview": scan.session_overview,
        "sentiment_summary": scan.sentiment_summary,
        "setups": [s.to_dict() for s in scan.setups],
        "contract_watchlist": scan.contract_watchlist,
        "avoid_today": scan.avoid_today,
        "sources_searched": scan.sources_searched,
        "latency_ms": scan.latency_ms,
        "error": scan.error,
    }
