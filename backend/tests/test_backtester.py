"""
Unit tests for backtester.py — BacktestEngine.run() and _build_report().

Rules (CLAUDE.md):
  - No lookahead: signals at bar i only use data up to bar i
  - Costs are ON by default; zero-cost run must be explicit
  - set_seed() makes runs reproducible
  - Primary metrics: expectancy, profit_factor (not win_rate)
  - DSR must accompany any Sharpe figure
  - All tests use synthetic OHLCV (no network)
"""
import numpy as np
import pandas as pd
import pytest

from backtester import BacktestEngine, BtTrade
from strategies.macd import MACDStrategy
from strategies.ma_crossover import MACrossoverStrategy
from strategies.rsi import RSIStrategy
from cost_model import CostModel


# ── Helpers ────────────────────────────────────────────────────────────────────

def _df(n: int = 500, trend: float = 0.001) -> pd.DataFrame:
    """
    Synthetic OHLCV with gentle upward drift (trend > 0) or downtrend (< 0).
    n must be > 55 (engine requirement).
    """
    np.random.seed(42)
    log_ret = np.random.normal(trend, 0.015, n)
    closes  = 100.0 * np.exp(np.cumsum(log_ret))
    opens   = np.roll(closes, 1); opens[0] = closes[0]
    highs   = np.maximum(opens, closes) * (1 + np.abs(np.random.normal(0, 0.005, n)))
    lows    = np.minimum(opens, closes) * (1 - np.abs(np.random.normal(0, 0.005, n)))
    return pd.DataFrame({
        "datetime": pd.date_range("2020-01-01", periods=n, freq="D"),
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": np.full(n, 1_000_000.0),
    })


def _engine(**kwargs) -> BacktestEngine:
    defaults = {"initial_capital": 100_000.0, "position_size_pct": 10.0}
    defaults.update(kwargs)
    return BacktestEngine(**defaults)


def _macd() -> MACDStrategy:
    return MACDStrategy("TEST", {"fast_period": 12, "slow_period": 26, "signal_period": 9},
                        {"stop_loss_pct": 2.0, "take_profit_pct": 4.0, "position_size_pct": 10.0})


def _ma() -> MACrossoverStrategy:
    return MACrossoverStrategy("TEST", {"fast_ma": 10, "slow_ma": 30, "ma_type": "EMA"},
                               {"stop_loss_pct": 2.0, "take_profit_pct": 4.0, "position_size_pct": 10.0})


def _rsi() -> RSIStrategy:
    return RSIStrategy("TEST", {"rsi_period": 14, "oversold_level": 30, "overbought_level": 70},
                       {"stop_loss_pct": 2.0, "take_profit_pct": 4.0, "position_size_pct": 10.0})


# ── Minimum bars check ────────────────────────────────────────────────────────

class TestMinBarsValidation:
    def test_raises_on_too_few_bars(self):
        with pytest.raises(ValueError, match="55"):
            _engine().run(_macd(), _df(n=40), "TEST")

    def test_exactly_55_bars_raises_on_edge(self):
        """55 bars is the minimum; exactly 55 should not raise (runs warmup through all)."""
        # With only 5 tradeable bars (50 warmup + 5 active) the engine may produce
        # no trades but must not raise.
        result = _engine().run(_macd(), _df(n=55), "TEST")
        assert "total_trades" in result

    def test_sufficient_bars_returns_dict(self):
        result = _engine().run(_macd(), _df(n=200), "TEST")
        assert isinstance(result, dict)


# ── Report structure ─────────────────────────────────────────────────────────

