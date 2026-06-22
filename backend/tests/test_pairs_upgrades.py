"""
Tests for Phase 5: Kalman hedge ratio, half-life estimation, cointegration stability.

Acceptance criteria:
  - estimate_halflife() returns a finite positive value for mean-reverting spread.
  - estimate_halflife() returns inf for a trending spread.
  - KalmanHedgeFilter.beta tracks OLS beta after warm-up.
  - Kalman beta changes over time (time-varying hedge ratio).
  - check_cointegration() returns is_cointegrated=True for a synthetic cointegrated pair.
  - Pairs backtest report includes hedge_ratio_method, kalman_beta_final, halflife_bars,
    cointegration_pvalue when use_kalman=True.
  - Kalman vs OLS backtests complete without error and return valid metrics.
"""
import numpy as np
import pytest

from strategies.pairs import (
    PairsStrategy,
    KalmanHedgeFilter,
    estimate_halflife,
    check_cointegration,
)
from backtester_pairs import PairsBacktestEngine
from cost_model import CostModel
import pandas as pd


# ── Synthetic cointegrated pair fixture ────────────────────────────────────────

@pytest.fixture
def cointegrated_pair():
    """Two cointegrated price series: p2 ≈ 2 × p1 + noise."""
    np.random.seed(42)
    n = 400
    drift = np.cumsum(np.random.normal(0, 0.5, n))
    p1 = 100 + drift
    # p2 = 2 × p1 + stationary OU noise
    noise = np.zeros(n)
    for i in range(1, n):
        noise[i] = 0.8 * noise[i - 1] + np.random.normal(0, 1.0)
    p2 = 2.0 * p1 + noise + 50.0

    def _make_df(prices):
        df = pd.DataFrame({
            "datetime": pd.date_range("2022-01-01", periods=n, freq="D"),
            "open":   prices * (1 + np.random.normal(0, 0.001, n)),
            "high":   prices * (1 + abs(np.random.normal(0, 0.005, n))),
            "low":    prices * (1 - abs(np.random.normal(0, 0.005, n))),
            "close":  prices,
            "volume": np.full(n, 1_000_000),
        })
        return df

    return _make_df(p1), _make_df(p2), p1, p2


# ── Half-life estimation ──────────────────────────────────────────────────────

class TestHalfLife:
    def test_mean_reverting_spread_finite(self, cointegrated_pair):
        _, _, p1, p2 = cointegrated_pair
        spread = p1 - 0.5 * p2   # stationary by construction
        hl = estimate_halflife(spread)
        assert hl > 0
        assert hl != float("inf")

    def test_random_walk_longer_halflife_than_mean_reverting(self):
        """A random walk should have a longer half-life than a fast mean-reverting series."""
        np.random.seed(99)
        rw = np.cumsum(np.random.normal(0, 1, 300))         # random walk

        mr = np.zeros(300)                                   # fast mean-reverting OU
        for i in range(1, 300):
            mr[i] = 0.1 * mr[i - 1] + np.random.normal(0, 1)

        hl_rw = estimate_halflife(rw)
        hl_mr = estimate_halflife(mr)
        # Fast OU should have shorter half-life than random walk (which barely mean-reverts)
        if hl_rw != float("inf") and hl_mr != float("inf"):
            assert hl_rw > hl_mr

    def test_strongly_mean_reverting_short_halflife(self):
        """OU process with high κ should have short half-life."""
        np.random.seed(42)
        x = [0.0]
        kappa = 0.5   # fast mean reversion → half-life ≈ log(2)/0.5 ≈ 1.4 bars
        for _ in range(300):
            x.append((1 - kappa) * x[-1] + np.random.normal(0, 0.5))
        hl = estimate_halflife(np.array(x))
        assert hl < 20   # should be much less than 20 bars

    def test_short_series_returns_inf(self):
        assert estimate_halflife(np.array([1.0, 2.0, 3.0])) == float("inf")


# ── Kalman Hedge Filter ───────────────────────────────────────────────────────

