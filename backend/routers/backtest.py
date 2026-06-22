"""
Backtesting API endpoints.
  POST /backtest/run          — single-asset strategy backtest
  POST /backtest/run-pairs    — market-neutral pairs backtest
  GET  /backtest/find-pairs   — scan universe for cointegrated pairs
"""
import asyncio
import logging
import time
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Tuple

from cost_model import CostModel

router = APIRouter(prefix="/backtest", tags=["backtest"])
log = logging.getLogger("backtest_router")

# ── In-memory data cache (symbol+period → DataFrame, expires after 1 hour) ───
_cache: dict = {}          # key → (df, fetched_at)
_CACHE_TTL = 3600          # seconds


def _cache_get(key: str) -> Optional[pd.DataFrame]:
    if key in _cache:
        df, fetched_at = _cache[key]
        if time.time() - fetched_at < _CACHE_TTL:
            log.info(f"Cache hit: {key}")
            return df
        del _cache[key]
    return None


def _cache_set(key: str, df: pd.DataFrame):
    _cache[key] = (df, time.time())


# ── Request schemas ───────────────────────────────────────────────────────────

class CostModelReq(BaseModel):
    commission_bps: float = 10.0
    half_spread_bps: float = 5.0
    slippage_k: float = 0.1


class PositionSizerReq(BaseModel):
    """Phase 3: pluggable position sizer."""
    type: str = "fixed"                  # "fixed" | "vol_target" | "kelly"
    fraction: float = 0.10               # for "fixed" (and kelly fallback)
    annual_vol_target: float = 0.15      # for "vol_target"
    kelly_fraction: float = 0.5          # for "kelly"
    max_fraction: float = 0.20           # hard cap for vol_target and kelly


class RegimeGateReq(BaseModel):
    """Phase 4: regime gate configuration."""
    preset: str = "off"                  # "off" | "relaxed" | "standard" | "strict"
    hurst_min: float = 0.3
    hurst_max: float = 0.7
    vol_pct_max: float = 80.0
    lookback: int = 60


class SingleBacktestReq(BaseModel):
    symbol: str
    template: str
    parameters: dict
    risk_config: dict
    period: str = "2y"
    initial_capital: float = 100_000.0
    commission_pct: float = 0.1
    position_size_pct: float = 10.0
    cost_preset: str = "auto"
    cost_model: Optional[CostModelReq] = None
    position_sizer: Optional[PositionSizerReq] = None   # Phase 3
    regime_gate: Optional[RegimeGateReq] = None         # Phase 4


class PairsBacktestReq(BaseModel):
    symbol1: str
    symbol2: str
    lookback: int = 60
    entry_zscore: float = 2.0
    exit_zscore: float = 0.5
    stop_zscore: float = 3.5
    period: str = "2y"
    initial_capital: float = 100_000.0
    commission_pct: float = 0.1
    position_size_pct: float = 20.0
    cost_preset: str = "auto"
    cost_model: Optional[CostModelReq] = None
    use_kalman: bool = True              # Phase 5: Kalman hedge ratio
    kalman_delta: float = 1e-4
    kalman_R: float = 1e-2


class WalkForwardReq(BaseModel):
    symbol: str
    template: str
    param_grid: dict                                 # {"fast_ema": [5,9,13], ...}
    risk_config: dict = {"stop_loss_pct": 2.0, "take_profit_pct": 4.0}
    period: str = "5y"
    initial_capital: float = 100_000.0
    position_size_pct: float = 10.0
    is_bars: int = 252                               # in-sample window (bars)
    oos_bars: int = 63                               # out-of-sample window (bars, ~1 quarter)
    cost_preset: str = "auto"
    cost_model: Optional[CostModelReq] = None
    n_mc: int = 500                                  # Monte Carlo permutations


# ── Data fetcher (with cache) ─────────────────────────────────────────────────