class TestReportStructure:
    REQUIRED_KEYS = [
        "strategy_type", "symbol", "strategy_name", "start_date", "end_date",
        "initial_capital", "final_capital", "total_return_pct",
        "gross_return_pct", "total_costs_paid", "benchmark_return_pct", "alpha_pct",
        "annualization_n", "cost_model", "position_sizer", "regime_gate",
        "total_trades", "winning_trades", "losing_trades",
        # Primary KPIs
        "expectancy", "profit_factor", "payoff_ratio",
        # Secondary
        "max_drawdown_pct", "sharpe_ratio", "sortino_ratio", "calmar_ratio",
        # Informational
        "win_rate", "low_sample_warning",
        # Data
        "equity_curve", "monthly_returns", "trades",
        # Statistical significance (CLAUDE.md)
        "deflated_sharpe_ratio", "mc_permutation",
        # KPI gates
        "kpi_pass",
    ]

    @pytest.fixture(scope="class")
    def report(self):
        return _engine().run(_macd(), _df(n=500), "AAPL")

    def test_all_required_keys_present(self, report):
        for k in self.REQUIRED_KEYS:
            assert k in report, f"Missing key: {k}"

    def test_strategy_type_is_single(self, report):
        assert report["strategy_type"] == "single"

    def test_initial_capital_preserved(self, report):
        assert report["initial_capital"] == 100_000.0

    def test_kpi_pass_has_required_keys(self, report):
        for k in ("expectancy", "profit_factor", "max_drawdown", "sharpe", "dsr"):
            assert k in report["kpi_pass"]

    def test_kpi_pass_values_are_bool(self, report):
        for k, v in report["kpi_pass"].items():
            assert isinstance(v, bool), f"kpi_pass[{k!r}] is not bool"

    def test_equity_curve_has_required_fields(self, report):
        assert len(report["equity_curve"]) > 0
        row = report["equity_curve"][0]
        for k in ("date", "value", "gross", "bah"):
            assert k in row

    def test_trade_log_fields(self, report):
        if report["trades"]:
            t = report["trades"][0]
            for k in ("entry_date", "exit_date", "side", "entry_price",
                      "exit_price", "pnl", "gross_pnl", "cost_paid", "pnl_pct", "reason"):
                assert k in t

    def test_mc_permutation_has_required_fields(self, report):
        mc = report["mc_permutation"]
        for k in ("p_value", "significant", "verdict"):
            assert k in mc

    def test_mc_pvalue_in_0_1(self, report):
        assert 0.0 <= report["mc_permutation"]["p_value"] <= 1.0

    def test_dsr_in_0_1(self, report):
        assert 0.0 <= report["deflated_sharpe_ratio"] <= 1.0

    def test_win_rate_in_0_100(self, report):
        assert 0.0 <= report["win_rate"] <= 100.0

    def test_max_drawdown_pct_nonnegative(self, report):
        assert report["max_drawdown_pct"] >= 0.0

    def test_trade_counts_consistent(self, report):
        assert report["winning_trades"] + report["losing_trades"] <= report["total_trades"]


# ── Cost model correctness ───────────────────────────────────────────────────

class TestCostModelApplied:
    def test_costs_on_by_default(self):
        """total_costs_paid > 0 when there are trades and real costs are applied."""
        r = _engine().run(_ma(), _df(n=500), "TEST")
        if r["total_trades"] > 0:
            assert r["total_costs_paid"] > 0.0

    def test_zero_cost_run_has_no_costs(self):
        """CostModel.zero() → gross == net and total_costs_paid == 0."""
        engine = BacktestEngine(
            initial_capital=100_000.0,
            cost_model=CostModel.zero(),
            position_size_pct=10.0,
        )
        r = engine.run(_ma(), _df(n=500), "TEST")
        assert r["total_costs_paid"] == pytest.approx(0.0, abs=0.01)

    def test_gross_return_gte_net_return(self):
        """Gross return (pre-costs) is always ≥ net return (post-costs)."""
        r = _engine().run(_ma(), _df(n=500), "TEST")
        assert r["gross_return_pct"] >= r["total_return_pct"] - 0.01

    def test_cost_model_dict_present(self):
        r = _engine().run(_macd(), _df(n=500), "TEST")
        cm = r["cost_model"]
        assert "commission_bps" in cm
        assert "half_spread_bps" in cm


