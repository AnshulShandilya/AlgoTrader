"""
Auto-Pilot Engine
==================
One call scans the full asset universe, picks the best tradeable assets on
daily bars, runs every strategy template against each one, scores the results,
and returns a ranked leaderboard — all without needing a broker connection.

Pipeline:
  1. Download daily OHLCV for STOCK_UNIVERSE + CRYPTO_UNIVERSE via yfinance
  2. Score each asset on volatility, momentum, volume, RSI, trend (daily bars)
  3. Keep top_n_assets by scanner score
  4. For each asset × strategy combination run a full backtest
  5. Score each backtest on a 0–100 composite KPI rubric
  6. Return ranked leaderboard + summary stats
"""
import asyncio
import logging
import math
import time
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger("autopilot")

# ── Live progress state (written during run, read by /autopilot/progress) ─────
_progress: dict = {
    "phase":             "idle",   # idle | downloading | scoring | backtesting | done
    "pct":               0,
    "message":           "",
    "current_item":      "",       # e.g. "AAPL · adaptive"
    "assets_done":       0,
    "assets_total":      0,
    "backtests_done":    0,
    "backtests_total":   0,
    "with_edge":         0,
    "elapsed_seconds":   0.0,
}

def get_progress() -> dict:
    return dict(_progress)

def _update(**kw):
    _progress.update(kw)

# ── Asset universe ─────────────────────────────────────────────────────────────

# Top 100 US stocks — mega/large-cap leaders across all sectors
US_STOCKS = [
    # Tech / Semiconductors
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD",
    "ORCL", "CRM", "ADBE", "QCOM", "TXN", "INTC", "AMAT", "LRCX", "KLAC",
    "MU", "SNPS", "CDNS", "MRVL", "NXPI", "ON", "TER",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "BLK", "C", "AXP", "SCHW", "SPGI",
    "MCO", "CME", "ICE", "V", "MA", "PYPL",
    # Healthcare / Pharma / Biotech
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "PFE", "TMO", "DHR", "ABT",
    "BMY", "AMGN", "GILD", "REGN", "ISRG", "MDT", "SYK", "ZTS", "VRTX",
    # Consumer
    "WMT", "COST", "HD", "MCD", "SBUX", "NKE", "TGT", "LOW", "BKNG",
    "PG", "KO", "PEP", "PM", "MO", "CL", "EL",
    # Energy
    "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "VLO",
    # Industrials / Diversified
    "GE", "HON", "CAT", "DE", "RTX", "ETN", "MMM", "EMR", "NOC", "LMT",
    # Telecoms / Media
    "VZ", "T", "NFLX", "DIS", "CMCSA",
    # Real Estate / Utilities
    "PLD", "NEE", "AMT",
    # High-growth / Disruptors
    "COIN", "MSTR", "PLTR", "HOOD", "SOFI", "RBLX", "SNAP",
    "UBER", "LYFT", "ABNB", "DASH", "RIVN", "LCID",
    # ETFs
    "SPY", "QQQ", "IWM", "XLF", "XLK", "GLD", "SLV",
]

# Top 50 UK stocks (FTSE 100) — yfinance uses .L suffix for LSE
UK_STOCKS = [
    # Energy / Mining
    "SHEL.L", "BP.L", "RIO.L", "AAL.L", "ANTO.L", "BHP.L", "GLEN.L", "EVR.L",
    # Financials / Banking
    "HSBA.L", "BARC.L", "LLOY.L", "NWG.L", "STAN.L", "PRU.L", "LSEG.L",
    "AV.L", "RSA.L", "ITRK.L", "EXPN.L",
    # Healthcare / Pharma
    "AZN.L", "GSK.L", "HIKM.L", "CRDA.L",
    # Consumer / Retail
    "ULVR.L", "DGE.L", "RKT.L", "ABF.L", "TSCO.L", "MKS.L",
    "CPG.L", "OCDO.L", "AUTO.L", "JD.L",
    # Telecoms / Media
    "VOD.L", "BT-A.L", "WPP.L", "ITV.L", "REL.L",
    # Industrials / Tech
    "BATS.L", "BA.L", "RR.L", "HLMA.L", "IMI.L", "WEIR.L",
    "SMT.L", "SDR.L", "FERG.L", "CRH.L",
    # Utilities / Real Estate
    "NG.L", "SSE.L", "SGRO.L", "LAND.L", "PSN.L",
]

