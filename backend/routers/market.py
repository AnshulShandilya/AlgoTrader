from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import BotSettings
import yfinance as yf
from datetime import datetime
from typing import Optional

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/bars/{symbol}")
async def get_bars(
    symbol: str,
    timeframe: str = Query("1Day"),
    limit: int = Query(100),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = result.scalar_one_or_none()

    df = None
    if settings and (settings.alpaca_api_key or settings.binance_api_key):
        from broker import create_broker
        try:
            broker = create_broker(settings)
            df = broker.get_bars(symbol.upper(), timeframe=timeframe, limit=limit)
            if df is None or df.empty:
                df = None
        except Exception:
            df = None

    if df is None:
        period_map = {"1Min": "5d", "5Min": "5d", "15Min": "5d", "1Hour": "60d", "1Day": "1y"}
        interval_map = {"1Min": "1m", "5Min": "5m", "15Min": "15m", "1Hour": "1h", "1Day": "1d"}
        yf_symbol = symbol.upper().replace("/", "-")
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period_map.get(timeframe, "1y"), interval=interval_map.get(timeframe, "1d"))
        df = df.reset_index().rename(columns={"Date": "datetime", "Datetime": "datetime", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        df = df[["datetime", "open", "high", "low", "close", "volume"]].tail(limit)

    records = []
    for _, row in df.iterrows():
        records.append({
            "timestamp": str(row["datetime"]),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": int(row["volume"]),
        })
    return records


@router.get("/quote/{symbol}")
async def get_quote(symbol: str):
    try:
        ticker = yf.Ticker(symbol.upper())
        info = ticker.fast_info
        hist = ticker.history(period="2d")
        if hist.empty:
            raise HTTPException(404, f"No data found for {symbol}")
        latest = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else hist.iloc[-1]
        change = float(latest["Close"]) - float(prev["Close"])
        change_pct = change / float(prev["Close"]) * 100
        return {
            "symbol": symbol.upper(),
            "price": round(float(latest["Close"]), 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "volume": int(latest["Volume"]),
            "high": round(float(latest["High"]), 2),
            "low": round(float(latest["Low"]), 2),
            "open": round(float(latest["Open"]), 2),
        }
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/watchlist")
async def get_watchlist_quotes(symbols: str = Query("AAPL,TSLA,NVDA,SPY,QQQ")):
    results = []
    for sym in symbols.split(","):
        sym = sym.strip().upper()
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="2d")
            if not hist.empty:
                latest = hist.iloc[-1]
                prev = hist.iloc[-2] if len(hist) > 1 else hist.iloc[-1]
                change_pct = (float(latest["Close"]) - float(prev["Close"])) / float(prev["Close"]) * 100
                results.append({
                    "symbol": sym,
                    "price": round(float(latest["Close"]), 2),
                    "change_pct": round(change_pct, 2),
                    "volume": int(latest["Volume"]),
                })
        except Exception:
            pass
    return results


@router.get("/universe-movers")
async def get_universe_movers(
    filter: str = Query("all", description="all|boom|penny|uk|us|crypto"),
    limit: int = Query(50),
):
    """Return cached universe screener results — boom stocks, penny movers, etc."""
    from universe import get_universe_cache
    cache = get_universe_cache()

    if filter == "boom":
        items = cache.get("boom_stocks", [])
    elif filter == "penny":
        items = cache.get("penny_movers", [])
    elif filter == "uk":
        items = [x for x in cache.get("all_movers", []) if x.get("asset_class") == "uk_stock"]
    elif filter == "us":
        items = [x for x in cache.get("all_movers", []) if x.get("asset_class") == "us_stock"]
    elif filter == "crypto":
        items = [x for x in cache.get("all_movers", []) if x.get("asset_class") == "crypto"]
    else:
        items = cache.get("all_movers", [])

    return {
        "updated_at":     cache.get("updated_at"),
        "total_screened": cache.get("total_screened", 0),
        "count":          len(items[:limit]),
        "items":          items[:limit],
        "error":          cache.get("error"),
    }


@router.get("/boom-check")
async def get_boom_check(symbols: str = Query(..., description="Comma-separated ticker symbols")):
    """Check whether specific symbols are currently booming (price spike + volume spike)."""
    from universe import boom_check
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        raise HTTPException(400, "No symbols provided")
    results = boom_check(sym_list)
    return {"symbols": results, "checked_at": datetime.utcnow().isoformat()}


@router.post("/universe-refresh")
async def trigger_universe_refresh():
    """Run the universe screener now (inside the server process so the cache updates)."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from universe import screen_universe
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = await loop.run_in_executor(pool, screen_universe)
    return {
        "ok": True,
        "screened":   result.get("total_screened", 0),
        "boom":       len(result.get("boom_stocks", [])),
        "penny":      len(result.get("penny_movers", [])),
        "candidates": len(result.get("filter_candidates_raw", [])),
        "updated_at": result.get("updated_at"),
    }


@router.get("/universe-candidates")
async def get_universe_candidates(
    asset_class: str = Query("all", description="all|us_stock|uk_stock|crypto|commodity"),
    sort_by: str = Query("pct_change", description="pct_change|vol_ratio|rsi|score"),
    direction: str = Query("all", description="all|up|down"),
    limit: int = Query(100),
):
    """Return scored universe candidates for the Market Scanner page."""
    from universe import get_universe_cache

    def _quick_score(item: dict) -> float:
        score = 0.0
        pct = abs(item.get("pct_change", 0))
        score += min(pct * 5, 30)
        vr = item.get("vol_ratio", 1.0)
        if vr > 1.0:
            score += min((vr - 1.0) * 15, 25)
        if item.get("rsi_rising"):
            score += 10
        rsi = item.get("rsi", 50)
        if 40 <= rsi <= 65:
            score += 5
        if item.get("above_ma50"):
            score += 15
        if item.get("at_20d_extreme"):
            score += 10
        if abs(item.get("gap_pct", 0)) > 1.0:
            score += 5
        return min(round(score, 1), 100)

    cache = get_universe_cache()
    items = list(cache.get("filter_candidates_raw", []))

    if asset_class != "all":
        items = [x for x in items if x.get("asset_class") == asset_class]

    if direction != "all":
        items = [x for x in items if x.get("direction") == direction]

    # Attach quick score
    for item in items:
        if "quick_score" not in item:
            item["quick_score"] = _quick_score(item)

    if sort_by == "vol_ratio":
        items.sort(key=lambda x: x.get("vol_ratio", 0), reverse=True)
    elif sort_by == "rsi":
        items.sort(key=lambda x: x.get("rsi", 50))
    elif sort_by == "score":
        items.sort(key=lambda x: x.get("quick_score", 0), reverse=True)
    else:  # pct_change default
        items.sort(key=lambda x: abs(x.get("pct_change", 0)), reverse=True)

    up_count = sum(1 for x in items if x.get("direction") == "up")
    return {
        "updated_at":     cache.get("updated_at"),
        "total_screened": cache.get("total_screened", 0),
        "total_candidates": len(items),
        "up_count":       up_count,
        "down_count":     len(items) - up_count,
        "items":          items[:limit],
        "error":          cache.get("error"),
    }


@router.get("/universe-stats")
async def get_universe_stats():
    """Summary stats about the loaded universe."""
    import json
    from pathlib import Path
    from universe import get_universe_cache
    path = Path(__file__).parent.parent / "data" / "universe_tickers.json"
    try:
        with open(path) as f:
            data = json.load(f)
        counts = {k: len(v) for k, v in data.items()}
        total  = sum(counts.values())
    except Exception:
        counts = {}
        total  = 0

    cache = get_universe_cache()
    return {
        "total_tickers":  total,
        "by_group":       counts,
        "last_screened":  cache.get("updated_at"),
        "boom_count":     len(cache.get("boom_stocks", [])),
        "penny_count":    len(cache.get("penny_movers", [])),
        "total_screened": cache.get("total_screened", 0),
    }
