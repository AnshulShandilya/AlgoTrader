"""
Single source of truth for all backtest performance metrics.

All functions accept a list/array of trade-level data or equity-curve values.
Pure numpy — no pandas dependency, no side effects.

Primary profitability metrics (CLAUDE.md):
  expectancy()    E = (WR × AvgWin) − (LR × AvgLoss)   positive → edge exists
  profit_factor() gross_profit / gross_loss              target ≥ 1.5
  payoff_ratio()  avg_win / avg_loss                     informational

Secondary / risk metrics:
  sharpe()        annualised Sharpe ratio
  sortino()       downside-only Sharpe
  calmar()        total_return / max_drawdown
  max_drawdown()  peak-to-trough as a percentage
"""
import math
import numpy as np
from typing import List


# ── Trade-level metrics ───────────────────────────────────────────────────────

def expectancy(pnls: List[float]) -> float:
    """
    Expected dollar P&L per trade.

    E = (win_rate × avg_win) − (loss_rate × avg_loss)

    Positive E means the strategy earns money on average after costs.
    The *scale* of E matters: E=$5 on $10,000 positions = 0.05 bps — not worth it.
    """
    if not pnls:
        return 0.0
    arr    = np.array(pnls, dtype=float)
    wins   = arr[arr > 0]
    losses = arr[arr <= 0]
    n      = len(arr)
    wr     = len(wins)   / n
    lr     = len(losses) / n
    avg_w  = float(wins.mean())   if len(wins)   > 0 else 0.0
    avg_l  = float(losses.mean()) if len(losses) > 0 else 0.0   # negative number
    return round(wr * avg_w + lr * avg_l, 4)   # lr × avg_l is negative, so subtracted


def profit_factor(pnls: List[float]) -> float:
    """
    Gross profit / gross loss.  > 1 = net positive;  target ≥ 1.5.
    Returns 0.0 when there are no losing trades (can't divide by zero; report separately).
    """
    arr          = np.array(pnls, dtype=float)
    gross_profit = float(arr[arr > 0].sum())
    gross_loss   = float(abs(arr[arr <= 0].sum()))
    if gross_loss == 0:
        return 0.0   # no losers: return 0 rather than inf to avoid serialisation issues
    return round(gross_profit / gross_loss, 4)


def payoff_ratio(pnls: List[float]) -> float:
    """
    Average win / average loss (absolute value).
    Informational: a strategy can be profitable with payoff < 1 if win rate is high.
    """
    arr  = np.array(pnls, dtype=float)
    wins = arr[arr > 0]
    loss = arr[arr <= 0]
    if len(wins) == 0 or len(loss) == 0:
        return 0.0
    return round(float(wins.mean()) / float(abs(loss.mean())), 4)


def expectancy_pct(pnl_pcts: List[float]) -> float:
    """Same as expectancy() but using % returns — useful for cross-asset comparison."""
    return expectancy(pnl_pcts)


# ── Equity-curve metrics ──────────────────────────────────────────────────────

def max_drawdown(equity: np.ndarray) -> float:
    """Peak-to-trough drawdown as a positive percentage."""
    if len(equity) < 2:
        return 0.0
    peak_arr  = np.maximum.accumulate(equity)
    drawdowns = (peak_arr - equity) / peak_arr * 100
    return float(drawdowns.max())


def sharpe(equity: np.ndarray, ann_N: int = 252, rf: float = 0.05) -> float:
    """Annualised Sharpe ratio (excess return over daily risk-free rate)."""
    if len(equity) < 2:
        return 0.0
    rets   = np.diff(equity) / equity[:-1]
    std    = rets.std()
    if std == 0:
        return 0.0
    rfday  = rf / ann_N
    excess = rets - rfday
    return float(excess.mean() / std * np.sqrt(ann_N))


def sortino(equity: np.ndarray, ann_N: int = 252, rf: float = 0.05) -> float:
    """Sortino ratio — penalises downside volatility only."""
    if len(equity) < 2:
        return 0.0
    rets   = np.diff(equity) / equity[:-1]
    rfday  = rf / ann_N
    excess = rets - rfday
    down   = rets[rets < rfday]
    dstd   = down.std() if len(down) > 1 else rets.std()
    if dstd == 0:
        return 0.0
    return float(excess.mean() / dstd * np.sqrt(ann_N))


def calmar(total_return_pct: float, max_dd_pct: float) -> float:
    """Calmar ratio: total return / max drawdown."""
    if max_dd_pct <= 0:
        return 0.0
    return round(total_return_pct / max_dd_pct, 4)


# ── Convenience bundle ────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal CDF via error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Standard normal quantile via scipy erfinv (accurate, no lookahead)."""
    from scipy.special import erfinv as _erfinv
    p = float(np.clip(p, 1e-15, 1.0 - 1e-15))
    return float(math.sqrt(2.0) * _erfinv(2.0 * p - 1.0))


def _moments(rets: np.ndarray) -> tuple:
    """Return (skewness, excess_kurtosis) of a returns array."""
    n = len(rets)
    if n < 3:
        return 0.0, 0.0
    m = rets.mean()
    s = rets.std()
    if s == 0:
        return 0.0, 0.0
    z = (rets - m) / s
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4)) - 3.0  # excess kurtosis (Gaussian = 0)
    return skew, kurt


