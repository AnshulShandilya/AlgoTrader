"""
Unit tests for metrics.py.

All assertions use hand-computed expected values.
No network calls; uses only the conftest fixtures.
"""
import numpy as np
import pytest

from metrics import (
    expectancy,
    profit_factor,
    payoff_ratio,
    sharpe,
    sortino,
    max_drawdown,
    calmar,
    compute_all,
)


# ── Trade-level metrics ───────────────────────────────────────────────────────

class TestExpectancy:
    def test_hand_computed(self, known_trade_log):
        # 6 wins × +200, 4 losses × -100
        # E = (0.6 × 200) + (0.4 × -100) = 120 − 40 = 80.0
        assert expectancy(known_trade_log) == pytest.approx(80.0, abs=0.01)

    def test_all_winners(self):
        assert expectancy([100, 200, 150]) == pytest.approx(150.0, abs=0.01)

    def test_all_losers(self):
        assert expectancy([-50, -100]) == pytest.approx(-75.0, abs=0.01)

    def test_empty(self):
        assert expectancy([]) == 0.0

    def test_breakeven(self):
        # One win +100, one loss -100 → E = 0
        assert expectancy([100, -100]) == pytest.approx(0.0, abs=0.01)

    def test_negative_edge(self):
        # 3 wins +1, 7 losses -10 → E = (0.3×1) + (0.7×-10) = -6.7
        result = expectancy([1, 1, 1, -10, -10, -10, -10, -10, -10, -10])
        assert result == pytest.approx(-6.7, abs=0.01)


class TestProfitFactor:
    def test_hand_computed(self, known_trade_log):
        # gross_profit = 6×200 = 1200; gross_loss = 4×100 = 400; PF = 3.0
        assert profit_factor(known_trade_log) == pytest.approx(3.0, abs=0.001)

    def test_breakeven(self):
        assert profit_factor([100, -100]) == pytest.approx(1.0, abs=0.001)

    def test_no_losers(self):
        # No losing trades → return 0 (not inf) per CLAUDE.md
        assert profit_factor([100, 200]) == 0.0

    def test_no_winners(self):
        assert profit_factor([-50, -100]) == 0.0

    def test_empty(self):
        assert profit_factor([]) == 0.0


class TestPayoffRatio:
    def test_hand_computed(self, known_trade_log):
        # avg_win = 200, avg_loss = 100 → ratio = 2.0
        assert payoff_ratio(known_trade_log) == pytest.approx(2.0, abs=0.001)

    def test_low_payoff_high_winrate(self):
        # 9 wins × +1, 1 loss × -5 → payoff = 1/5 = 0.2
        pnls = [1] * 9 + [-5]
        assert payoff_ratio(pnls) == pytest.approx(0.2, abs=0.001)

    def test_no_losses(self):
        assert payoff_ratio([100, 200]) == 0.0


# ── Equity-curve metrics ──────────────────────────────────────────────────────

class TestMaxDrawdown:
    def test_flat(self):
        assert max_drawdown(np.array([100.0, 100.0, 100.0])) == pytest.approx(0.0)

    def test_monotone_rising(self):
        assert max_drawdown(np.array([100.0, 110.0, 120.0])) == pytest.approx(0.0)

    def test_single_trough(self):
        # Peak 120, trough 60 → DD = (120-60)/120 × 100 = 50%
        eq = np.array([100.0, 120.0, 60.0, 90.0])
        assert max_drawdown(eq) == pytest.approx(50.0, abs=0.01)

    def test_short_series(self):
        assert max_drawdown(np.array([100.0])) == 0.0


class TestSharpe:
    def test_flat_equity(self):
        # No returns → undefined; function returns 0
        assert sharpe(np.full(100, 1000.0)) == 0.0

    def test_positive_drift(self, synthetic_ohlcv):
        # Monotone rising equity should have positive Sharpe
        eq = np.linspace(100, 200, 500)
        assert sharpe(eq, ann_N=365) > 0

    def test_negative_drift(self):
        eq = np.linspace(200, 100, 500)
        assert sharpe(eq, ann_N=365) < 0


class TestCalmar:
    def test_basic(self):
        assert calmar(30.0, 10.0) == pytest.approx(3.0, abs=0.001)

    def test_zero_dd(self):
        assert calmar(30.0, 0.0) == 0.0


# ── Reproducibility ───────────────────────────────────────────────────────────

class TestReproducibility:
    def test_backtest_is_deterministic(self, synthetic_ohlcv):
        """
        Running the backtest engine twice on the same data must produce
        byte-identical equity curves and metrics.
        """
        import sys
        sys.path.insert(0, ".")
        from seed import set_seed
        from backtester import BacktestEngine
        from strategies import get_strategy

        risk = {"stop_loss_pct": 2.0, "take_profit_pct": 4.0}
        params = {"fast_ema": 9, "slow_ema": 21, "rsi_period": 14}

        set_seed(42)
        strat1 = get_strategy("scalping", params, risk, "TEST")
        engine1 = BacktestEngine(initial_capital=100_000, commission_pct=0.1, position_size_pct=10.0)
        r1 = engine1.run(strat1, synthetic_ohlcv.copy(), "TEST")

        set_seed(42)
        strat2 = get_strategy("scalping", params, risk, "TEST")
        engine2 = BacktestEngine(initial_capital=100_000, commission_pct=0.1, position_size_pct=10.0)
        r2 = engine2.run(strat2, synthetic_ohlcv.copy(), "TEST")

        assert r1["total_return_pct"] == r2["total_return_pct"]
        assert r1["sharpe_ratio"]     == r2["sharpe_ratio"]
        assert r1["expectancy"]       == r2["expectancy"]
        assert r1["profit_factor"]    == r2["profit_factor"]

    def test_metrics_are_deterministic(self, known_trade_log):
        """Running metrics twice gives the same answer (no stochastic state)."""
        e1 = expectancy(known_trade_log)
        e2 = expectancy(known_trade_log)
        assert e1 == e2

        pf1 = profit_factor(known_trade_log)
        pf2 = profit_factor(known_trade_log)
        assert pf1 == pf2


# ── compute_all convenience function ─────────────────────────────────────────

class TestComputeAll:
    def test_returns_all_keys(self, known_trade_log):
        equity = np.linspace(100_000, 110_000, 100)
        result = compute_all(known_trade_log, equity, 10.0, ann_N=252)
        required = {"expectancy", "profit_factor", "payoff_ratio",
                    "sharpe_ratio", "sortino_ratio", "calmar_ratio", "max_drawdown_pct"}
        assert required.issubset(result.keys())

    def test_values_consistent(self, known_trade_log):
        equity = np.linspace(100_000, 110_000, 100)
        result = compute_all(known_trade_log, equity, 10.0, ann_N=252)
        # Expectancy and profit_factor should match standalone functions
        assert result["expectancy"]    == pytest.approx(expectancy(known_trade_log), abs=1e-9)
        assert result["profit_factor"] == pytest.approx(profit_factor(known_trade_log), abs=1e-9)