# Top 20 commodities — yfinance futures tickers
COMMODITY_UNIVERSE = [
    "GC=F",   # Gold
    "SI=F",   # Silver
    "CL=F",   # Crude Oil WTI
    "BZ=F",   # Brent Crude
    "NG=F",   # Natural Gas
    "HG=F",   # Copper
    "PL=F",   # Platinum
    "PA=F",   # Palladium
    "ZW=F",   # Wheat
    "ZC=F",   # Corn
    "ZS=F",   # Soybeans
    "KC=F",   # Coffee
    "CT=F",   # Cotton
    "SB=F",   # Sugar
    "CC=F",   # Cocoa
    "RB=F",   # RBOB Gasoline
    "HO=F",   # Heating Oil
    "LE=F",   # Live Cattle
    "ZO=F",   # Oats
    "LBS=F",  # Lumber
]

# Combined stock universe (US + UK = ~150 symbols)
STOCK_UNIVERSE = US_STOCKS + UK_STOCKS

CRYPTO_UNIVERSE = [
    "BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "AVAX/USD",
    "LINK/USD", "LTC/USD", "DOT/USD", "ADA/USD", "XRP/USD",
    "MATIC/USD", "UNI/USD", "AAVE/USD",
]

# ── Strategy matrix (template → default parameters) ──────────────────────────
STRATEGY_MATRIX = [
    ("adaptive",     {"fast_ema": 9,  "slow_ema": 21, "rsi_period": 14,
                      "hurst_lookback": 60, "trend_hurst_min": 0.55,
                      "sideways_hurst_max": 0.45, "crash_vol_pct": 85.0}),
    ("scalping",     {"fast_ema": 9,  "slow_ema": 21, "rsi_period": 14,
                      "rsi_buy_level": 45, "rsi_sell_level": 55, "vol_multiplier": 1.1}),
    ("rsi",          {"rsi_period": 14, "oversold_level": 30, "overbought_level": 70}),
    ("ma_crossover", {"fast_ma": 20,  "slow_ma": 50,  "ma_type": "EMA"}),
    ("bollinger",    {"period": 20,   "std_dev": 2.0}),
    ("momentum",     {"lookback_period": 20, "momentum_threshold": 3.0, "volume_filter": False}),
    ("macd",         {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
]

DEFAULT_RISK = {"stop_loss_pct": 1.5, "take_profit_pct": 3.0, "position_size_pct": 10.0}

# ── In-process yfinance cache (1 h TTL) ──────────────────────────────────────
_data_cache: dict = {}
_CACHE_TTL = 3600


def _cache_get(key: str) -> Optional[pd.DataFrame]:
    if key in _data_cache:
        df, ts = _data_cache[key]
        if time.time() - ts < _CACHE_TTL:
            return df
        del _data_cache[key]
    return None


def _cache_set(key: str, df: pd.DataFrame):
    _data_cache[key] = (df, time.time())


def _fetch_yf(symbol: str, period: str) -> Optional[pd.DataFrame]:
    key = f"{symbol}:{period}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    import yfinance as yf
    yf_sym = symbol.upper().replace("/", "-")
    try:
        # Use Ticker.history() — per-instance, no shared global state between
        # concurrent calls.  yf.download() batches concurrent requests and
        # returns a MultiIndex DataFrame with ALL tickers mixed together.
        ticker = yf.Ticker(yf_sym)
        df = ticker.history(period=period, interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        # Flatten any remaining MultiIndex (shouldn't occur with Ticker.history)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        for alias in ("date", "index", "datetime"):
            if alias in df.columns:
                df = df.rename(columns={alias: "datetime"})
                break
        required = {"open", "high", "low", "close", "volume"}
        if required - set(df.columns):
            return None
        df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
        # Ensure all price/volume columns are plain floats (no residual Series)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        if df.empty:
            return None
        _cache_set(key, df)
        return df
    except Exception as e:
        log.warning(f"yfinance failed for {symbol}: {e}")
        return None


# ── Daily-bar asset scorer (20 indicators via shared engine) ─────────────────
def _score_asset_daily(symbol: str, df: pd.DataFrame) -> float:
    """Return composite 0-100 score from the 20-indicator engine."""
    from indicators import compute_all
    result = compute_all(df)
    return result.get("score", 0.0)


# ── Backtest result scorer (0–100) ────────────────────────────────────────────
def _score_backtest(r: dict, min_trades: int) -> float:
    if r.get("expectancy", 0) <= 0 or r.get("profit_factor", 0) < 1.0:
        return 0.0
    if r.get("total_trades", 0) < max(3, min_trades // 3):
        return 0.0

    pf  = r.get("profit_factor",    1.0)
    dd  = r.get("max_drawdown_pct", 50.0)
    sr  = r.get("sharpe_ratio",     0.0)
    n   = r.get("total_trades",     0)
    exp = r.get("expectancy",       0.0)

    pf_score  = min(35, (pf - 1.0) / 2.0 * 35)
    dd_score  = max(0, (10.0 - dd) / 10.0 * 25)
    sr_score  = max(0, min(20, sr / 2.0 * 20))
    n_score   = min(10, n / 30 * 8 + min(n / 100, 1.0) * 2)
    exp_score = min(10, math.log1p(exp / 50) * 5)

    score = pf_score + dd_score + sr_score + n_score + exp_score

    if all(r.get("kpi_pass", {}).values()):
        score *= 1.10

    return round(min(100.0, score), 1)


# ── Core pipeline ─────────────────────────────────────────────────────────────
async def run_autopilot(
    period: str = "2y",
    initial_capital: float = 100_000,
    position_size_pct: float = 10.0,
    top_n_assets: int = 10,
    min_trades: int = 10,
    include_stocks: bool = True,
    include_crypto: bool = True,
    include_commodities: bool = True,
    strategies: Optional[list] = None,
) -> dict:
    from backtester import BacktestEngine
    from cost_model import CostModel
    from strategies import get_strategy

    t0 = time.time()
    _update(phase="downloading", pct=0, message="Starting…",
            current_item="", assets_done=0, backtests_done=0,
            backtests_total=0, with_edge=0, elapsed_seconds=0.0)

    # ── 1. Build universe ──────────────────────────────────────────────────────
    universe = []
    if include_stocks:
        universe += STOCK_UNIVERSE
    if include_crypto:
        universe += CRYPTO_UNIVERSE
    if include_commodities:
        universe += COMMODITY_UNIVERSE
    if not universe:
        universe = STOCK_UNIVERSE + CRYPTO_UNIVERSE + COMMODITY_UNIVERSE

    strategy_matrix = [
        (t, p) for t, p in STRATEGY_MATRIX
        if strategies is None or t in strategies
    ]

    total_universe = len(universe)
    _update(assets_total=total_universe,
            message=f"Downloading {total_universe} assets…")

    log.info(f"Auto-pilot: {total_universe} assets × {len(strategy_matrix)} strategies "
             f"= {total_universe * len(strategy_matrix)} combinations")

    # ── 2. Download data concurrently (max 8 parallel) ────────────────────────
    sem = asyncio.Semaphore(8)
    assets_done_counter = 0

    async def fetch(sym):
        nonlocal assets_done_counter
        async with sem:
            result = sym, await asyncio.to_thread(_fetch_yf, sym, period)
        assets_done_counter += 1
        pct = int(assets_done_counter / total_universe * 30)   # 0–30%
        _update(
            phase="downloading",
            pct=pct,
            current_item=sym,
            assets_done=assets_done_counter,
            message=f"Downloading {sym} ({assets_done_counter}/{total_universe})",
            elapsed_seconds=round(time.time() - t0, 1),
        )
        return result

    fetch_results = await asyncio.gather(*[fetch(s) for s in universe])
    data_map = {sym: df for sym, df in fetch_results if df is not None}
    log.info(f"Downloaded {len(data_map)}/{total_universe} assets")

    # ── 3. Score each asset and pick top N ────────────────────────────────────
    _update(phase="scoring", pct=32, message="Scoring assets…", current_item="")
    from indicators import compute_all
    scored_assets = []
    for sym, df in data_map.items():
        ind = compute_all(df)
        sc  = ind.get("score", 0.0)
        if sc > 0:
            scored_assets.append((sym, sc, df, ind))

    scored_assets.sort(key=lambda x: x[1], reverse=True)
    top_assets = scored_assets[:top_n_assets]
    total_backtests = len(top_assets) * len(strategy_matrix)
    log.info(f"Top {len(top_assets)} assets: {[s for s,_,_,_ in top_assets]}")

    _update(phase="backtesting", pct=35,
            backtests_total=total_backtests, backtests_done=0,
            message=f"Running {total_backtests} backtests…")

    # Build lookup: symbol → (scanner_score, indicator_dict)
    asset_meta = {sym: (sc, ind) for sym, sc, _, ind in top_assets}

    # ── 4. Run all strategy × asset combinations ──────────────────────────────
    bt_done_counter = 0
    edge_counter = 0

    def _run_one(sym: str, df: pd.DataFrame, template: str, params: dict) -> Optional[dict]:
        nonlocal bt_done_counter, edge_counter
        try:
            is_crypto = "/" in sym or sym.upper().endswith(("-USD", "USDT"))
            cm = CostModel.crypto() if is_crypto else CostModel.equity()
            risk  = {**DEFAULT_RISK}
            strat = get_strategy(template, {**params}, risk, sym)
            engine = BacktestEngine(
                initial_capital=initial_capital,
                position_size_pct=position_size_pct,
                cost_model=cm,
            )
            r = engine.run(strat, df.copy(), sym)
            r["_template"] = template
            sc, ind = asset_meta.get(sym, (0, {}))
            r["_scanner_score"] = sc
            r["_indicators"] = ind
            if r.get("expectancy", 0) > 0 and r.get("profit_factor", 0) >= 1.0:
                edge_counter += 1
            return r
        except Exception as e:
            log.debug(f"Backtest failed {sym}/{template}: {e}")
            return None
        finally:
            bt_done_counter += 1
            pct = 35 + int(bt_done_counter / total_backtests * 63)   # 35–98%
            _update(
                pct=pct,
                backtests_done=bt_done_counter,
                with_edge=edge_counter,
                current_item=f"{sym} · {template}",
                message=f"Backtest {bt_done_counter}/{total_backtests}: {sym} × {template}",
                elapsed_seconds=round(time.time() - t0, 1),
            )

    tasks = [
        asyncio.to_thread(_run_one, sym, df, template, dict(params))
        for sym, _, df, _ in top_assets
        for template, params in strategy_matrix
    ]

    bsem = asyncio.Semaphore(12)

    async def run_bounded(coro):
        async with bsem:
            return await coro

    bt_results = await asyncio.gather(*[run_bounded(t) for t in tasks])
    bt_results = [r for r in bt_results if r is not None]
    log.info(f"Completed {len(bt_results)} backtests")

    # ── 5. Score, filter, rank ─────────────────────────────────────────────────
    leaderboard = []
    for r in bt_results:
        score = _score_backtest(r, min_trades)
        ind   = r.get("_indicators", {})
        leaderboard.append({
            "symbol":           r.get("symbol", "?"),
            "strategy":         r.get("_template", "?"),
            "strategy_name":    r.get("strategy_name", r.get("_template", "?")),
            "scanner_score":    r.get("_scanner_score", 0),
            "score":            score,
            "expectancy":       round(r.get("expectancy", 0), 2),
            "profit_factor":    round(r.get("profit_factor", 0), 3),
            "max_drawdown_pct": round(r.get("max_drawdown_pct", 0), 2),
            "sharpe_ratio":     round(r.get("sharpe_ratio", 0), 2),
            "total_return_pct": round(r.get("total_return_pct", 0), 2),
            "benchmark_return_pct": round(r.get("benchmark_return_pct", 0) or 0, 2),
            "alpha_pct":        round(r.get("alpha_pct", 0) or 0, 2),
            "total_trades":     r.get("total_trades", 0),
            "win_rate":         round(r.get("win_rate", 0), 1),
            "payoff_ratio":     round(r.get("payoff_ratio", 0), 2),
            "kpi_pass":         r.get("kpi_pass", {}),
            "all_pass":         all(r.get("kpi_pass", {}).values()),
            "start_date":       r.get("start_date", ""),
            "end_date":         r.get("end_date", ""),
            # 20-indicator snapshot at scan time
            "indicators": {
                "bias":                  ind.get("bias", "neutral"),
                "bullish_votes":         ind.get("bullish_votes", 0),
                "ema_trend":             ind.get("ema_trend", "neutral"),
                "ema_stack":             ind.get("ema_stack", 0),
                "adx":                   ind.get("adx", 0),
                "rsi":                   ind.get("rsi", 0),
                "macd_hist":             ind.get("macd_hist", 0),
                "stoch_k":               ind.get("stoch_k", 0),
                "williams_r":            ind.get("williams_r", 0),
                "cci":                   ind.get("cci", 0),
                "roc":                   ind.get("roc", 0),
                "obv_trend":             ind.get("obv_trend", "unknown"),
                "mfi":                   ind.get("mfi", 0),
                "volume_ratio":          ind.get("volume_ratio", 0),
                "atr_pct":               ind.get("atr_pct", 0),
                "bb_width":              ind.get("bb_width", 0),
                "donchian_pct":          ind.get("donchian_pct", 0),
                "supertrend_direction":  ind.get("supertrend_direction", "bearish"),
                "hurst":                 ind.get("hurst", 0.5),
                "market_regime":         ind.get("market_regime", "random"),
                "price_vs_vwap":         ind.get("price_vs_vwap", 0),
                "fib_nearest_level":     ind.get("fib_nearest_level", "0.500"),
                "fib_distance_pct":      ind.get("fib_distance_pct", 0),
                "ichimoku_above_cloud":  ind.get("ichimoku_above_cloud", False),
                "sar_direction":         ind.get("sar_direction", "bearish"),
            },
        })

    leaderboard.sort(key=lambda x: x["score"], reverse=True)
    for i, row in enumerate(leaderboard):
        row["rank"] = i + 1

    # ── 6. Summary stats ──────────────────────────────────────────────────────
    with_edge  = sum(1 for r in leaderboard if r["score"] > 0)
    all_pass   = sum(1 for r in leaderboard if r["all_pass"])

    elapsed = round(time.time() - t0, 1)
    log.info(f"Auto-pilot done in {elapsed}s — {with_edge} with edge, {all_pass} all-KPI-pass")

    _update(phase="done", pct=100,
            message=f"Done in {elapsed}s · {with_edge} with edge · {all_pass} all-KPI-pass",
            current_item="", elapsed_seconds=elapsed)

    return {
        "status":             "ok",
        "elapsed_seconds":    elapsed,
        "assets_scanned":     len(scored_assets),
        "assets_tested":      len(top_assets),
        "combinations_run":   len(bt_results),
        "combinations_with_edge": with_edge,
        "combinations_all_pass":  all_pass,
        "top_assets":         [{"symbol": s, "scanner_score": sc} for s, sc, _, __ in top_assets],
        "leaderboard":        leaderboard,
    }
