"""
Adaptive Regime-Switching Strategy
====================================
Detects the current market regime at each bar and applies the optimal
signal logic for that regime — no manual switching required.

Regime classification (uses closes up to bar i, no lookahead):
  CRASH    vol_pct_rank > crash_vol_pct (extreme volatility spike)
  TRENDING Hurst > trend_hurst_min      (persistent, directional price)
  SIDEWAYS Hurst < sideways_hurst_max   (mean-reverting, oscillating)
  NEUTRAL  anything in between          (ambiguous — use conservative EMA)

Signal logic per regime:
  TRENDING  — EMA crossover + RSI momentum confirmation (long AND short)
  SIDEWAYS  — RSI extremes + Bollinger band reversion (buy 30, sell 70)
  CRASH     — short-only with tight confirmation, or flat if disabled
  NEUTRAL   — EMA crossover only (no RSI filter, moderate frequency)

This design handles all four market conditions:
  Bull market  → TRENDING long signals
  Bear market  → TRENDING short signals
  Sideways     → SIDEWAYS mean-reversion
  Crash        → CRASH short or flat
"""
import numpy as np
import pandas as pd
from datetime import datetime
from .base import BaseStrategy, Signal


# ── Regime constants ──────────────────────────────────────────────────────────
REGIME_TRENDING  = "trending"
REGIME_SIDEWAYS  = "sideways"
REGIME_CRASH     = "crash"
REGIME_NEUTRAL   = "neutral"


