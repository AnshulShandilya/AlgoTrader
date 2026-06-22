from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database import get_db
from models import BotSettings, Trade, TradeStatus, Strategy, StrategyStatus
from schemas import BotSettingsSchema
from datetime import datetime

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


async def get_broker(db: AsyncSession):
    result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = result.scalar_one_or_none()
    if not settings or (not settings.alpaca_api_key and not settings.binance_api_key):
        raise HTTPException(400, "No broker API keys configured. Go to Settings first.")
    from broker import create_broker
    try:
        return create_broker(settings)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/broker")
async def get_active_broker(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = result.scalar_one_or_none()
    from broker import get_broker_name
    name = get_broker_name(settings) if settings else "none"
    return {"broker": name}


def _broker_name(broker) -> str:
    from broker.alpaca import AlpacaBroker
    from broker.binance import BinanceBroker
    if isinstance(broker, BinanceBroker):
        return "binance_testnet" if broker.testnet else "binance_live"
    if isinstance(broker, AlpacaBroker):
        return "alpaca_paper" if broker.paper else "alpaca_live"
    return "unknown"


def _create_fetch(settings, use_alpaca: bool = False):
    """
    Create broker + fetch account/positions entirely in one thread.
    Avoids sharing a requests Session across threads (python-binance 1.x).
    BinanceBroker uses _account_snapshot() to batch tickers in one HTTP call.
    """
    from broker import create_broker, create_alpaca_broker
    from broker.binance import BinanceBroker
    broker = create_alpaca_broker(settings) if use_alpaca else create_broker(settings)
    if isinstance(broker, BinanceBroker):
        account, positions = broker._account_snapshot()
    else:
        account  = broker.get_account()
        positions = broker.get_positions()
    return broker, account, positions


@router.get("/snapshot")
async def get_portfolio_snapshot(db: AsyncSession = Depends(get_db)):
    import asyncio

    result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = result.scalar_one_or_none()

    if not settings or (not settings.alpaca_api_key and not settings.binance_api_key):
        return {
            "error": "No broker API keys configured",
            "no_keys": True,
            "portfolio_value": None, "cash": None, "equity": None,
            "positions": [], "total_unrealized_pnl": 0.0,
            "total_realized_pnl": 0.0, "open_positions_count": 0,
        }

    loop = asyncio.get_event_loop()
    has_alpaca  = bool(settings.alpaca_api_key)
    has_binance = bool(settings.binance_api_key)

    # Fetch both brokers in parallel when both are configured
    alpaca_data  = None
    binance_data = None

    async def _fetch(use_alpaca: bool):
        return await loop.run_in_executor(None, _create_fetch, settings, use_alpaca)

    if has_binance:
        try:
            binance_data = await _fetch(False)
        except Exception:
            binance_data = None

    if has_alpaca:
        try:
            alpaca_data = await _fetch(True)
        except Exception:
            alpaca_data = None

    if not alpaca_data and not binance_data:
        return {
            "error": "Could not connect to any broker",
            "connection_error": True, "active_broker": "none",
            "portfolio_value": None, "cash": None, "equity": None,
            "positions": [], "total_unrealized_pnl": 0.0,
            "total_realized_pnl": 0.0, "open_positions_count": 0,
        }

    # Symbols we actually traded (open DB trades only)
    db_open_res = await db.execute(
        select(Trade.symbol).where(Trade.status == TradeStatus.open)
    )
    traded_symbols = {row[0].upper() for row in db_open_res.all()}

    # Build combined view: Alpaca is primary for portfolio value when available
    # (Alpaca paper = real paper trading; Binance testnet = secondary)
    all_positions = []
    total_portfolio = 0.0
    total_cash      = 0.0
    total_equity    = 0.0
    total_unrealized = 0.0

    alpaca_summary  = None
    binance_summary = None

    if alpaca_data:
        _, acc_a, pos_a = alpaca_data
        unr_a = sum(p["unrealized_pl"] for p in pos_a)
        alpaca_summary = {
            "broker": "alpaca_paper",
            "portfolio_value": float(acc_a.get("portfolio_value", 0)),
            "cash": float(acc_a.get("cash", 0)),
            "equity": float(acc_a.get("equity", 0)),
            "unrealized_pnl": round(unr_a, 2),
            "positions_count": len(pos_a),
        }
        total_portfolio += alpaca_summary["portfolio_value"]
        total_cash      += alpaca_summary["cash"]
        total_equity    += alpaca_summary["equity"]
        total_unrealized += unr_a
        all_positions.extend(pos_a)

    if binance_data:
        _, acc_b, pos_b = binance_data
        # Filter out Binance testnet pre-seeded tokens — only show symbols we traded
        if traded_symbols:
            pos_b_filtered = [p for p in pos_b if p["symbol"].upper() in traded_symbols]
        else:
            pos_b_filtered = []  # no open DB trades → nothing to show from Binance
        unr_b = sum(p["unrealized_pl"] for p in pos_b_filtered)
        binance_summary = {
            "broker": "binance_testnet",
            "portfolio_value": float(acc_b.get("portfolio_value", 0)),
            "cash": float(acc_b.get("cash", 0)),
            "equity": float(acc_b.get("equity", 0)),
            "unrealized_pnl": round(unr_b, 2),
            "positions_count": len(pos_b_filtered),
        }
        total_portfolio += binance_summary["portfolio_value"]
        total_cash      += binance_summary["cash"]
        total_equity    += binance_summary["equity"]
        total_unrealized += unr_b
        all_positions.extend(pos_b_filtered)

    # Determine which broker label to surface (prefer Alpaca)
    active_broker_name = "alpaca_paper" if alpaca_data else "binance_testnet"

    pnl_result = await db.execute(
        select(func.sum(Trade.pnl)).where(Trade.status == TradeStatus.closed)
    )
    total_realized_pnl = pnl_result.scalar() or 0.0

    return {
        "portfolio_value": round(total_portfolio, 2),
        "cash":            round(total_cash, 2),
        "equity":          round(total_equity, 2),
        "buying_power":    round(total_equity, 2),
        "positions":       all_positions,
        "total_unrealized_pnl": round(total_unrealized, 2),
        "total_realized_pnl":   round(total_realized_pnl, 2),
        "open_positions_count": len(all_positions),
        "active_broker":   active_broker_name,
        "brokers": {
            "alpaca":  alpaca_summary,
            "binance": binance_summary,
        },
    }


@router.get("/positions")
async def get_positions(db: AsyncSession = Depends(get_db)):
    broker = await get_broker(db)
    return broker.get_positions()


@router.get("/orders")
async def get_orders(db: AsyncSession = Depends(get_db)):
    broker = await get_broker(db)
    return broker.get_orders(limit=50)


@router.post("/close-all")
async def close_all_positions(db: AsyncSession = Depends(get_db)):
    """
    Emergency flatten: close every open broker position on ALL configured brokers,
    mark every open DB trade as manually closed, and pause all active strategies.
    Safe to call even if one broker is down — partial success is reported.
    """
    settings_res = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = settings_res.scalar_one_or_none()
    if not settings or (not settings.alpaca_api_key and not settings.binance_api_key):
        raise HTTPException(400, "No broker API keys configured")

    import asyncio
    from broker import create_alpaca_broker, create_broker
    from broker.binance import BinanceBroker
    from broker.alpaca import AlpacaBroker

    results: list[dict] = []

    def _close_broker(broker, label: str) -> list[dict]:
        out = []
        try:
            positions = broker.get_positions()
        except Exception as e:
            return [{"broker": label, "symbol": "ALL", "status": "error", "error": str(e)}]
        for pos in positions:
            sym = pos.get("symbol", "?")
            try:
                broker.close_position(sym)
                out.append({"broker": label, "symbol": sym, "status": "closed"})
            except Exception as e:
                out.append({"broker": label, "symbol": sym, "status": "error", "error": str(e)})
        return out

    loop = asyncio.get_event_loop()

    if settings.alpaca_api_key:
        try:
            alpaca = create_alpaca_broker(settings)
            res = await loop.run_in_executor(None, _close_broker, alpaca, "alpaca")
            results.extend(res)
        except Exception as e:
            results.append({"broker": "alpaca", "symbol": "ALL", "status": "error", "error": str(e)})

    if settings.binance_api_key:
        try:
            binance = create_broker(settings)
            if isinstance(binance, BinanceBroker):
                res = await loop.run_in_executor(None, _close_broker, binance, "binance")
                results.extend(res)
        except Exception as e:
            results.append({"broker": "binance", "symbol": "ALL", "status": "error", "error": str(e)})

    # Mark all open DB trades closed
    open_res = await db.execute(select(Trade).where(Trade.status == TradeStatus.open))
    open_trades = open_res.scalars().all()
    now = datetime.utcnow()
    for t in open_trades:
        t.status = TradeStatus.closed
        t.exit_reason = "manual_flatten"
        t.closed_at = now
        t.pnl = 0.0  # unknown at flatten time; will be reconciled on next portfolio sync
        t.notes = (t.notes or "") + " | EMERGENCY FLATTEN"

    # Pause all active strategies
    strats_res = await db.execute(select(Strategy).where(Strategy.status == StrategyStatus.active))
    active_strats = strats_res.scalars().all()
    for s in active_strats:
        s.status = StrategyStatus.paused

    # Clear scheduler jobs
    from scheduler import get_scheduler
    sched = get_scheduler()
    for job in sched.get_jobs():
        if job.id.startswith("strategy_"):
            job.remove()

    await db.commit()

    from routers.events import write_event
    closed_count = len([r for r in results if r["status"] == "closed"])
    await write_event(
        type="risk_alert", severity="warning",
        title=f"🚨 FLATTEN ALL executed — {closed_count} position(s) closed",
        body=f"{len(active_strats)} strategies paused · {len(open_trades)} DB trades marked closed · manual action",
        meta={"positions_closed": closed_count, "trades_marked": len(open_trades),
              "strategies_paused": len(active_strats)},
        db=db,
    )
    await db.commit()

    return {
        "positions_closed": closed_count,
        "db_trades_marked": len(open_trades),
        "strategies_paused": len(active_strats),
        "results": results,
    }


@router.get("/settings", response_model=BotSettingsSchema)
async def get_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = result.scalar_one_or_none()
    if not settings:
        return BotSettingsSchema()
    data = BotSettingsSchema.model_validate(settings, from_attributes=True)
    if data.alpaca_api_key:
        data.alpaca_api_key = data.alpaca_api_key[:8] + "..." + data.alpaca_api_key[-4:]
    if data.alpaca_secret_key:
        data.alpaca_secret_key = "••••••••••••••••"
    if data.binance_api_key:
        data.binance_api_key = data.binance_api_key[:8] + "..." + data.binance_api_key[-4:]
    if data.binance_secret_key:
        data.binance_secret_key = "••••••••••••••••"
    return data


@router.put("/settings")
async def save_settings(payload: BotSettingsSchema, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = BotSettings(id=1)
        db.add(settings)

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(settings, field, value)

    await db.commit()
    return {"saved": True}
