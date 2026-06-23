from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Strategy, BotSettings
from schemas import StrategyCreate, StrategyUpdate, StrategyOut
from strategies import STRATEGY_TEMPLATES, get_strategy
from datetime import datetime
import pandas as pd

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _pick_broker(settings, symbol: str):
    """Route to the right broker based on asset class."""
    from broker import create_broker, create_alpaca_broker
    from broker.binance import BinanceBroker
    from broker.alpaca import AlpacaBroker
    sym = symbol.upper()
    is_crypto    = "/" in sym or sym.endswith(("-USD", "USDT"))
    is_uk_stock  = sym.endswith(".L")
    is_commodity = sym.endswith("=F")
    primary = create_broker(settings)
    if is_crypto and isinstance(primary, AlpacaBroker) and settings.binance_api_key:
        from broker.binance import BinanceBroker as BB
        return BB(settings.binance_api_key, settings.binance_secret_key,
                  testnet=settings.binance_testnet)
    if not is_crypto and isinstance(primary, BinanceBroker) and settings.alpaca_api_key:
        return create_alpaca_broker(settings)
    return primary


def _fetch_bars(broker, symbol: str, timeframe: str, settings):
    """Fetch bars via yfinance for non-Alpaca symbols, broker for US equities."""
    sym = symbol.upper()
    use_yfinance = (
        sym.endswith(".L")      # UK LSE stocks (pence-quoted)
        or sym.endswith("=F")   # Commodities (gold GC=F, oil CL=F)
        or sym.endswith("=X")   # Forex pairs (EURUSD=X, GBPUSD=X)
        or sym.endswith("-USD")  # Crypto (BTC-USD, ETH-USD, SOL-USD)
    )
    if use_yfinance:
        import yfinance as yf
        yf_sym = sym.replace("/", "-")
        _YF_INTERVAL = {
            "1Min":  ("2m",  "5d"),
            "5Min":  ("5m",  "60d"),
            "15Min": ("15m", "60d"),
            "1Hour": ("60m", "60d"),
            "4Hour": ("1h",  "730d"),
            "1Day":  ("1d",  "2y"),
        }
        _yf_iv, _yf_per = _YF_INTERVAL.get(timeframe, ("5m", "60d"))
        raw = yf.Ticker(yf_sym).history(period=_yf_per, interval=_yf_iv, auto_adjust=True)
        if raw is None or raw.empty:
            # Intraday may not be available for some symbols — fall back to daily
            raw = yf.Ticker(yf_sym).history(period="2y", interval="1d", auto_adjust=True)
        if raw is None or raw.empty:
            raise ValueError(f"No market data available for {symbol}")
        raw = raw.reset_index()
        raw.columns = [str(c).lower() for c in raw.columns]
        for alias in ("date", "index", "datetime", "timestamp"):
            if alias in raw.columns:
                raw = raw.rename(columns={alias: "datetime"})
                break
        return raw[["datetime", "open", "high", "low", "close", "volume"]].tail(200).reset_index(drop=True)
    return broker.get_bars(symbol, timeframe=timeframe, limit=200)


@router.get("/templates")
async def get_templates():
    return {
        k: {
            "name": v["name"],
            "description": v["description"],
            "parameters": v["parameters"],
            "risk_config": v["risk_config"],
        }
        for k, v in STRATEGY_TEMPLATES.items()
    }


@router.get("/", response_model=list[StrategyOut])
async def list_strategies(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Strategy).order_by(Strategy.created_at.desc()))
    strategies = result.scalars().all()
    return [StrategyOut.from_orm_with_winrate(s) for s in strategies]


@router.post("/", response_model=StrategyOut)
async def create_strategy(payload: StrategyCreate, db: AsyncSession = Depends(get_db)):
    if payload.template not in STRATEGY_TEMPLATES:
        raise HTTPException(400, f"Unknown template: {payload.template}")
    strategy = Strategy(**payload.model_dump())
    db.add(strategy)
    await db.commit()
    await db.refresh(strategy)
    return StrategyOut.from_orm_with_winrate(strategy)


@router.get("/{strategy_id}", response_model=StrategyOut)
async def get_strategy_by_id(strategy_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(404, "Strategy not found")
    return StrategyOut.from_orm_with_winrate(strategy)


@router.patch("/{strategy_id}", response_model=StrategyOut)
async def update_strategy(strategy_id: int, payload: StrategyUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(404, "Strategy not found")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(strategy, field, value)
    strategy.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(strategy)
    # Sync scheduler whenever status changes
    from scheduler import reload_jobs
    await reload_jobs()
    return StrategyOut.from_orm_with_winrate(strategy)


@router.delete("/{strategy_id}")
async def delete_strategy(strategy_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(404, "Strategy not found")
    await db.delete(strategy)
    await db.commit()
    return {"deleted": True}


@router.post("/{strategy_id}/run-signal")
async def run_strategy_signal(strategy_id: int, db: AsyncSession = Depends(get_db)):
    """Manually run a strategy and get the current signal without placing an order."""
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy_row = result.scalar_one_or_none()
    if not strategy_row:
        raise HTTPException(404, "Strategy not found")

    settings_result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = settings_result.scalar_one_or_none()
    if not settings or (not settings.alpaca_api_key and not settings.binance_api_key):
        raise HTTPException(400, "No broker API keys configured. Go to Settings.")

    import asyncio
    def _run(settings, strategy_row):
        timeframe = strategy_row.parameters.get("timeframe", "1Day")
        broker = _pick_broker(settings, strategy_row.symbol)
        df = _fetch_bars(broker, strategy_row.symbol, timeframe, settings)
        strat = get_strategy(strategy_row.template, strategy_row.parameters,
                             strategy_row.risk_config, strategy_row.symbol)
        return strat.generate_signal(df)

    try:
        loop = asyncio.get_event_loop()
        signal = await loop.run_in_executor(None, _run, settings, strategy_row)
    except Exception as e:
        raise HTTPException(500, str(e))

    return {
        "strategy_id": strategy_id,
        "strategy_name": strategy_row.name,
        "symbol": signal.symbol,
        "signal": signal.action,
        "confidence": signal.confidence,
        "indicators": signal.indicators,
        "timestamp": signal.timestamp,
    }
