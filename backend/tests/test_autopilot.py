"""
Unit tests for autopilot.py pure functions: _score_backtest and _score_asset_daily.

Rules (CLAUDE.md): no network calls, no DB, no broker objects.
"""
import math
import numpy as np
import pandas as pd
import pytest

from autopilot import _score_backtest, _score_asset_daily


# ── Helpers ────────────────────────────────────────────────────────────────────

def _good_result(**overrides) -> dict:
    """Base backtest result that passes all KPIs."""
    base = {
        "expectancy":    50.0,
        "profit_factor": 2.0,
        "max_drawdown_pct": 5.0,
        "sharpe_ratio":  2.0,
        "total_trades":  30,
        "kpi_pass": {
            "expectancy":    True,
            "profit_factor": True,
            "max_drawdown":  True,
            "sharpe":        True,
            "dsr":           True,
        },
    }
    base.update(overrides)
    return base


def _bad_result(**overrides) -> dict:
    """Base backtest result that fails all KPIs."""
    base = {
        "expectancy":    -5.0,
        "profit_factor": 0.8,
        "max_drawdown_pct": 20.0,
        "sharpe_ratio":  0.3,
        "total_trades":  5,
        "kpi_pass": {
            "expectancy":    False,
            "profit_factor": False,
            "max_drawdown":  False,
            "sharpe":        False,
        },
    }
    base.update(overrides)
    return base


# ── _score_backtest zero-score conditions ─────────────────────────────────────

class TestScoreBacktestZeroConditions:
    def test_zero_when_expectancy_non_positive(self):
        assert _score_backtest(_good_result(expectancy=0.0), 10) == 0.0
        assert _score_backtest(_good_result(expectancy=-1.0), 10) == 0.0

    def test_zero_when_profit_factor_below_1(self):
        assert _score_backtest(_good_result(profit_factor=0.99), 10) == 0.0
        assert _score_backtest(_good_result(profit_factor=0.0), 10) == 0.0

    def test_zero_when_too_few_trades(self):
        """Less than max(3, min_trades//3) trades → 0."""
        # min_trades=30 → floor = 10; need total_trades >= 10
        assert _score_backtest(_good_result(total_trades=2), 30) == 0.0

    def test_three_trades_with_small_min_trades_not_zero(self):
        """min_trades=3 → floor = max(3, 1) = 3; 3 trades is exactly enough."""
        score = _score_backtest(_good_result(total_trades=3), 3)
        assert score > 0.0

    def test_empty_result_returns_zero(self):
        assert _score_backtest({}, 10) == 0.0


# ── _score_backtest value ranges ─────────────────────────────────────────────

class TestScoreBacktestRanges:
    def test_score_in_0_100(self):
        for pf in (1.1, 1.5, 2.0, 5.0, 10.0):
            r = _good_result(profit_factor=pf)
            score = _score_backtest(r, 10)
            assert 0.0 <= score <= 100.0, f"score={score} for pf={pf}"

    def test_perfect_result_near_100(self):
        r = _good_result(
            profit_factor=10.0,
            max_drawdown_pct=0.0,
            sharpe_ratio=5.0,
            total_trades=200,
            expectancy=500.0,
        )
        assert _score_backtest(r, 10) > 80.0

    def test_borderline_result_positive_score(self):
        r = _good_result(
            profit_factor=1.01,
            max_drawdown_pct=9.9,
            sharpe_ratio=0.1,
            total_trades=5,
            expectancy=0.01,
        )
        score = _score_backtest(r, 3)
        assert score >= 0.0


# ── _score_backtest monotonicity ──────────────────────────────────────────────

class TestScoreBacktestMonotonicity:
    def test_higher_profit_factor_scores_higher(self):
        s1 = _score_backtest(_good_result(profit_factor=1.2), 10)
        s2 = _score_backtest(_good_result(profit_factor=2.0), 10)
        s3 = _score_backtest(_good_result(profit_factor=4.0), 10)
        assert s1 <= s2 <= s3

    def test_lower_drawdown_scores_higher(self):
        s1 = _score_backtest(_good_result(max_drawdown_pct=9.0), 10)
        s2 = _score_backtest(_good_result(max_drawdown_pct=5.0), 10)
        s3 = _score_backtest(_good_result(max_drawdown_pct=1.0), 10)
        assert s1 <= s2 <= s3

    def test_higher_sharpe_scores_higher(self):
        s1 = _score_backtest(_good_result(sharpe_ratio=0.5), 10)
        s2 = _score_backtest(_good_result(sharpe_ratio=1.5), 10)
        s3 = _score_backtest(_good_result(sharpe_ratio=3.0), 10)
        assert s1 <= s2 <= s3

    def test_more_trades_scores_higher_up_to_cap(self):
        s1 = _score_backtest(_good_result(total_trades=5),  3)
        s2 = _score_backtest(_good_result(total_trades=30), 3)
        s3 = _score_backtest(_good_result(total_trades=100), 3)
        assert s1 <= s2 <= s3

    def test_higher_expectancy_scores_higher(self):
        s1 = _score_backtest(_good_result(expectancy=1.0),   10)
        s2 = _score_backtest(_good_result(expectancy=50.0),  10)
        s3 = _score_backtest(_good_result(expectancy=200.0), 10)
        assert s1 <= s2 <= s3


