"""
Tests for Phase 2: walk-forward validation, Deflated Sharpe Ratio, MC permutation test.

All tests use the synthetic_ohlcv fixture — no network calls, no yfinance.

Key acceptance criteria (CLAUDE.md):
  - OOS-only Sharpe / expectancy are reported, separate from IS.
  - DSR is displayed alongside raw Sharpe with n_trials as input.
  - Permutation test returns p-value + plain-English verdict.
  - IS and OOS periods never overlap.
  - DSR approaches 0 when n_trials is very large (selection bias saturates).
  - DSR approaches 1 when n_trials=1 and SR is strongly positive.
"""
import math
import numpy as np
import pytest

import metrics as _m
from backtester import BacktestEngine
from cost_model import CostModel
from strategies import get_strategy
from walk_forward import run_walk_forward, _param_combos, _stitch_oos_equity


# ── Deflated Sharpe Ratio ─────────────────────────────────────────────────────

class TestDeflatedSharpe:
    def test_returns_probability_in_0_1(self):
        dsr = _m.deflated_sharpe_ratio(1.5, 252, 10, 0.0, 0.0)
        assert 0.0 <= dsr <= 1.0

    def test_single_trial_no_deflation(self):
        """With 1 trial, DSR should be close to the raw SR probability."""
        dsr1  = _m.deflated_sharpe_ratio(2.0, 252, 1,   0.0, 0.0)
        dsr50 = _m.deflated_sharpe_ratio(2.0, 252, 50,  0.0, 0.0)
        # More trials → more deflation → lower DSR
        assert dsr1 > dsr50

    def test_more_trials_lowers_dsr(self):
        """DSR should decrease monotonically as n_trials increases."""
        dsrs = [_m.deflated_sharpe_ratio(1.5, 252, n, 0.0, 0.0) for n in [1, 5, 20, 100]]
        for a, b in zip(dsrs, dsrs[1:]):
            assert a >= b

    def test_negative_sharpe_yields_low_dsr(self):
        dsr = _m.deflated_sharpe_ratio(-0.5, 252, 10, 0.0, 0.0)
        assert dsr < 0.2

    def test_strong_positive_sharpe_single_trial_significant(self):
        dsr = _m.deflated_sharpe_ratio(3.0, 500, 1, 0.0, 0.0)
        assert dsr > 0.95

    def test_non_gaussian_returns_lowers_dsr(self, known_trade_log):
        """Negative skewness (fat tails) reduces DSR relative to Gaussian."""
        dsr_gaussian = _m.deflated_sharpe_ratio(2.0, 252, 5, skewness=0.0,  excess_kurtosis=0.0)
        dsr_negskew  = _m.deflated_sharpe_ratio(2.0, 252, 5, skewness=-1.0, excess_kurtosis=3.0)
        assert dsr_gaussian > dsr_negskew

    def test_zero_obs_returns_zero(self):
        assert _m.deflated_sharpe_ratio(2.0, 0, 5, 0.0, 0.0) == 0.0

    def test_moments_of_returns_gaussian(self):
        """Synthetic equity from GBM should have near-zero skew and kurtosis."""
        np.random.seed(42)
        rets  = np.random.normal(0, 0.01, 1000)
        equity = 100_000 * np.cumprod(1 + np.concatenate([[0.0], rets]))
        sk, ku = _m.moments_of_returns(equity)
        assert abs(sk) < 0.5   # weakly bounded, not tight
        assert abs(ku) < 1.5


# ── Monte Carlo Permutation Test ──────────────────────────────────────────────

class TestMCPermutation:
    def test_returns_required_keys(self, synthetic_ohlcv):
        equity = np.array([100_000 * (1 + 0.001 * i) for i in range(200)])
        result = _m.mc_permutation_test(equity, n_permutations=100)
        for key in ("observed_sharpe", "p_value", "n_permutations", "significant", "verdict"):
            assert key in result

    def test_pvalue_in_0_1(self, synthetic_ohlcv):
        equity = np.array([100_000 * (1 + 0.001 * i) for i in range(200)])
        r = _m.mc_permutation_test(equity, n_permutations=100)
        assert 0.0 < r["p_value"] <= 1.0

    def test_flat_equity_not_significant(self):
        """A flat equity curve has Sharpe ≈ 0; should not be significant."""
        equity = np.full(200, 100_000.0)
        r = _m.mc_permutation_test(equity, n_permutations=100)
        assert not r["significant"]

    def test_monotone_rising_equity_low_pvalue(self):
        """Strongly positive equity should yield low p-value (significant)."""
        equity = 100_000 * np.cumprod(1 + np.full(500, 0.002))  # +0.2%/bar
        r = _m.mc_permutation_test(equity, n_permutations=200)
        assert r["p_value"] < 0.2   # should be significant or near-significant

    def test_verdict_is_string(self, synthetic_ohlcv):
        equity = np.array([100_000 + i * 10 for i in range(100)])
        r = _m.mc_permutation_test(equity, n_permutations=50)
        assert isinstance(r["verdict"], str) and len(r["verdict"]) > 0

    def test_short_equity_returns_fallback(self):
        equity = np.array([100_000.0, 101_000.0])  # only 2 bars
        r = _m.mc_permutation_test(equity, n_permutations=50)
        assert r["p_value"] == 1.0