def _fetch_yf(symbol: str, period: str) -> pd.DataFrame:
    """
    Download daily OHLCV from Yahoo Finance.
    Results are cached in memory for 1 hour — subsequent calls for the same
    symbol + period return instantly without hitting Yahoo Finance.
    """
    cache_key = f"{symbol}:{period}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    import yfinance as yf

    yf_sym = symbol.upper().replace("/", "-")
    t0 = time.time()
    df = yf.download(yf_sym, period=period, interval="1d", progress=False, auto_adjust=True)
    log.info(f"yfinance {symbol} {period}: {len(df)} rows in {time.time()-t0:.1f}s")

    if df.empty:
        raise ValueError(
            f"No data for '{symbol}'. "
            f"Use format BTC/USD, ETH/USD, AAPL, TSLA etc."
        )

    df = df.reset_index()

    # yfinance ≥1.x returns MultiIndex columns: ('Close','BTC-USD'), ('Date','')…
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]

    for alias in ("date", "index", "datetime"):
        if alias in df.columns:
            df = df.rename(columns={alias: "datetime"})
            break

    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns {missing} for {symbol}")

    df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
    _cache_set(cache_key, df)
    return df


# ── Cost model resolver ───────────────────────────────────────────────────────

def _resolve_cost_model(preset: str, symbol: str, override: Optional[CostModelReq]) -> CostModel:
    if override is not None:
        return CostModel(
            commission_bps=override.commission_bps,
            half_spread_bps=override.half_spread_bps,
            slippage_k=override.slippage_k,
        )
    if preset == "crypto":
        return CostModel.crypto()
    if preset == "equity":
        return CostModel.equity()
    if preset == "zero":
        return CostModel.zero()
    # "auto" or "legacy" → detect from symbol
    return CostModel.from_symbol(symbol)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/run")
async def run_backtest(req: SingleBacktestReq):
    """Backtest any single-asset strategy template over historical daily bars."""
    from backtester import BacktestEngine
    from strategies import get_strategy

    try:
        df = await asyncio.to_thread(_fetch_yf, req.symbol, req.period)
    except Exception as e:
        raise HTTPException(400, f"Data fetch failed: {e}")

    try:
        from position_sizer import make_sizer
        from regime import make_regime_gate

        cm     = _resolve_cost_model(req.cost_preset, req.symbol, req.cost_model)
        ann_N  = 365 if ("/" in req.symbol or req.symbol.upper().endswith(("-USD", "USDT"))) else 252

        sizer = make_sizer(
            sizer_type=req.position_sizer.type if req.position_sizer else "fixed",
            fraction=req.position_sizer.fraction if req.position_sizer else req.position_size_pct / 100,
            annual_vol_target=req.position_sizer.annual_vol_target if req.position_sizer else 0.15,
            max_fraction=req.position_sizer.max_fraction if req.position_sizer else 0.20,
            kelly_fraction=req.position_sizer.kelly_fraction if req.position_sizer else 0.5,
            ann_N=ann_N,
        ) if req.position_sizer else None

        rg = None
        if req.regime_gate:
            rg = make_regime_gate(
                preset=req.regime_gate.preset,
                hurst_min=req.regime_gate.hurst_min,
                hurst_max=req.regime_gate.hurst_max,
                vol_pct_max=req.regime_gate.vol_pct_max,
                lookback=req.regime_gate.lookback,
            )

        strategy = get_strategy(req.template, req.parameters, req.risk_config, req.symbol)
        engine   = BacktestEngine(
            initial_capital=req.initial_capital,
            position_size_pct=req.position_size_pct,
            cost_model=cm,
            position_sizer=sizer,
            regime_gate=rg,
        )
        result = await asyncio.to_thread(engine.run, strategy, df, req.symbol)
        result["cached"] = False
        return result
    except Exception as e:
        log.error(f"Backtest error: {e}", exc_info=True)
        raise HTTPException(400, str(e))