def moments_of_returns(equity: np.ndarray) -> tuple:
    """Compute (skewness, excess_kurtosis) from an equity curve."""
    equity = np.asarray(equity, dtype=float)
    if len(equity) < 3:
        return 0.0, 0.0
    rets = np.diff(equity) / equity[:-1]
    return _moments(rets)


def deflated_sharpe_ratio(
    sr_annualized: float,
    n_obs: int,
    n_trials: int,
    skewness: float = 0.0,
    excess_kurtosis: float = 0.0,
    ann_N: int = 252,
) -> float:
    """
    Deflated Sharpe Ratio (Bailey & López de Prado 2014).

    Adjusts for selection bias when the best Sharpe from N_trials configs is
    reported.  Returns a probability in [0, 1] — the DSR interpretation:
      ≥ 0.95 → strategy edge is likely genuine at the 5% level.
      < 0.95 → the edge may be an artefact of overfitting.

    Args:
        sr_annualized:   observed annualised Sharpe ratio
        n_obs:           number of OOS observations used to estimate that SR
        n_trials:        total number of IS parameter configs trialled
        skewness:        skewness of OOS returns (0 = Gaussian)
        excess_kurtosis: excess kurtosis of OOS returns (0 = Gaussian)
        ann_N:           trading days per year (252 equity / 365 crypto)
    """
    if n_obs < 2:
        return 0.0

    # Convert annualized SR to per-observation SR
    sr_hat = sr_annualized / math.sqrt(ann_N)

    # PSR variance term (Bailey & López de Prado 2014, Eq. 3).
    # excess_kurtosis = gamma4 - 3, so (gamma4 - 1)/4 = (excess_kurtosis + 2) / 4.
    denom_sq = (
        1.0
        - skewness * sr_hat
        + (excess_kurtosis + 2.0) / 4.0 * sr_hat ** 2
    )
    if denom_sq <= 0:
        denom_sq = 1e-8
    sigma = math.sqrt(denom_sq)

    # Expected maximum SR from N_trials under H0 (Eq. 8).
    # SR_n* = sigma / sqrt(T-1) × [(1−γ_E)·Z(1−1/N) + γ_E·Z(1−1/(N·e))]
    euler_gamma = 0.5772156649
    if n_trials <= 1:
        sr_0 = 0.0
    else:
        z1   = _norm_ppf(1.0 - 1.0 / n_trials)
        z2   = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
        sr_0 = sigma * ((1.0 - euler_gamma) * z1 + euler_gamma * z2) / math.sqrt(n_obs - 1)

    z = (sr_hat - sr_0) * math.sqrt(n_obs - 1) / sigma
    return round(_norm_cdf(z), 6)


def mc_permutation_test(
    equity: np.ndarray,
    n_permutations: int = 1000,
    ann_N: int = 252,
) -> dict:
    """
    Monte Carlo permutation test on an equity curve.

    Shuffles the daily returns N times and computes the fraction of permuted
    Sharpe ratios ≥ the observed Sharpe (one-sided p-value).  A low p-value
    (≤ 0.05) means the observed performance is unlikely under the null of
    random returns.

    Args:
        equity:          equity curve (array of portfolio values)
        n_permutations:  number of random shuffles
        ann_N:           annualisation factor for Sharpe computation
    """
    equity = np.asarray(equity, dtype=float)
    if len(equity) < 5:
        return {"observed_sharpe": 0.0, "p_value": 1.0, "n_permutations": n_permutations,
                "significant": False, "verdict": "Insufficient data for permutation test"}

    observed = sharpe(equity, ann_N)
    rets     = np.diff(equity) / equity[:-1]

    count = 0
    for _ in range(n_permutations):
        perm_rets  = np.random.permutation(rets)
        perm_eq    = equity[0] * np.cumprod(np.concatenate([[1.0], 1.0 + perm_rets]))
        perm_sharpe = sharpe(perm_eq, ann_N)
        if perm_sharpe >= observed:
            count += 1

    # +1 continuity correction (Phipson & Smyth 2010)
    pvalue = (count + 1) / (n_permutations + 1)
    sig    = pvalue <= 0.05
    return {
        "observed_sharpe": round(observed, 4),
        "p_value":         round(pvalue, 4),
        "n_permutations":  n_permutations,
        "significant":     sig,
        "verdict": (
            "Significant at 5%: returns are unlikely to be random"
            if sig else
            "Not significant: observed Sharpe is consistent with random returns"
        ),
    }


# ── Convenience bundle ────────────────────────────────────────────────────────

def compute_all(
    pnls: List[float],
    equity: np.ndarray,
    total_return_pct: float,
    ann_N: int = 252,
) -> dict:
    """
    Return every metric in one dict. Used by both single-asset and pairs report builders.
    """
    max_dd = max_drawdown(equity)
    return {
        "expectancy":    expectancy(pnls),
        "profit_factor": profit_factor(pnls),
        "payoff_ratio":  payoff_ratio(pnls),
        "sharpe_ratio":  round(sharpe(equity, ann_N), 4),
        "sortino_ratio": round(sortino(equity, ann_N), 4),
        "calmar_ratio":  round(calmar(total_return_pct, max_dd), 4),
        "max_drawdown_pct": round(max_dd, 4),
    }