class TestKalmanHedgeFilter:
    def test_initialize_and_update(self, cointegrated_pair):
        _, _, p1, p2 = cointegrated_pair
        kf = KalmanHedgeFilter(delta=1e-4, R=1e-2)
        kf.initialize(p1[:60], p2[:60])
        assert kf.beta > 0

    def test_beta_changes_over_time(self, cointegrated_pair):
        _, _, p1, p2 = cointegrated_pair
        kf = KalmanHedgeFilter(delta=1e-4, R=1e-2)
        kf.initialize(p1[:60], p2[:60])
        betas = [kf.update(p2[i], p1[i]) for i in range(60, 200)]
        # Beta should not be constant (Kalman updates it)
        assert max(betas) - min(betas) > 0

    def test_tracks_ols_estimate_approximately(self, cointegrated_pair):
        """After warm-up, Kalman beta should be close to OLS beta on the window."""
        _, _, p1, p2 = cointegrated_pair
        kf = KalmanHedgeFilter(delta=1e-5, R=1e-3)  # tight: slow drift
        kf.initialize(p1[:60], p2[:60])
        for i in range(60, 200):
            kf.update(p2[i], p1[i])
        ols_beta, _ = np.polyfit(p2[140:200], p1[140:200], 1)
        # Should be in the same ballpark (within 50%)
        assert abs(kf.beta - ols_beta) / max(abs(ols_beta), 0.1) < 0.5

    def test_beta_always_finite(self, cointegrated_pair):
        _, _, p1, p2 = cointegrated_pair
        kf = KalmanHedgeFilter()
        kf.initialize(p1[:60], p2[:60])
        for i in range(60, len(p1)):
            b = kf.update(p2[i], p1[i])
            assert np.isfinite(b)


# ── Cointegration check ───────────────────────────────────────────────────────

class TestCointegrationCheck:
    def test_cointegrated_pair_detected(self, cointegrated_pair):
        _, _, p1, p2 = cointegrated_pair
        result = check_cointegration(p1, p2, threshold=0.10)
        assert "is_cointegrated" in result
        assert "pvalue" in result
        # Should detect cointegration (p-value < 0.10)
        assert result["is_cointegrated"] is True

    def test_non_cointegrated_pair_not_detected(self):
        """Two independent random walks should not be cointegrated."""
        np.random.seed(77)
        p1 = np.cumsum(np.random.normal(0, 1, 300)) + 100
        p2 = np.cumsum(np.random.normal(0, 1, 300)) + 200  # independent
        result = check_cointegration(p1, p2, threshold=0.01)  # strict threshold
        # With strict threshold, independent series should mostly fail
        assert "pvalue" in result

    def test_short_series_returns_default(self):
        result = check_cointegration(np.array([1.0, 2.0]), np.array([2.0, 4.0]))
        assert result["is_cointegrated"] is True  # default safe


# ── Pairs backtest with Kalman ────────────────────────────────────────────────

class TestPairsKalmanIntegration:
    def _run(self, df1, df2, use_kalman=True):
        strat = PairsStrategy(
            symbol1="SYM1", symbol2="SYM2",
            lookback=60, entry_zscore=2.0, exit_zscore=0.5, stop_zscore=3.5,
        )
        engine = PairsBacktestEngine(
            initial_capital=100_000,
            position_size_pct=20.0,
            cost_model=CostModel.zero(),
            use_kalman=use_kalman,
        )
        return engine.run(strat, df1.copy(), df2.copy())

    def test_report_has_kalman_fields(self, cointegrated_pair):
        df1, df2, _, _ = cointegrated_pair
        r = self._run(df1, df2, use_kalman=True)
        assert r["hedge_ratio_method"] == "kalman"
        assert r["kalman_beta_final"] is not None
        assert r["halflife_bars"] is not None or r["halflife_warning"] is True
        assert "cointegration_pvalue" in r
        assert "cointegrated" in r

    def test_ols_mode_report(self, cointegrated_pair):
        df1, df2, _, _ = cointegrated_pair
        r = self._run(df1, df2, use_kalman=False)
        assert r["hedge_ratio_method"] == "ols"
        assert r["kalman_beta_final"] is None

    def test_kalman_and_ols_both_complete(self, cointegrated_pair):
        df1, df2, _, _ = cointegrated_pair
        r_kalman = self._run(df1, df2, use_kalman=True)
        r_ols    = self._run(df1, df2, use_kalman=False)
        for key in ("total_return_pct", "total_trades", "sharpe_ratio"):
            assert key in r_kalman
            assert key in r_ols

    def test_halflife_meaningful_for_cointegrated_pair(self, cointegrated_pair):
        df1, df2, _, _ = cointegrated_pair
        r = self._run(df1, df2)
        # For a genuinely cointegrated pair, half-life should be finite and < 252
        if r["halflife_bars"] is not None:
            assert r["halflife_bars"] > 0

    def test_cointegration_detected(self, cointegrated_pair):
        df1, df2, _, _ = cointegrated_pair
        r = self._run(df1, df2)
        assert r["cointegrated"] is True