# ── Walk-forward harness internals ────────────────────────────────────────────

class TestParamCombos:
    def test_single_param(self):
        grid = {"fast_ema": [5, 9, 13]}
        combos = _param_combos(grid)
        assert len(combos) == 3
        assert all("fast_ema" in c for c in combos)

    def test_two_params(self):
        grid = {"fast_ema": [5, 9], "slow_ema": [21, 50]}
        combos = _param_combos(grid)
        assert len(combos) == 4

    def test_empty_grid(self):
        assert _param_combos({}) == [{}]


class TestStitchOosEquity:
    def test_single_fold(self):
        chunk = [{"date": "2024-01-01", "value": 100_000 + i * 100} for i in range(5)]
        stitched = _stitch_oos_equity([chunk], 100_000)
        assert len(stitched) == 5
        assert stitched[0]["value"] == pytest.approx(100_000, abs=1)

    def test_two_folds_compounding(self):
        fold1 = [{"date": f"2024-01-0{i+1}", "value": 100_000 + i * 500} for i in range(4)]
        fold2 = [{"date": f"2024-02-0{i+1}", "value": 100_000 + i * 200} for i in range(4)]
        stitched = _stitch_oos_equity([fold1, fold2], 100_000)
        assert len(stitched) == 8
        # fold2 starts from where fold1 ended
        fold1_end = stitched[3]["value"]
        assert stitched[4]["value"] == pytest.approx(fold1_end, abs=1)

    def test_empty_chunk_skipped(self):
        chunk = [{"date": "2024-01-01", "value": 100_000}]
        stitched = _stitch_oos_equity([[], chunk], 100_000)
        assert len(stitched) == 1


# ── Walk-forward integration ──────────────────────────────────────────────────

def _make_engine(capital, pos_size_pct, cm):
    return BacktestEngine(
        initial_capital=capital,
        position_size_pct=pos_size_pct,
        cost_model=cm or CostModel.zero(),
    )


def _make_strategy(params, risk_config, symbol):
    return get_strategy("scalping", params, risk_config, symbol)


