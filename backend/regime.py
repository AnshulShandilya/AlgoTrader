"""
Phase 4: Regime gate — block new entries when market conditions are unfavourable.

Two filters applied in conjunction:
  1. Hurst exponent   — characterises the market's autocorrelation structure
  2. Vol percentile   — identifies unusually high-volatility regimes

Hurst exponent interpretation (H):
  H > 0.5  trending / persistent  → momentum strategies work
  H ≈ 0.5  random walk            → neutral
  H < 0.5  mean-reverting         → pairs / stat-arb strategies work

Typical presets:
  "off"     → no gating (always allow)
  "relaxed" → block only extreme regimes (H ∈ [0.2, 0.8], vol < 90th pct)
  "standard"→ H ∈ [0.3, 0.7], vol < 80th pct
  "strict"  → H ∈ [0.4, 0.6], vol < 65th pct

No lookahead: allow_entry(closes, i) only uses closes[0:i] (bar i is NOT included —
it is the signal bar whose close we've just observed, but the indicator window ends
at closes[i-1] for signal generation at bar i).
"""
from dataclasses import dataclass
import numpy as np


# ── Hurst exponent (R/S variance method) ─────────────────────────────────────

def hurst_exponent(prices: np.ndarray, max_lag: int = 20) -> float:
    """
    Estimate Hurst exponent via log-log regression of std(lag-differences)
    on lag length (Rescaled Range / variance method).

    Returns H ∈ [0, 1].  Falls back to 0.5 (neutral) on insufficient data.
    """
    prices = np.asarray(prices, dtype=float)
    n = len(prices)
    if n < max_lag + 4:
        return 0.5

    lags  = range(2, min(max_lag + 1, n // 4))
    taus  = []
    valid = []
    for lag in lags:
        diff = prices[lag:] - prices[:-lag]
        std  = float(diff.std())
        if std > 0:
            taus.append(std)
            valid.append(lag)

    if len(taus) < 4:
        return 0.5

    log_lags = np.log(np.array(valid, dtype=float))
    log_taus = np.log(np.array(taus))

    # OLS slope = H
    n_pts = len(log_lags)
    lx, ly = log_lags.mean(), log_taus.mean()
    H = float(np.dot(log_lags - lx, log_taus - ly) / np.dot(log_lags - lx, log_lags - lx))
    return float(np.clip(H, 0.0, 1.0))


# ── Vol percentile ────────────────────────────────────────────────────────────

def _rolling_vol(rets: np.ndarray, window: int = 10) -> np.ndarray:
    """Array of rolling `window`-bar standard deviations of `rets`."""
    if len(rets) < window:
        return np.array([rets.std()] if len(rets) > 0 else [0.0])
    out = np.array([rets[max(0, i - window):i].std() for i in range(window, len(rets) + 1)])
    return out


# ── Regime Gate ───────────────────────────────────────────────────────────────

@dataclass
class RegimeGate:
    """
    Gate that returns False (block entry) when the market is in an unfavourable
    regime for the deployed strategy.

    Parameters
    ----------
    hurst_min, hurst_max : float
        Allowed range of Hurst exponent.  Entries blocked outside this range.
    vol_pct_max : float
        Block entries when the current 10-bar volatility's percentile rank (in
        the lookback window) exceeds this threshold.
    lookback : int
        Bars used for Hurst and vol history.
    """
    hurst_min: float = 0.3
    hurst_max: float = 0.7
    vol_pct_max: float = 80.0
    lookback: int = 60

    def allow_entry(self, closes: np.ndarray, i: int) -> bool:
        """
        Returns True if regime allows a new entry at bar i.

        Uses closes[max(0, i-lookback) : i] — does NOT include bar i's close
        (no lookahead: bar i close is the signal bar, entry happens at bar i+1).
        """
        start  = max(0, i - self.lookback)
        window = closes[start:i]      # up to bar i-1

        if len(window) < max(12, self.lookback // 5):
            return True   # not enough history → don't gate early bars

        # ── Hurst check ──────────────────────────────────────────────────────
        H = hurst_exponent(window)
        if not (self.hurst_min <= H <= self.hurst_max):
            return False

        # ── Vol percentile check ──────────────────────────────────────────────
        rets = np.diff(window) / (window[:-1] + 1e-10)
        if len(rets) >= 12:
            roll_vols  = _rolling_vol(rets, window=10)
            current    = float(roll_vols[-1])
            pct_rank   = float(np.mean(roll_vols <= current) * 100)
            if pct_rank > self.vol_pct_max:
                return False

        return True

    def as_dict(self) -> dict:
        return {
            "hurst_min":   self.hurst_min,
            "hurst_max":   self.hurst_max,
            "vol_pct_max": self.vol_pct_max,
            "lookback":    self.lookback,
        }


# ── Presets ───────────────────────────────────────────────────────────────────

_PRESETS = {
    "off":      RegimeGate(hurst_min=0.0, hurst_max=1.0, vol_pct_max=100.0),
    "relaxed":  RegimeGate(hurst_min=0.2, hurst_max=0.8, vol_pct_max=90.0),
    "standard": RegimeGate(hurst_min=0.3, hurst_max=0.7, vol_pct_max=80.0),
    "strict":   RegimeGate(hurst_min=0.4, hurst_max=0.6, vol_pct_max=65.0),
}


def make_regime_gate(
    preset: str = "off",
    hurst_min: float = 0.3,
    hurst_max: float = 0.7,
    vol_pct_max: float = 80.0,
    lookback: int = 60,
) -> RegimeGate:
    """Return a RegimeGate from a named preset or explicit parameters."""
    if preset in _PRESETS:
        return _PRESETS[preset]
    return RegimeGate(
        hurst_min=hurst_min,
        hurst_max=hurst_max,
        vol_pct_max=vol_pct_max,
        lookback=lookback,
    )