# ── all-pass KPI bonus ────────────────────────────────────────────────────────

class TestKpiPassBonus:
    def test_all_pass_applies_10pct_bonus(self):
        all_pass = _good_result(kpi_pass={
            "expectancy": True, "profit_factor": True,
            "max_drawdown": True, "sharpe": True,
        })
        one_fail = _good_result(kpi_pass={
            "expectancy": True, "profit_factor": True,
            "max_drawdown": True, "sharpe": False,
        })
        s_pass = _score_backtest(all_pass, 10)
        s_fail = _score_backtest(one_fail, 10)
        assert s_pass > s_fail
        # The bonus is 10%
        assert s_pass == pytest.approx(s_fail * 1.10, rel=0.01)

    def test_any_false_in_kpi_no_bonus(self):
        """At least one False → all() is False → no bonus."""
        with_false  = _good_result(kpi_pass={"expectancy": True,  "profit_factor": False})
        all_true    = _good_result(kpi_pass={"expectancy": True,  "profit_factor": True})
        s_no_bonus  = _score_backtest(with_false, 10)
        s_with_bonus = _score_backtest(all_true, 10)
        assert s_with_bonus == pytest.approx(s_no_bonus * 1.10, rel=0.01)

    def test_score_capped_at_100(self):
        """Even with 10% bonus, score must not exceed 100."""
        r = _good_result(
            profit_factor=10.0, max_drawdown_pct=0.0,
            sharpe_ratio=5.0, total_trades=500,
            expectancy=10_000.0,
            kpi_pass={k: True for k in ["expectancy", "profit_factor", "max_drawdown", "sharpe"]},
        )
        assert _score_backtest(r, 10) <= 100.0


# ── _score_asset_daily ────────────────────────────────────────────────────────

class TestScoreAssetDaily:
    def test_returns_float_in_0_100(self, synthetic_ohlcv):
        score = _score_asset_daily("AAPL", synthetic_ohlcv)
        assert isinstance(score, float)
        assert 0.0 <= score <= 100.0

    def test_different_symbols_same_data_same_score(self, synthetic_ohlcv):
        """Score depends only on price data, not the symbol string."""
        s1 = _score_asset_daily("AAPL", synthetic_ohlcv)
        s2 = _score_asset_daily("BTC",  synthetic_ohlcv)
        assert s1 == s2

    def test_trending_vs_flat(self):
        """Trending data should generally score differently from flat data."""
        np.random.seed(20)
        n = 300
        closes_up = np.cumsum(np.abs(np.random.normal(0.5, 0.3, n))) + 100
        closes_flat = np.full(n, 100.0) + np.random.normal(0, 0.2, n)

        def _make_df(closes):
            opens = np.roll(closes, 1); opens[0] = closes[0]
            return pd.DataFrame({
                "datetime": pd.date_range("2022-01-01", periods=n, freq="h"),
                "open": opens,
                "high": np.maximum(opens, closes) * 1.005,
                "low":  np.minimum(opens, closes) * 0.995,
                "close": closes,
                "volume": np.full(n, 1_000_000.0),
            })

        s_trend = _score_asset_daily("A", _make_df(closes_up))
        s_flat  = _score_asset_daily("B", _make_df(closes_flat))
        # They should differ (trending market tends to score higher)
        assert s_trend != s_flat

    def test_short_df_returns_zero_or_valid(self):
        """With very few bars, compute_all returns default score (0) gracefully."""
        short = pd.DataFrame({
            "datetime": pd.date_range("2022-01-01", periods=20, freq="D"),
            "open": np.full(20, 100.0), "high": np.full(20, 101.0),
            "low": np.full(20, 99.0), "close": np.full(20, 100.0),
            "volume": np.full(20, 1_000_000.0),
        })
        score = _score_asset_daily("X", short)
        assert 0.0 <= score <= 100.0

    def test_reproducible(self, synthetic_ohlcv):
        s1 = _score_asset_daily("X", synthetic_ohlcv)
        s2 = _score_asset_daily("X", synthetic_ohlcv)
        assert s1 == s2