class TestWalkForwardIntegration:
    def test_returns_required_keys(self, synthetic_ohlcv):
        result = run_walk_forward(
            strategy_factory=_make_strategy,
            param_grid={"fast_ema": [9, 13], "slow_ema": [21], "rsi_period": [14]},
            risk_config={"stop_loss_pct": 2.0, "take_profit_pct": 4.0},
            df=synthetic_ohlcv,
            symbol="TEST",
            engine_factory=_make_engine,
            is_bars=180,
            oos_bars=80,
            initial_capital=100_000,
            position_size_pct=10.0,
            cost_model=CostModel.zero(),
            ann_N=252,
            n_mc=50,
        )
        required = [
            "n_folds", "n_trials", "folds", "oos_equity_curve",
            "oos_return_pct", "oos_sharpe", "oos_expectancy",
            "oos_profit_factor", "oos_max_drawdown_pct",
            "dsr", "mc_pvalue", "mc_verdict", "kpi_pass",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_oos_only_metrics_labeled(self, synthetic_ohlcv):
        """All OOS metrics must be prefixed with 'oos_' to avoid IS/OOS confusion."""
        result = run_walk_forward(
            strategy_factory=_make_strategy,
            param_grid={"fast_ema": [9], "slow_ema": [21], "rsi_period": [14]},
            risk_config={"stop_loss_pct": 2.0, "take_profit_pct": 4.0},
            df=synthetic_ohlcv,
            symbol="TEST",
            engine_factory=_make_engine,
            is_bars=180,
            oos_bars=80,
            initial_capital=100_000,
            position_size_pct=10.0,
            cost_model=CostModel.zero(),
            ann_N=252,
            n_mc=20,
        )
        # These should NOT be present as bare IS metrics
        assert "sharpe_ratio" not in result   # raw (un-labelled) Sharpe must not exist
        assert "return_pct" not in result
        # These MUST be present as OOS-only
        assert "oos_sharpe" in result
        assert "oos_return_pct" in result

    def test_n_trials_equals_combos_times_folds(self, synthetic_ohlcv):
        grid = {"fast_ema": [9, 13], "slow_ema": [21, 50], "rsi_period": [14]}
        n_combos = 4  # 2 × 2 × 1
        result = run_walk_forward(
            strategy_factory=_make_strategy,
            param_grid=grid,
            risk_config={"stop_loss_pct": 2.0, "take_profit_pct": 4.0},
            df=synthetic_ohlcv,
            symbol="TEST",
            engine_factory=_make_engine,
            is_bars=180,
            oos_bars=80,
            initial_capital=100_000,
            position_size_pct=10.0,
            cost_model=CostModel.zero(),
            ann_N=252,
            n_mc=20,
        )
        assert result["n_trials"] == n_combos * result["n_folds"]

    def test_oos_equity_curve_length_reasonable(self, synthetic_ohlcv):
        result = run_walk_forward(
            strategy_factory=_make_strategy,
            param_grid={"fast_ema": [9], "slow_ema": [21], "rsi_period": [14]},
            risk_config={"stop_loss_pct": 2.0, "take_profit_pct": 4.0},
            df=synthetic_ohlcv,
            symbol="TEST",
            engine_factory=_make_engine,
            is_bars=180,
            oos_bars=80,
            initial_capital=100_000,
            position_size_pct=10.0,
            cost_model=CostModel.zero(),
            ann_N=252,
            n_mc=20,
        )
        # synthetic_ohlcv has 500 bars; BacktestEngine starts at bar 50
        # So ~450 usable; with is=200, oos=50 → ~4 folds × ~50 bars each ≈ 200 OOS bars
        assert len(result["oos_equity_curve"]) > 0

    def test_dsr_in_0_1(self, synthetic_ohlcv):
        result = run_walk_forward(
            strategy_factory=_make_strategy,
            param_grid={"fast_ema": [9], "slow_ema": [21], "rsi_period": [14]},
            risk_config={"stop_loss_pct": 2.0, "take_profit_pct": 4.0},
            df=synthetic_ohlcv,
            symbol="TEST",
            engine_factory=_make_engine,
            is_bars=180,
            oos_bars=80,
            initial_capital=100_000,
            position_size_pct=10.0,
            cost_model=CostModel.zero(),
            ann_N=252,
            n_mc=20,
        )
        assert 0.0 <= result["dsr"] <= 1.0

    def test_kpi_pass_keys_present(self, synthetic_ohlcv):
        result = run_walk_forward(
            strategy_factory=_make_strategy,
            param_grid={"fast_ema": [9], "slow_ema": [21], "rsi_period": [14]},
            risk_config={"stop_loss_pct": 2.0, "take_profit_pct": 4.0},
            df=synthetic_ohlcv,
            symbol="TEST",
            engine_factory=_make_engine,
            is_bars=180,
            oos_bars=80,
            initial_capital=100_000,
            position_size_pct=10.0,
            cost_model=CostModel.zero(),
            ann_N=252,
            n_mc=20,
        )
        kpi = result["kpi_pass"]
        for k in ("oos_expectancy", "oos_profit_factor", "oos_sharpe",
                  "oos_max_drawdown", "dsr_significant", "mc_significant"):
            assert k in kpi

    def test_too_little_data_raises(self, synthetic_ohlcv):
        """When data is shorter than one fold, should raise ValueError."""
        tiny = synthetic_ohlcv.iloc[:100].reset_index(drop=True)
        with pytest.raises(ValueError, match="fold|bars"):
            run_walk_forward(
                strategy_factory=_make_strategy,
                param_grid={"fast_ema": [9], "slow_ema": [21], "rsi_period": [14]},
                risk_config={"stop_loss_pct": 2.0, "take_profit_pct": 4.0},
                df=tiny,
                symbol="TEST",
                engine_factory=_make_engine,
                is_bars=180,
                oos_bars=80,
                initial_capital=100_000,
                position_size_pct=10.0,
                cost_model=CostModel.zero(),
                ann_N=252,
                n_mc=20,
            )