# ── Reproducibility (CLAUDE.md: runs must be deterministic) ──────────────────

class TestReproducibility:
    def test_same_data_same_result(self):
        """Two runs on identical data must produce identical metrics."""
        df = _df(n=500)
        r1 = _engine().run(_macd(), df.copy(), "TEST")
        r2 = _engine().run(_macd(), df.copy(), "TEST")
        assert r1["total_trades"]     == r2["total_trades"]
        assert r1["total_return_pct"] == r2["total_return_pct"]
        assert r1["sharpe_ratio"]     == r2["sharpe_ratio"]
        assert r1["expectancy"]       == r2["expectancy"]

    def test_equity_curve_reproducible(self):
        df = _df(n=500)
        r1 = _engine().run(_ma(), df.copy(), "TEST")
        r2 = _engine().run(_ma(), df.copy(), "TEST")
        assert r1["equity_curve"] == r2["equity_curve"]


# ── No-lookahead guarantee ────────────────────────────────────────────────────

class TestNoLookahead:
    def test_signals_generated_with_prefix_data(self):
        """
        Verify signal at bar i only sees data up to bar i by counting calls.
        We instrument the strategy to record how many bars it sees each call.
        """
        bars_seen = []

        class RecordingStrategy(MACDStrategy):
            def generate_signal(self, df: pd.DataFrame):
                bars_seen.append(len(df))
                return super().generate_signal(df)

        strat = RecordingStrategy("T", {"fast_period": 12, "slow_period": 26, "signal_period": 9},
                                  {"stop_loss_pct": 2.0, "take_profit_pct": 4.0})
        n = 300
        _engine().run(strat, _df(n=n), "T")

        # Each call should see a strictly increasing window
        for i in range(1, len(bars_seen)):
            assert bars_seen[i] > bars_seen[i - 1], \
                f"Call {i} saw {bars_seen[i]} bars but previous call saw {bars_seen[i-1]}"

        # Maximum bars seen = n (all bars)
        assert max(bars_seen) == n


# ── Trade log correctness ─────────────────────────────────────────────────────

class TestTradeLog:
    @pytest.fixture(scope="class")
    def report(self):
        # Oscillating prices generate more trades for RSI strategy
        np.random.seed(0)
        n = 500
        t = np.linspace(0, 4 * np.pi, n)
        closes = 100 + 20 * np.sin(t) + np.random.normal(0, 1, n)
        opens = np.roll(closes, 1); opens[0] = closes[0]
        highs = np.maximum(opens, closes) * 1.003
        lows  = np.minimum(opens, closes) * 0.997
        df = pd.DataFrame({
            "datetime": pd.date_range("2020-01-01", periods=n, freq="D"),
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": np.full(n, 1_000_000.0),
        })
        return _engine().run(_rsi(), df, "TEST")

    def test_trade_side_valid(self, report):
        for t in report["trades"]:
            assert t["side"] in ("long", "short")

    def test_trade_reason_valid(self, report):
        valid = {"stop_loss", "take_profit", "signal", "end_of_data"}
        for t in report["trades"]:
            assert t["reason"] in valid, f"Unexpected reason: {t['reason']}"

    def test_exit_after_entry_date(self, report):
        for t in report["trades"]:
            assert t["exit_date"] >= t["entry_date"]

    def test_winning_losing_counts(self, report):
        winners = sum(1 for t in report["trades"] if t["pnl"] > 0)
        losers  = sum(1 for t in report["trades"] if t["pnl"] <= 0)
        assert report["winning_trades"] == winners
        assert report["losing_trades"]  == losers

    def test_total_trades_matches_list(self, report):
        assert report["total_trades"] == len(report["trades"])

    def test_costs_per_trade_nonnegative(self, report):
        """cost_paid should be ≥ 0 for each trade (costs are always positive drag)."""
        for t in report["trades"]:
            assert t["cost_paid"] >= -0.01, f"Negative cost_paid: {t['cost_paid']}"


