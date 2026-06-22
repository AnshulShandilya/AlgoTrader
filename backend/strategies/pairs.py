"""
Market-neutral statistical pairs trading strategy.

Theory:
  Two assets that are cointegrated tend to move together over time.
  When their spread (priceA - β*priceB) diverges significantly from
  its historical mean (z-score > 2.0), it is likely to mean-revert.

  Entry z > +2.0  → "short the spread": sell asset1, buy asset2
  Entry z < -2.0  → "long the spread":  buy asset1, sell asset2
  Exit  |z| < 0.5 → mean reversion achieved, close both legs
  Stop  |z| > 3.5 → spread diverging further, cut losses

Cointegration tested via Engle-Granger two-step procedure (statsmodels).
Hedge ratio estimated by OLS on a rolling lookback window.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Tuple, Optional, List
from itertools import combinations
import logging

log = logging.getLogger("pairs_strategy")

try:
    from statsmodels.tsa.stattools import coint
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools import add_constant as sm_add_constant
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False
    log.warning("statsmodels not installed — cointegration scan disabled")


def _add_constant(x: np.ndarray) -> np.ndarray:
    return sm_add_constant(x) if HAS_STATSMODELS else np.column_stack([np.ones(len(x)), x])


# ── Half-life estimation (OU process AR(1)) ───────────────────────────────────

def estimate_halflife(spread: np.ndarray) -> float:
    """
    Estimate mean-reversion half-life from an AR(1) fit on the spread.

    Ornstein-Uhlenbeck discrete approximation:
        ΔS_t = a + b·S_{t-1} + ε   (OLS fit)
        half-life = -log(2) / b     (valid when b < 0, i.e. mean-reverting)

    Returns:
        float — half-life in bars. +inf if spread is not mean-reverting (b ≥ 0).
    """
    if len(spread) < 10:
        return float("inf")
    y = np.diff(spread)
    x = spread[:-1]
    x_c = np.column_stack([np.ones(len(x)), x])
    try:
        beta = np.linalg.lstsq(x_c, y, rcond=None)[0]
    except np.linalg.LinAlgError:
        return float("inf")
    b = float(beta[1])
    if b >= 0:
        return float("inf")
    return float(-np.log(2.0) / b)


# ── Kalman-filter hedge ratio ─────────────────────────────────────────────────

class KalmanHedgeFilter:
    """
    Single-state Kalman filter for a time-varying hedge ratio β_t.

    Observation model:  p2_t = β_t · p1_t + ε_t      ε_t ~ N(0, R)
    Transition model:   β_t  = β_{t-1} + η_t          η_t ~ N(0, Q)

    Parameters
    ----------
    delta : float
        Process noise magnitude.  Larger → β can drift faster.
        Typical range: 1e-5 (very stable) to 1e-3 (fast drift).
    R : float
        Observation noise variance. Larger → trust observations less.
    """

    def __init__(self, delta: float = 1e-4, R: float = 1e-2):
        self.Q = delta       # process noise
        self.R = R           # observation noise
        self._beta: Optional[float] = None
        self._P: float = 1.0

    def initialize(self, y: np.ndarray, x: np.ndarray) -> None:
        """Set initial β from OLS on (y, x)."""
        if len(x) < 2:
            self._beta = 1.0
            return
        try:
            beta_ols, _ = np.polyfit(x, y, 1)
            self._beta = float(beta_ols)
        except Exception:
            self._beta = 1.0

    def update(self, p1: float, p2: float) -> float:
        """
        Ingest one new observation (p1_t, p2_t) and return updated β_t.
        Must call initialize() before the first update().
        """
        if self._beta is None:
            self._beta = p2 / (p1 + 1e-10)

        # Predict
        P_pred = self._P + self.Q

        # Innovation
        y_hat = self._beta * p1
        innov = p2 - y_hat
        S     = p1 * P_pred * p1 + self.R

        # Update
        K         = P_pred * p1 / S
        self._beta = self._beta + K * innov
        self._P    = (1.0 - K * p1) * P_pred

        return float(self._beta)

    @property
    def beta(self) -> float:
        return float(self._beta) if self._beta is not None else 1.0


# ── Cointegration stability ───────────────────────────────────────────────────

def check_cointegration(
    prices1: np.ndarray,
    prices2: np.ndarray,
    threshold: float = 0.10,
) -> dict:
    """
    Run Engle-Granger cointegration test on (prices1, prices2).

    Returns dict with:
        is_cointegrated : bool  (pvalue < threshold)
        pvalue          : float
    """
    if not HAS_STATSMODELS or len(prices1) < 20:
        return {"is_cointegrated": True, "pvalue": 0.0}
    try:
        _, pvalue, _ = coint(prices1, prices2)
        return {"is_cointegrated": bool(pvalue < threshold), "pvalue": round(float(pvalue), 4)}
    except Exception:
        return {"is_cointegrated": True, "pvalue": 0.0}


@dataclass
class PairsSignal:
    action: str          # "long_spread" | "short_spread" | "exit" | "hold"
    zscore: float
    hedge_ratio: float
    spread: float
    spread_mean: float
    spread_std: float
    leg1_action: str     # "buy" | "sell" | "hold"
    leg2_action: str     # "buy" | "sell" | "hold"
    kalman_beta: Optional[float] = None   # Kalman hedge ratio if used


class PairsStrategy:
    """
    Parameters
    ----------
    symbol1, symbol2 : str
        The two assets in the pair (symbol1 = y, symbol2 = x in regression).
    lookback : int
        Rolling window (bars) for computing hedge ratio and z-score.
    entry_zscore : float
        |z-score| threshold to open a position.
    exit_zscore : float
        |z-score| threshold below which position is closed (mean reversion).
    stop_zscore : float
        |z-score| threshold above which position is cut (divergence stop).
    """

    template = "pairs"
    risk_config: dict = None

    def __init__(
        self,
        symbol1: str,
        symbol2: str,
        lookback: int = 60,
        entry_zscore: float = 2.0,
        exit_zscore: float = 0.5,
        stop_zscore: float = 3.5,
        risk_config: Optional[dict] = None,
    ):
        self.symbol1 = symbol1
        self.symbol2 = symbol2
        self.lookback = lookback
        self.entry_z = entry_zscore
        self.exit_z  = exit_zscore
        self.stop_z  = stop_zscore
        self.risk_config = risk_config or {"stop_loss_pct": 3.0, "take_profit_pct": 5.0}

    # ── Core maths ────────────────────────────────────────────────────────────

    def _hedge_ratio(self, y: np.ndarray, x: np.ndarray) -> Tuple[float, float]:
        """OLS: y = β·x + α  →  returns (β, α)."""
        X = _add_constant(x)
        if HAS_STATSMODELS:
            model = OLS(y, X).fit()
            return float(model.params[1]), float(model.params[0])
        beta, alpha = np.polyfit(x, y, 1)
        return float(beta), float(alpha)

    def calc_zscore(
        self,
        prices1: np.ndarray,
        prices2: np.ndarray,
        hedge_ratio_override: Optional[float] = None,
    ) -> Tuple[float, float, float, float, float]:
        """Returns (zscore, beta, current_spread, spread_mean, spread_std).

        If hedge_ratio_override is provided (e.g. from Kalman filter), it is
        used instead of computing OLS on this call.
        """
        n = len(prices1)
        if n < self.lookback:
            return 0.0, 1.0, 0.0, 0.0, 0.0

        w1 = prices1[-self.lookback:]
        w2 = prices2[-self.lookback:]

        if hedge_ratio_override is not None:
            beta, alpha = hedge_ratio_override, 0.0
        else:
            beta, alpha = self._hedge_ratio(w1, w2)

        spread  = w1 - beta * w2 - alpha
        mean    = float(spread.mean())
        std     = float(spread.std())
        current = float(spread[-1])
        z       = (current - mean) / std if std > 0 else 0.0
        return z, beta, current, mean, std

    # ── Signal generation ─────────────────────────────────────────────────────

    def generate_signal(
        self,
        prices1: np.ndarray,
        prices2: np.ndarray,
        position: str = "none",              # "long_spread" | "short_spread" | "none"
        hedge_ratio_override: Optional[float] = None,
    ) -> PairsSignal:
        z, beta, spread, mean, std = self.calc_zscore(
            prices1, prices2, hedge_ratio_override=hedge_ratio_override
        )

        # Exit logic (checked first)
        kb = hedge_ratio_override  # None if OLS used

        if position != "none":
            if abs(z) < self.exit_z:
                return PairsSignal("exit", z, beta, spread, mean, std, "hold", "hold", kb)
            if abs(z) > self.stop_z:
                return PairsSignal("exit", z, beta, spread, mean, std, "hold", "hold", kb)

        if position == "none":
            if z > self.entry_z:
                return PairsSignal("short_spread", z, beta, spread, mean, std, "sell", "buy", kb)
            if z < -self.entry_z:
                return PairsSignal("long_spread", z, beta, spread, mean, std, "buy", "sell", kb)

        return PairsSignal("hold", z, beta, spread, mean, std, "hold", "hold", kb)


# ── Pair discovery ────────────────────────────────────────────────────────────

SCAN_UNIVERSE = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD",
    "XRP-USD", "ADA-USD", "LINK-USD", "DOGE-USD",
    "LTC-USD", "AVAX-USD",
]


def find_pairs(period: str = "1y", _use_router_cache: bool = True) -> List[dict]:
    """
    Download daily closes for the crypto universe and run pairwise
    Engle-Granger cointegration tests. Returns pairs sorted by p-value.
    """
    if not HAS_STATSMODELS:
        raise RuntimeError("statsmodels required — run: pip install statsmodels")

    import yfinance as yf

    log.info(f"Scanning {len(SCAN_UNIVERSE)} assets for cointegrated pairs…")

    # Fetch price history — reuse router cache when available
    try:
        from routers.backtest import _fetch_yf, _cache_get
        def _get_close(sym):
            our_sym = sym.replace("-USD", "/USD")
            df = _fetch_yf(our_sym, period)
            return df["close"].rename(sym)
        use_router_cache = True
    except Exception:
        use_router_cache = False

    data: dict = {}
    for sym in SCAN_UNIVERSE:
        try:
            if use_router_cache:
                data[sym] = _get_close(sym)
                continue
            df = yf.download(sym, period=period, interval="1d", progress=False, auto_adjust=True)
            if not df.empty:
                # yfinance ≥1.x: MultiIndex columns ('Close','BTC-USD'), ('High','BTC-USD')…
                if isinstance(df.columns, pd.MultiIndex):
                    close_col = [c for c in df.columns if c[0].lower() == "close"]
                    close = df[close_col[0]] if close_col else df.iloc[:, 0]
                else:
                    close = df["Close"] if "Close" in df.columns else df["close"]
                data[sym] = close.dropna()
        except Exception as e:
            log.warning(f"Skip {sym}: {e}")

    if len(data) < 2:
        return []

    # Align on common trading days
    prices_df = pd.DataFrame(data).dropna()
    log.info(f"Common history: {len(prices_df)} bars across {len(prices_df.columns)} assets")

    results = []
    for a1, a2 in combinations(list(prices_df.columns), 2):
        try:
            y = prices_df[a1].values
            x = prices_df[a2].values
            _, pvalue, _ = coint(y, x)
            correlation  = float(np.corrcoef(y, x)[0, 1])
            sym1 = a1.replace("-USD", "/USD")
            sym2 = a2.replace("-USD", "/USD")
            results.append({
                "symbol1":      sym1,
                "symbol2":      sym2,
                "pvalue":       round(float(pvalue), 4),
                "correlation":  round(correlation, 3),
                "cointegrated": pvalue < 0.05,
                "n_bars":       len(prices_df),
            })
        except Exception as e:
            log.debug(f"Coint failed {a1}/{a2}: {e}")

    results.sort(key=lambda r: r["pvalue"])
    log.info(f"Found {sum(r['cointegrated'] for r in results)} cointegrated pairs")
    return results
