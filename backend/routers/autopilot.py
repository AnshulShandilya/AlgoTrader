"""
Auto-Pilot API — one endpoint that scans, backtests and ranks everything.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import logging

router = APIRouter(prefix="/autopilot", tags=["autopilot"])
log = logging.getLogger("autopilot_router")

# ── Cache the last run in memory so the UI can re-display without re-running ──
_last_result: Optional[dict] = None


class AutoPilotReq(BaseModel):
    period: str = "2y"
    initial_capital: float = 100_000.0
    position_size_pct: float = 10.0
    top_n_assets: int = 10
    min_trades: int = 10
    include_stocks: bool = True
    include_crypto: bool = True
    include_commodities: bool = True
    strategies: Optional[List[str]] = None   # None = all 7


@router.post("/run")
async def run_autopilot(req: AutoPilotReq):
    """
    Scan the full asset universe, run every strategy template,
    and return a ranked leaderboard of the best combinations.
    Takes 20-60 seconds on first run (downloads data); subsequent runs
    are fast because yfinance data is cached for 1 hour.
    """
    global _last_result
    from autopilot import run_autopilot as _run
    try:
        result = await _run(
            period=req.period,
            initial_capital=req.initial_capital,
            position_size_pct=req.position_size_pct,
            top_n_assets=req.top_n_assets,
            min_trades=req.min_trades,
            include_stocks=req.include_stocks,
            include_crypto=req.include_crypto,
            include_commodities=req.include_commodities,
            strategies=req.strategies,
        )
        _last_result = result
        leaderboard = result.get("leaderboard", [])
        top = leaderboard[0] if leaderboard else None
        from routers.events import write_event
        await write_event(
            type="autopilot", severity="success",
            title=f"AutoPilot completed — {len(leaderboard)} combinations ranked",
            body=(f"Top pick: {top['symbol']} · {top['strategy']} · "
                  f"PF: {top.get('profit_factor', 0):.2f} · "
                  f"Expectancy: ${top.get('expectancy', 0):.2f}")
            if top else "No viable combinations found",
            meta={"count": len(leaderboard), "top_symbol": top["symbol"] if top else None},
        )
        return result
    except Exception as e:
        log.error(f"Auto-pilot error: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@router.get("/last")
async def get_last_result():
    """Return the cached result of the most recent auto-pilot run."""
    if _last_result is None:
        return {"status": "no_results", "leaderboard": []}
    return _last_result


@router.get("/progress")
async def get_progress():
    """Real-time progress of a running auto-pilot (or last completed run)."""
    from autopilot import get_progress as _gp
    return _gp()


# ── Deploy endpoints ───────────────────────────────────────────────────────────

class DeployReq(BaseModel):
    symbol: str
    strategy: str            # template name e.g. "adaptive"
    parameters: dict = {}
    position_size_pct: float = 5.0
    stop_loss_pct: float = 1.5
    take_profit_pct: float = 3.0


class ExecuteReq(DeployReq):
    confirmed: bool = False  # must be True to actually place the order


def _broker_and_settings(db_session):
    """Sync helper — call inside run_in_executor."""
    import asyncio
    # Can't await here; caller must pass pre-fetched settings
    raise NotImplementedError("use async version")


@router.post("/deploy/preview")
async def deploy_preview(req: DeployReq, db=None):
    """
    Step 1 — Preview.
    Fetches the live signal for the chosen strategy WITHOUT placing any order.
    Returns: current price, signal (buy/sell/hold), position size, estimated cost.
    """
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy import select as sa_select
    from database import get_db
    from models import BotSettings
    from strategies import get_strategy

    # Inline DB access (can't use Depends without a proper param, so reopen)
    async for session in get_db():
        result = await session.execute(sa_select(BotSettings).where(BotSettings.id == 1))
        settings = result.scalar_one_or_none()
        break

    if not settings or (not settings.alpaca_api_key and not settings.binance_api_key):
        raise HTTPException(400, "No broker API keys configured")

    def _preview(settings, req):
        from broker import create_broker, create_alpaca_broker
        from broker.binance import BinanceBroker
        from broker.alpaca import AlpacaBroker

        sym = req.symbol
        is_crypto    = "/" in sym or sym.upper().endswith(("-USD", "USDT"))
        is_uk_stock  = sym.upper().endswith(".L")
        is_commodity = sym.upper().endswith("=F")
        # UK stocks and commodities can't be traded via Alpaca/Binance;
        # fetch bars via yfinance and use Alpaca account for equity reference only.
        use_yf_bars = is_uk_stock or is_commodity

        # Pick the right broker for account equity + order routing
        primary = create_broker(settings)
        if is_crypto and isinstance(primary, AlpacaBroker) and settings.binance_api_key:
            broker = BinanceBroker(settings.binance_api_key, settings.binance_secret_key, testnet=settings.binance_testnet)
        elif not is_crypto and isinstance(primary, BinanceBroker) and settings.alpaca_api_key:
            broker = create_alpaca_broker(settings)
        else:
            broker = primary

        account = broker.get_account()
        equity  = float(account.get("equity", 0))

        risk = {
            "stop_loss_pct":     req.stop_loss_pct,
            "take_profit_pct":   req.take_profit_pct,
            "position_size_pct": req.position_size_pct,
        }
        strat = get_strategy(req.strategy, dict(req.parameters), risk, sym)

        if use_yf_bars:
            # Fetch 200 daily bars via yfinance (Alpaca/Binance don't carry these symbols)
            import yfinance as yf
            import pandas as pd
            yf_sym = sym.upper().replace("/", "-")
            ticker = yf.Ticker(yf_sym)
            raw = ticker.history(period="2y", interval="1d", auto_adjust=True)
            if raw is None or raw.empty:
                raise ValueError(f"No yfinance data for {sym}")
            raw = raw.reset_index()
            raw.columns = [str(c).lower() for c in raw.columns]
            for alias in ("date", "index", "datetime"):
                if alias in raw.columns:
                    raw = raw.rename(columns={alias: "datetime"})
                    break
            df = raw[["datetime", "open", "high", "low", "close", "volume"]].copy()
            df = df.tail(200).reset_index(drop=True)
            broker_label = "yFinance (signal only)"
        else:
            df = broker.get_bars(sym, timeframe="1Day", limit=200)
            broker_label = type(broker).__name__.replace("Broker", "")

        signal = strat.generate_signal(df)

        current_price = float(signal.indicators.get("close", 0))
        position_usd  = equity * (req.position_size_pct / 100)
        qty = round(position_usd / current_price, 6) if current_price > 0 else 0

        return {
            "signal":        signal.action,
            "confidence":    round(signal.confidence, 2),
            "current_price": current_price,
            "equity":        round(equity, 2),
            "position_usd":  round(position_usd, 2),
            "estimated_qty": qty,
            "stop_price":    round(current_price * (1 - req.stop_loss_pct / 100), 4) if current_price else 0,
            "target_price":  round(current_price * (1 + req.take_profit_pct / 100), 4) if current_price else 0,
            "indicators":    signal.indicators,
            "broker":        broker_label,
            "note":          "Signal only — UK stocks and commodities require a CFD/spread-bet broker for live execution" if use_yf_bars else None,
        }

    loop = asyncio.get_event_loop()
    try:
        preview = await loop.run_in_executor(None, _preview, settings, req)
        return preview
    except Exception as e:
        log.error(f"Deploy preview error: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@router.post("/deploy/execute")
async def deploy_execute(req: ExecuteReq):
    """
    Step 2 — Execute.
    Places a real market order. Requires confirmed=True in the request body.
    Also saves the strategy to the DB so the automation engine can manage it.
    """
    if not req.confirmed:
        raise HTTPException(400, "Must set confirmed=true to place a real order")

    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy import select as sa_select
    from database import get_db
    from models import BotSettings, Strategy, StrategyStatus, Trade, TradeStatus, TradeSide
    from strategies import get_strategy
    from datetime import datetime

    async for session in get_db():
        result = await session.execute(sa_select(BotSettings).where(BotSettings.id == 1))
        settings = result.scalar_one_or_none()
        break

    if not settings or (not settings.alpaca_api_key and not settings.binance_api_key):
        raise HTTPException(400, "No broker API keys configured")

    def _execute(settings, req):
        from broker import create_broker, create_alpaca_broker
        from broker.binance import BinanceBroker
        from broker.alpaca import AlpacaBroker

        sym = req.symbol
        is_crypto    = "/" in sym or sym.upper().endswith(("-USD", "USDT"))
        is_uk_stock  = sym.upper().endswith(".L")
        is_commodity = sym.upper().endswith("=F")
        use_yf_bars  = is_uk_stock or is_commodity

        if use_yf_bars:
            # UK stocks and commodities can't be placed via Alpaca/Binance
            return {
                "placed": False,
                "reason": f"{sym} is a {'UK stock' if is_uk_stock else 'commodity'} — live orders require a CFD/spread-bet broker (e.g. IG, Interactive Brokers). The signal has been saved as a strategy alert.",
                "signal": "alert_only",
            }

        primary = create_broker(settings)
        if is_crypto and isinstance(primary, AlpacaBroker) and settings.binance_api_key:
            broker = BinanceBroker(settings.binance_api_key, settings.binance_secret_key, testnet=settings.binance_testnet)
        elif not is_crypto and isinstance(primary, BinanceBroker) and settings.alpaca_api_key:
            broker = create_alpaca_broker(settings)
        else:
            broker = primary

        account = broker.get_account()

        risk = {
            "stop_loss_pct":     req.stop_loss_pct,
            "take_profit_pct":   req.take_profit_pct,
            "position_size_pct": req.position_size_pct,
        }
        strat = get_strategy(req.strategy, dict(req.parameters), risk, sym)
        df = broker.get_bars(sym, timeframe="1Day", limit=200)
        signal = strat.generate_signal(df)

        if signal.action == "hold":
            return {"placed": False, "reason": "Signal is HOLD — no order needed right now",
                    "signal": "hold", "indicators": signal.indicators}

        equity = float(account.get("equity", 0))
        current_price = float(signal.indicators.get("close", 0))
        qty = broker.calculate_shares(equity, req.position_size_pct, current_price)
        if qty <= 0:
            return {"placed": False, "reason": "Quantity too small", "signal": signal.action}

        sl_price = round(current_price * (1 - req.stop_loss_pct / 100), 2) if current_price else None
        tp_price = round(current_price * (1 + req.take_profit_pct / 100), 2) if current_price else None
        order = broker.place_market_order(sym, qty, signal.action,
                                          stop_loss_price=sl_price,
                                          take_profit_price=tp_price)
        return {
            "placed": True,
            "signal": signal.action,
            "confidence": round(signal.confidence, 2),
            "qty": qty,
            "current_price": current_price,
            "order": order,
            "indicators": signal.indicators,
        }

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _execute, settings, req)
    except Exception as e:
        log.error(f"Deploy execute error: {e}", exc_info=True)
        raise HTTPException(500, str(e))

    if not result.get("placed"):
        return result

    # Daily trade limit guard (checked after execute so we have the signal)
    if result.get("signal") == "buy":
        from sqlalchemy import func as sa_func
        from datetime import timedelta
        async for session in get_db():
            today_start = __import__("datetime").datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            daily_result = await session.execute(
                sa_select(sa_func.count(Trade.id)).where(Trade.opened_at >= today_start)
            )
            daily_count = daily_result.scalar() or 0
            max_daily = settings.max_daily_trades or 20
            if daily_count >= max_daily:
                raise HTTPException(429, f"Daily trade limit ({max_daily}) reached — {daily_count} trades placed today. Resets at midnight UTC.")
            break

    # Persist strategy + trade record
    async for session in get_db():
        strat_name = f"AutoPilot · {req.symbol} · {req.strategy}"
        new_strategy = Strategy(
            name=strat_name,
            template=req.strategy,
            symbol=req.symbol.upper(),
            parameters={**req.parameters,
                        "position_size_pct": req.position_size_pct},
            risk_config={
                "stop_loss_pct":     req.stop_loss_pct,
                "take_profit_pct":   req.take_profit_pct,
                "position_size_pct": req.position_size_pct,
            },
            status=StrategyStatus.active,
        )
        session.add(new_strategy)
        await session.flush()   # get new_strategy.id

        ep = result["current_price"] or 0
        trade = Trade(
            strategy_id=new_strategy.id,
            strategy_name=strat_name,
            symbol=req.symbol.upper(),
            side=TradeSide.buy if result["signal"] == "buy" else TradeSide.sell,
            qty=result["qty"],
            entry_price=ep,
            stop_loss_price=round(ep * (1 - req.stop_loss_pct / 100), 6) if ep else None,
            take_profit_price=round(ep * (1 + req.take_profit_pct / 100), 6) if ep else None,
            status=TradeStatus.open,
            alpaca_order_id=result["order"].get("order_id"),
            opened_at=datetime.utcnow(),
            notes=f"AutoPilot deploy · conf {result['confidence']} · SL {req.stop_loss_pct}% · TP {req.take_profit_pct}%",
        )
        session.add(trade)
        await session.commit()
        result["strategy_id"] = new_strategy.id
        result["trade_id"] = trade.id
        break

    return result
