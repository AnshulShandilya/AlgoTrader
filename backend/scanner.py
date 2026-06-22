"""
Market scanner — scores a universe of assets on volatility, volume,
momentum and trend quality. Returns top N candidates for scalping.
"""
import logging
import asyncio
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List
import pandas as pd
import numpy as np

log = logging.getLogger("scanner")

# Universe to scan — crypto is always live; stocks only during market hours
CRYPTO_UNIVERSE = [
    "BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "AVAX/USD",
    "LINK/USD", "LTC/USD", "BCH/USD", "DOT/USD", "MATIC/USD",
    "UNI/USD", "AAVE/USD", "ATOM/USD", "ADA/USD", "XRP/USD",
]

STOCK_UNIVERSE = [
    "AAPL", "TSLA", "NVDA", "AMZN", "META",
    "GOOGL", "AMD", "MSFT", "SPY", "QQQ",
    "NFLX", "COIN", "MSTR", "PLTR", "HOOD",
]


@dataclass
class AssetScore:
    symbol: str
    score: float
    # pillar sub-scores
    trend_score: float = 0.0
    momentum_score: float = 0.0
    volume_score: float = 0.0
    volatility_score: float = 0.0
    breakout_score: float = 0.0
    structure_score: float = 0.0
    # individual indicators
    atr_pct: float = 0.0
    volume_ratio: float = 0.0
    momentum_pct: float = 0.0   # ROC(10)
    rsi: float = 0.0
    macd_hist: float = 0.0
    bb_width: float = 0.0
    adx: float = 0.0
    stoch_k: float = 0.0
    stoch_d: float = 0.0
    williams_r: float = 0.0
    cci: float = 0.0
    obv_trend: str = "unknown"
    mfi: float = 0.0
    ema_trend: str = "neutral"
    ema_stack: int = 0
    ichimoku_above_cloud: bool = False
    supertrend_direction: str = "bearish"
    sar_direction: str = "bearish"
    donchian_pct: float = 0.5
    hurst: float = 0.5
    market_regime: str = "random"
    price_vs_vwap: float = 0.0
    fib_nearest_level: str = "0.500"
    fib_distance_pct: float = 0.0
    bias: str = "neutral"
    bullish_votes: int = 0
    bearish_votes: int = 0
    trend: str = "neutral"    # legacy alias → ema_trend
    reason: str = ""
    bars: Optional[pd.DataFrame] = field(default=None, repr=False)


def _score_asset(symbol: str, df: pd.DataFrame) -> Optional[AssetScore]:
    """Score an asset using the full 20-indicator professional engine."""
    if df is None or len(df) < 30:
        return None

    from indicators import compute_all
    ind = compute_all(df)
    if "error" in ind:
        return None

    # Build human-readable reason string
    reasons = []
    if ind.get("atr_pct", 0) > 0.5:
        reasons.append(f"ATR {ind['atr_pct']:.2f}%")
    if ind.get("volume_ratio", 0) > 1.3:
        reasons.append(f"vol {ind['volume_ratio']:.1f}x avg")
    if abs(ind.get("roc", 0)) > 1.0:
        reasons.append(f"ROC {ind.get('roc', 0):+.1f}%")
    reasons.append(ind.get("ema_trend", "neutral"))
    reasons.append(ind.get("market_regime", ""))
    if ind.get("bullish_votes", 0) >= 7:
        reasons.append(f"🟢 {ind['bullish_votes']}/10 bull")
    elif ind.get("bearish_votes", 0) >= 7:
        reasons.append(f"🔴 {ind['bearish_votes']}/10 bear")

    return AssetScore(
        symbol=symbol,
        score=round(ind.get("score", 0.0), 1),
        trend_score=round(ind.get("trend_score", 0.0), 1),
        momentum_score=round(ind.get("momentum_score", 0.0), 1),
        volume_score=round(ind.get("volume_score", 0.0), 1),
        volatility_score=round(ind.get("volatility_score", 0.0), 1),
        breakout_score=round(ind.get("breakout_score", 0.0), 1),
        structure_score=round(ind.get("structure_score", 0.0), 1),
        atr_pct=round(ind.get("atr_pct", 0.0), 3),
        volume_ratio=round(ind.get("volume_ratio", 0.0), 2),
        momentum_pct=round(ind.get("roc", 0.0), 2),
        rsi=round(ind.get("rsi", 0.0), 1),
        macd_hist=round(ind.get("macd_hist", 0.0), 4),
        bb_width=round(ind.get("bb_width", 0.0), 2),
        adx=round(ind.get("adx", 0.0), 1),
        stoch_k=round(ind.get("stoch_k", 0.0), 1),
        stoch_d=round(ind.get("stoch_d", 0.0), 1),
        williams_r=round(ind.get("williams_r", 0.0), 1),
        cci=round(ind.get("cci", 0.0), 1),
        obv_trend=ind.get("obv_trend", "unknown"),
        mfi=round(ind.get("mfi", 0.0), 1),
        ema_trend=ind.get("ema_trend", "neutral"),
        ema_stack=int(ind.get("ema_stack", 0)),
        ichimoku_above_cloud=bool(ind.get("ichimoku_above_cloud", False)),
        supertrend_direction=ind.get("supertrend_direction", "bearish"),
        sar_direction=ind.get("sar_direction", "bearish"),
        donchian_pct=round(ind.get("donchian_pct", 0.5), 3),
        hurst=round(ind.get("hurst", 0.5), 3),
        market_regime=ind.get("market_regime", "random"),
        price_vs_vwap=round(ind.get("price_vs_vwap", 0.0), 2),
        fib_nearest_level=str(ind.get("fib_nearest_level", "0.500")),
        fib_distance_pct=round(ind.get("fib_distance_pct", 0.0), 2),
        bias=ind.get("bias", "neutral"),
        bullish_votes=int(ind.get("bullish_votes", 0)),
        bearish_votes=int(ind.get("bearish_votes", 0)),
        trend=ind.get("ema_trend", "neutral"),
        reason=" · ".join(r for r in reasons if r),
        bars=df,
    )