# ── Low sample warning ────────────────────────────────────────────────────────

class TestLowSampleWarning:
    def test_low_sample_flag_when_few_trades(self):
        """With very tight parameters the strategy may generate < 30 trades."""
        strat = RSIStrategy("T", {"rsi_period": 14, "oversold_level": 1, "overbought_level": 99},
                            {"stop_loss_pct": 2.0, "take_profit_pct": 4.0})
        r = _engine().run(strat, _df(n=500), "T")
        # Either low_sample_warning is True (< 30 trades) or the count matches
        if r["total_trades"] < 30:
            assert r["low_sample_warning"] is True
        else:
            assert r["low_sample_warning"] is False

    def test_enough_trades_no_warning(self):
        """Oscillating data with RSI at normal levels should generate 30+ trades."""
        np.random.seed(7)
        n = 1000
        t = np.linspace(0, 8 * np.pi, n)
        closes = 100 + 25 * np.sin(t) + np.random.normal(0, 0.5, n)
        opens  = np.roll(closes, 1); opens[0] = closes[0]
        df = pd.DataFrame({
            "datetime": pd.date_range("2018-01-01", periods=n, freq="D"),
            "open": opens, "high": closes * 1.003, "low": closes * 0.997,
            "close": closes, "volume": np.full(n, 1_000_000.0),
        })
        r = _engine().run(_rsi(), df, "T")
        if r["total_trades"] >= 30:
            assert r["low_sample_warning"] is False


# ── Benchmark / alpha ─────────────────────────────────────────────────────────

class TestBenchmark:
    def test_benchmark_return_present(self):
        r = _engine().run(_macd(), _df(n=500), "TEST")
        assert "benchmark_return_pct" in r
        assert "alpha_pct" in r

    def test_equity_curve_has_bah_column(self):
        r = _engine().run(_macd(), _df(n=500), "TEST")
        for row in r["equity_curve"][:5]:
            assert "bah" in row

    def test_alpha_equals_return_minus_benchmark(self):
        r = _engine().run(_macd(), _df(n=500), "TEST")
        expected = round(r["total_return_pct"] - r["benchmark_return_pct"], 2)
        assert r["alpha_pct"] == pytest.approx(expected, abs=0.05)


# ── Monthly returns ───────────────────────────────────────────────────────────

class TestMonthlyReturns:
    def test_monthly_returns_present(self):
        r = _engine().run(_macd(), _df(n=500), "TEST")
        assert isinstance(r["monthly_returns"], list)

    def test_monthly_return_fields(self):
        r = _engine().run(_macd(), _df(n=500), "TEST")
        if r["monthly_returns"]:
            m = r["monthly_returns"][0]
            assert "month" in m
            assert "return_pct" in m

    def test_monthly_return_format(self):
        r = _engine().run(_macd(), _df(n=500), "TEST")
        for m in r["monthly_returns"]:
            assert len(m["month"]) == 7      # "YYYY-MM"
            assert m["month"][4] == "-"


# ── BtTrade dataclass ────────────────────────────────────────────────────────

class TestBtTrade:
    def test_default_exit_bar(self):
        t = BtTrade(entry_bar=5, symbol="TEST", side="long",
                    entry_price=100.0, entry_mid=100.0,
                    qty=10.0, entry_date="2020-01-01")
        assert t.exit_bar == -1
        assert t.pnl == 0.0
        assert t.gross_pnl == 0.0

    def test_pnl_fields_initialise_zero(self):
        t = BtTrade(entry_bar=0, symbol="X", side="short",
                    entry_price=50.0, entry_mid=50.0,
                    qty=5.0, entry_date="2021-06-01")
        assert t.cost_paid == 0.0
        assert t.pnl_pct == 0.0
        assert t.reason == ""
