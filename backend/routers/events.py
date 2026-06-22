"""
Event log — unified feed of trade, signal, risk, scanner, autopilot, and system events.

write_event() is a thin async helper imported by other routers to log events
without coupling them to this router's internals.
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from database import get_db, SessionLocal
from models import EventLog, Trade, TradeStatus

log = logging.getLogger("events")
router = APIRouter(prefix="/events", tags=["events"])


# ── Public helper — call from any router or scheduler ──────────────────────────

async def write_event(
    type: str,
    title: str,
    severity: str = "info",
    body: Optional[str] = None,
    symbol: Optional[str] = None,
    pnl: Optional[float] = None,
    meta: Optional[dict] = None,
    db: Optional[AsyncSession] = None,
) -> None:
    """Persist a single event. Opens its own session if db is not provided."""
    event = EventLog(
        type=type,
        severity=severity,
        title=title,
        body=body,
        symbol=symbol,
        pnl=pnl,
        meta=meta or {},
        created_at=datetime.utcnow(),
    )
    if db is not None:
        db.add(event)
        # caller is responsible for commit
    else:
        async with SessionLocal() as session:
            session.add(event)
            await session.commit()


# ── Backfill helper — called once on first GET /events ────────────────────────

_backfilled = False

async def _maybe_backfill(db: AsyncSession) -> None:
    """
    On first request, synthesise events from existing closed trades so the feed
    is not empty on a fresh dashboard open. Only runs once per process.
    """
    global _backfilled
    if _backfilled:
        return
    _backfilled = True

    count_result = await db.execute(select(func.count(EventLog.id)))
    if (count_result.scalar() or 0) > 0:
        return  # table already has entries

    closed_result = await db.execute(
        select(Trade).where(Trade.status == TradeStatus.closed).order_by(Trade.closed_at)
    )
    closed = closed_result.scalars().all()
    open_result = await db.execute(
        select(Trade).where(Trade.status == TradeStatus.open).order_by(Trade.opened_at)
    )
    open_trades = open_result.scalars().all()

    for t in closed:
        pnl_val = t.pnl or 0.0
        sev = "success" if pnl_val >= 0 else "error"
        reason_label = {
            "stop_loss":   "stop-loss hit",
            "take_profit": "take-profit hit",
            "signal":      "signal exit",
            "end_of_data": "end of data",
        }.get(t.exit_reason or "", t.exit_reason or "exit")
        db.add(EventLog(
            type="trade_close",
            severity=sev,
            title=f"{t.symbol} closed — {reason_label}",
            body=f"P&L: {'+' if pnl_val >= 0 else ''}${pnl_val:.2f} ({t.pnl_pct or 0:+.2f}%)",
            symbol=t.symbol,
            pnl=pnl_val,
            meta={"trade_id": t.id, "exit_reason": t.exit_reason, "side": str(t.side)},
            created_at=t.closed_at or datetime.utcnow(),
        ))

    for t in open_trades:
        db.add(EventLog(
            type="trade_open",
            severity="info",
            title=f"{t.symbol} position opened",
            body=f"Side: {str(t.side).upper()} · Entry: ${t.entry_price or '—'} · SL: ${t.stop_loss_price or '—'}",
            symbol=t.symbol,
            pnl=None,
            meta={"trade_id": t.id, "side": str(t.side)},
            created_at=t.opened_at,
        ))

    if closed or open_trades:
        await db.commit()
        log.info(f"[events] backfilled {len(closed)} closed + {len(open_trades)} open trade events")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/")
async def list_events(
    limit: int = Query(50, le=200),
    type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _maybe_backfill(db)

    q = select(EventLog).order_by(desc(EventLog.created_at)).limit(limit)
    if type:
        q = q.where(EventLog.type == type)
    if severity:
        q = q.where(EventLog.severity == severity)

    result = await db.execute(q)
    events = result.scalars().all()
    return [
        {
            "id":         e.id,
            "type":       e.type,
            "severity":   e.severity,
            "title":      e.title,
            "body":       e.body,
            "symbol":     e.symbol,
            "pnl":        e.pnl,
            "meta":       e.meta or {},
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


@router.get("/summary")
async def events_summary(db: AsyncSession = Depends(get_db)):
    """Quick counts by type for the dashboard status row."""
    await _maybe_backfill(db)
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    total   = (await db.execute(select(func.count(EventLog.id)))).scalar() or 0
    today_n = (await db.execute(
        select(func.count(EventLog.id)).where(EventLog.created_at >= today)
    )).scalar() or 0
    warnings = (await db.execute(
        select(func.count(EventLog.id)).where(
            EventLog.severity.in_(["warning", "error"]),
            EventLog.created_at >= today,
        )
    )).scalar() or 0

    last_result = await db.execute(
        select(EventLog).order_by(desc(EventLog.created_at)).limit(1)
    )
    last = last_result.scalar_one_or_none()

    return {
        "total":       total,
        "today":       today_n,
        "warnings_today": warnings,
        "last_event":  {
            "title":      last.title if last else None,
            "created_at": last.created_at.isoformat() if last and last.created_at else None,
        },
    }