# ── Inline Hurst estimator (lag-variance method, no circular import) ──────────
def _hurst(prices: np.ndarray) -> float:
    if len(prices) < 20:
        return 0.5
    lags, stds = [], []
    for lag in range(2, min(21, len(prices) // 2)):
        # lag-period differences (NOT n-th order diff)
        diff = prices[lag:] - prices[:-lag]
        if len(diff) < 4:
            continue
        s = np.std(diff)
        if s > 0:
            lags.append(np.log(lag))
            stds.append(np.log(s))
    if len(lags) < 3:
        return 0.5
    slope, _ = np.polyfit(lags, stds, 1)
    return float(np.clip(slope, 0.0, 1.0))


def _vol_percentile(rets: np.ndarray, window: int = 10, lookback: int = 60) -> float:
    """Return the current rolling-vol as a percentile of its own history."""
    if len(rets) < window + 2:
        return 50.0
    roll = np.array([
        rets[i - window:i].std()
        for i in range(window, len(rets) + 1)
    ])
    if len(roll) < 2:
        return 50.0
    cur = roll[-1]
    hist = roll[-lookback:] if len(roll) >= lookback else roll
    return float(np.mean(hist <= cur) * 100)


def _classify_regime(
    closes: np.ndarray,
    hurst_lookback: int,
    vol_lookback: int,
    trend_hurst_min: float,
    sideways_hurst_max: float,
    crash_vol_pct: float,
) -> str:
    rets = np.diff(closes) / (closes[:-1] + 1e-10)
    vol_pct = _vol_percentile(rets, window=10, lookback=vol_lookback)
    if vol_pct >= crash_vol_pct:
        return REGIME_CRASH
    window = closes[-hurst_lookback:] if len(closes) >= hurst_lookback else closes
    H = _hurst(window)
    if H >= trend_hurst_min:
        return REGIME_TRENDING
    if H <= sideways_hurst_max:
        return REGIME_SIDEWAYS
    return REGIME_NEUTRAL


# ── Indicator helpers ─────────────────────────────────────────────────────────
def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _bollinger(close: pd.Series, period: int = 20, std_dev: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return mid + std_dev * std, mid - std_dev * std  # upper, lower


# ── Strategy ──────────────────────────────────────────────────────────────────
class AdaptiveStrategy(BaseStrategy):
    """
    Regime-switching strategy that auto-detects bull / bear / sideways / crash
    and applies the correct entry logic for each.
    """

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        # ── Parameters ────────────────────────────────────────────────────────
        fast_ema         = int(self.parameters.get("fast_ema", 9))
        slow_ema         = int(self.parameters.get("slow_ema", 21))
        rsi_period       = int(self.parameters.get("rsi_period", 14))
        hurst_lookback   = int(self.parameters.get("hurst_lookback", 60))
        vol_lookback     = int(self.parameters.get("vol_lookback", 60))
        trend_hurst_min  = float(self.parameters.get("trend_hurst_min", 0.55))
        sideways_hurst_max = float(self.parameters.get("sideways_hurst_max", 0.45))
        crash_vol_pct    = float(self.parameters.get("crash_vol_pct", 85.0))
        rsi_oversold     = float(self.parameters.get("rsi_oversold", 30.0))
        rsi_overbought   = float(self.parameters.get("rsi_overbought", 70.0))
        rsi_trend_long   = float(self.parameters.get("rsi_trend_long", 45.0))
        rsi_trend_short  = float(self.parameters.get("rsi_trend_short", 55.0))
        crash_allow_short = bool(self.parameters.get("crash_allow_short", True))
        bb_period        = int(self.parameters.get("bb_period", 20))

        close = df["close"]
        closes_arr = close.values

        # Need at least slow_ema + some history
        min_bars = max(slow_ema + 5, hurst_lookback // 2, rsi_period + 5)
        if len(df) < min_bars:
            return self._hold(close, "insufficient_data", fast_ema, slow_ema, rsi_period)

        # ── Regime detection (uses all data up to current bar) ────────────────
        regime = _classify_regime(
            closes_arr,
            hurst_lookback=hurst_lookback,
            vol_lookback=vol_lookback,
            trend_hurst_min=trend_hurst_min,
            sideways_hurst_max=sideways_hurst_max,
            crash_vol_pct=crash_vol_pct,
        )

        # ── Common indicators ─────────────────────────────────────────────────
        ema_f = _ema(close, fast_ema)
        ema_s = _ema(close, slow_ema)
        rsi   = _rsi(close, rsi_period)

        ef_now,  es_now  = float(ema_f.iloc[-1]), float(ema_s.iloc[-1])
        ef_prev, es_prev = float(ema_f.iloc[-2]), float(ema_s.iloc[-2])
        rsi_now  = float(rsi.iloc[-1])
        rsi_prev = float(rsi.iloc[-2]) if len(rsi) > 1 else rsi_now
        px = float(close.iloc[-1])

        bullish_cross = ef_prev <= es_prev and ef_now > es_now   # golden cross
        bearish_cross = ef_prev >= es_prev and ef_now < es_now   # death cross
        bullish_trend = ef_now > es_now
        bearish_trend = ef_now < es_now

        # ── Signal logic per regime ───────────────────────────────────────────
        action     = "hold"
        confidence = 0.0

        if regime == REGIME_TRENDING:
            # Trade WITH the trend — EMA cross + RSI momentum confirmation
            # Long: golden cross OR already bullish with RSI rising from below threshold
            if (bullish_cross or (bullish_trend and rsi_prev < rsi_trend_long)) and rsi_now >= rsi_trend_long:
                action = "buy"
                confidence = min(1.0, 0.5 + (rsi_now - rsi_trend_long) / 100)
            # Short: death cross OR already bearish with RSI falling from above threshold
            elif (bearish_cross or (bearish_trend and rsi_prev > rsi_trend_short)) and rsi_now <= rsi_trend_short:
                action = "sell"
                confidence = min(1.0, 0.5 + (rsi_trend_short - rsi_now) / 100)

        elif regime == REGIME_SIDEWAYS:
            # Fade extremes — RSI + Bollinger band confirmation
            bb_upper, bb_lower = _bollinger(close, bb_period)
            bb_up  = float(bb_upper.iloc[-1]) if not pd.isna(bb_upper.iloc[-1]) else px * 1.04
            bb_low = float(bb_lower.iloc[-1]) if not pd.isna(bb_lower.iloc[-1]) else px * 0.96

            # Buy: RSI crossing up from oversold AND price near/below lower BB
            rsi_cross_up   = rsi_prev < rsi_oversold and rsi_now >= rsi_oversold
            rsi_deeply_os  = rsi_now < rsi_oversold + 5
            near_lower_bb  = px <= bb_low * 1.01

            # Sell: RSI crossing down from overbought AND price near/above upper BB
            rsi_cross_dn   = rsi_prev > rsi_overbought and rsi_now <= rsi_overbought
            rsi_deeply_ob  = rsi_now > rsi_overbought - 5
            near_upper_bb  = px >= bb_up * 0.99

            if (rsi_cross_up or rsi_deeply_os) and near_lower_bb:
                action = "buy"
                confidence = min(1.0, (rsi_oversold + 10 - rsi_now) / 20 + 0.4)
            elif (rsi_cross_dn or rsi_deeply_ob) and near_upper_bb:
                action = "sell"
                confidence = min(1.0, (rsi_now - (rsi_overbought - 10)) / 20 + 0.4)

        elif regime == REGIME_CRASH:
            # High-vol environment — short only (if enabled) with EMA confirmation
            if crash_allow_short and bearish_trend and rsi_now < 45:
                action = "sell"
                confidence = min(1.0, (45 - rsi_now) / 30 + 0.45)
            # Never go long in a crash — let stops handle open longs

        elif regime == REGIME_NEUTRAL:
            # Ambiguous regime — use simple EMA crossover, no RSI filter
            # More conservative: only trade on actual crossover bars
            if bullish_cross:
                action = "buy"
                confidence = 0.55
            elif bearish_cross:
                action = "sell"
                confidence = 0.55

        return Signal(
            symbol=self.symbol,
            action=action,
            confidence=round(confidence, 2),
            indicators={
                "regime":     regime,
                f"ema{fast_ema}":  round(ef_now, 4),
                f"ema{slow_ema}":  round(es_now, 4),
                "rsi":        round(rsi_now, 1),
                "trend":      "bullish" if bullish_trend else "bearish",
                "close":      round(px, 4),
            },
            timestamp=datetime.utcnow(),
        )

    def _hold(self, close, reason, fast_ema, slow_ema, rsi_period) -> Signal:
        return Signal(
            symbol=self.symbol,
            action="hold",
            confidence=0.0,
            indicators={"regime": "insufficient_data", "reason": reason},
            timestamp=datetime.utcnow(),
        )