async def scan_market(broker, top_n: int = 10, include_stocks: bool = False) -> List[AssetScore]:
    """Fetch bars for all universe assets, score them, return top N."""
    universe = CRYPTO_UNIVERSE.copy()
    if include_stocks:
        universe += STOCK_UNIVERSE

    scored: list[AssetScore] = []

    for symbol in universe:
        try:
            df = await asyncio.to_thread(broker.get_bars, symbol, "5Min", 100)
            result = _score_asset(symbol, df)
            if result:
                scored.append(result)
                log.debug(f"Scored {symbol}: {result.score:.1f}")
        except Exception as e:
            log.warning(f"Skip {symbol}: {e}")

    scored.sort(key=lambda x: x.score, reverse=True)
    top = scored[:top_n]

    log.info(f"Scanner complete — top {len(top)}: {[s.symbol for s in top]}")
    return top


def format_scan_results(results: List[AssetScore]) -> List[dict]:
    return [
        {
            "rank":                 i + 1,
            "symbol":               r.symbol,
            # composite
            "score":                r.score,
            "bias":                 r.bias,
            "bullish_votes":        r.bullish_votes,
            "bearish_votes":        r.bearish_votes,
            "reason":               r.reason,
            # pillar sub-scores
            "trend_score":          r.trend_score,
            "momentum_score":       r.momentum_score,
            "volume_score":         r.volume_score,
            "volatility_score":     r.volatility_score,
            "breakout_score":       r.breakout_score,
            "structure_score":      r.structure_score,
            # Trend indicators
            "ema_trend":            r.ema_trend,
            "ema_stack":            r.ema_stack,
            "adx":                  r.adx,
            "ichimoku_above_cloud": r.ichimoku_above_cloud,
            "supertrend_direction": r.supertrend_direction,
            "sar_direction":        r.sar_direction,
            # Momentum indicators
            "rsi":                  r.rsi,
            "macd_hist":            r.macd_hist,
            "stoch_k":              r.stoch_k,
            "stoch_d":              r.stoch_d,
            "williams_r":           r.williams_r,
            "cci":                  r.cci,
            "momentum_pct":         r.momentum_pct,   # ROC(10)
            # Volume / flow indicators
            "obv_trend":            r.obv_trend,
            "mfi":                  r.mfi,
            "volume_ratio":         r.volume_ratio,
            # Volatility indicators
            "atr_pct":              r.atr_pct,
            "bb_width":             r.bb_width,
            # Breakout indicators
            "donchian_pct":         r.donchian_pct,
            "fib_nearest_level":    r.fib_nearest_level,
            "fib_distance_pct":     r.fib_distance_pct,
            # Market structure
            "hurst":                r.hurst,
            "market_regime":        r.market_regime,
            "price_vs_vwap":        r.price_vs_vwap,
            # legacy
            "trend":                r.trend,
        }
        for i, r in enumerate(results)
    ]