@router.post("/run-pairs")
async def run_pairs_backtest(req: PairsBacktestReq):
    """Backtest a market-neutral pairs trading strategy on two assets."""
    from backtester_pairs import PairsBacktestEngine
    from strategies.pairs import PairsStrategy

    try:
        df1, df2 = await asyncio.gather(
            asyncio.to_thread(_fetch_yf, req.symbol1, req.period),
            asyncio.to_thread(_fetch_yf, req.symbol2, req.period),
        )
    except Exception as e:
        raise HTTPException(400, f"Data fetch failed: {e}")

    try:
        cm       = _resolve_cost_model(req.cost_preset, req.symbol1, req.cost_model)
        strategy = PairsStrategy(
            symbol1=req.symbol1,
            symbol2=req.symbol2,
            lookback=req.lookback,
            entry_zscore=req.entry_zscore,
            exit_zscore=req.exit_zscore,
            stop_zscore=req.stop_zscore,
        )
        engine = PairsBacktestEngine(
            initial_capital=req.initial_capital,
            position_size_pct=req.position_size_pct,
            cost_model=cm,
            use_kalman=req.use_kalman,
            kalman_delta=req.kalman_delta,
            kalman_R=req.kalman_R,
        )
        result = await asyncio.to_thread(engine.run, strategy, df1, df2)
        return result
    except Exception as e:
        log.error(f"Pairs backtest error: {e}", exc_info=True)
        raise HTTPException(400, str(e))


@router.get("/find-pairs")
async def find_cointegrated_pairs(period: str = "1y"):
    """
    Scan the crypto universe for cointegrated pairs.
    Data is cached — first call downloads (~10s), subsequent calls are instant.
    """
    import traceback as tb
    from strategies.pairs import find_pairs
    try:
        results = await asyncio.to_thread(find_pairs, period)
        # Ensure all values are JSON-safe native Python types
        clean = []
        for r in results:
            clean.append({
                "symbol1":      str(r["symbol1"]),
                "symbol2":      str(r["symbol2"]),
                "pvalue":       float(r["pvalue"]),
                "correlation":  float(r["correlation"]),
                "cointegrated": bool(r["cointegrated"]),
                "n_bars":       int(r["n_bars"]),
            })
        return {
            "pairs":        clean,
            "total":        len(clean),
            "cointegrated": sum(1 for r in clean if r["cointegrated"]),
        }
    except Exception as e:
        detail = f"{type(e).__name__}: {e}\n{tb.format_exc()}"
        log.error(f"find_pairs error: {detail}")
        raise HTTPException(400, detail)


@router.post("/walk-forward")
async def walk_forward_backtest(req: WalkForwardReq):
    """
    Walk-forward validation: rolling IS optimisation → OOS test.

    Returns OOS-only aggregate metrics, Deflated Sharpe Ratio, and a
    Monte Carlo permutation p-value.  IS and OOS figures are never mixed.
    """
    from backtester import BacktestEngine
    from strategies import get_strategy
    from walk_forward import run_walk_forward

    try:
        df = await asyncio.to_thread(_fetch_yf, req.symbol, req.period)
    except Exception as e:
        raise HTTPException(400, f"Data fetch failed: {e}")

    def _strategy_factory(params, risk_config, symbol):
        return get_strategy(req.template, params, risk_config, symbol)

    def _engine_factory(capital, pos_size_pct, cm):
        return BacktestEngine(
            initial_capital=capital,
            position_size_pct=pos_size_pct,
            cost_model=cm,
        )

    try:
        from backtester import BacktestEngine as _BE  # detect ann_N from df
        ann_N = _BE(initial_capital=req.initial_capital,
                    position_size_pct=req.position_size_pct)._detect_N(df)
        cm = _resolve_cost_model(req.cost_preset, req.symbol, req.cost_model)

        result = await asyncio.to_thread(
            run_walk_forward,
            _strategy_factory,
            req.param_grid,
            req.risk_config,
            df,
            req.symbol,
            _engine_factory,
            req.is_bars,
            req.oos_bars,
            req.initial_capital,
            req.position_size_pct,
            cm,
            ann_N,
            req.n_mc,
        )
        return result
    except Exception as e:
        log.error(f"Walk-forward error: {e}", exc_info=True)
        raise HTTPException(400, str(e))


@router.delete("/cache")
async def clear_cache():
    """Force-clear the data cache (useful after market hours to get fresh data)."""
    count = len(_cache)
    _cache.clear()
    return {"cleared": count, "message": f"Cleared {count} cached datasets"}
