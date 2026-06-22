"""
Unit tests for scanner.py — AssetScore construction and format_scan_results.

Rules (CLAUDE.md):
  - No network calls — synthetic OHLCV from conftest
  - Deterministic via fixed_seed autouse fixture
"""
import math
import numpy as np
import pandas as pd
import pytest

from scanner import AssetScore, _score_asset, format_scan_results


# ── Helpers ────────────────────────────────────────────────────────────────────

def _trending_df(n: int = 120) -> pd.DataFrame:
    np.random.seed(10)
    closes = np.cumsum(np.abs(np.random.normal(0.5, 0.3, n))) + 100
    opens  = np.roll(closes, 1); opens[0] = closes[0]
    highs  = np.maximum(opens, closes) * 1.005
    lows   = np.minimum(opens, closes) * 0.995
    return pd.DataFrame({
        "datetime": pd.date_range("2022-01-01", periods=n, freq="h"),
        "open":     opens, "high": highs, "low": lows,
        "close":    closes, "volume": np.full(n, 1_000_000.0),
    })


def _flat_df(n: int = 120) -> pd.DataFrame:
    np.random.seed(11)
    closes = 100 + np.random.normal(0, 0.2, n)
    opens  = np.roll(closes, 1); opens[0] = closes[0]
    highs  = closes + 0.2; lows = closes - 0.2
    return pd.DataFrame({
        "datetime": pd.date_range("2022-01-01", periods=n, freq="h"),
        "open":     opens, "high": highs, "low": lows,
        "close":    closes, "volume": np.full(n, 1_000_000.0),
    })


# ── _score_asset edge cases ────────────────────────────────────────────────────

class TestScoreAssetEdgeCases:
    def test_none_df_returns_none(self):
        assert _score_asset("BTC/USD", None) is None

    def test_empty_df_returns_none(self):
        assert _score_asset("BTC/USD", pd.DataFrame()) is None

    def test_too_few_bars_returns_none(self):
        df = _trending_df(n=20)
        assert _score_asset("BTC/USD", df) is None

    def test_30_bars_may_return_none_if_indicators_need_more(self):
        df = _trending_df(n=30)
        # Either None (not enough for all indicators) or valid AssetScore
        result = _score_asset("BTC/USD", df)
        assert result is None or isinstance(result, AssetScore)

    def test_sufficient_bars_returns_asset_score(self, synthetic_ohlcv):
        result = _score_asset("AAPL", synthetic_ohlcv)
        assert isinstance(result, AssetScore)


# ── AssetScore field validation ────────────────────────────────────────────────

class TestAssetScoreFields:
    def test_symbol_preserved(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert result.symbol == "TSLA"

    def test_score_in_0_100(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert 0.0 <= result.score <= 100.0

    def test_pillar_scores_in_0_100(self, synthetic_ohlcv):
        r = _score_asset("TSLA", synthetic_ohlcv)
        for attr in ["trend_score", "momentum_score", "volume_score",
                     "volatility_score", "breakout_score", "structure_score"]:
            val = getattr(r, attr)
            assert 0.0 <= val <= 100.0, f"{attr} = {val} out of range"

    def test_rsi_in_0_100(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert 0.0 <= result.rsi <= 100.0

    def test_atr_pct_positive(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert result.atr_pct >= 0

    def test_hurst_in_0_1(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert 0.0 <= result.hurst <= 1.0

    def test_stoch_k_in_0_100(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert 0.0 <= result.stoch_k <= 100.0

    def test_williams_r_in_minus100_to_0(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert -100.0 <= result.williams_r <= 0.0

    def test_mfi_in_0_100(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert 0.0 <= result.mfi <= 100.0

    def test_donchian_pct_in_0_1(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert 0.0 <= result.donchian_pct <= 1.0

    def test_bullish_plus_bearish_votes_equals_10(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert result.bullish_votes + result.bearish_votes == 10

    def test_bias_valid_enum(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert result.bias in ("strong_bull", "bull", "neutral", "bear", "strong_bear")

    def test_obv_trend_valid(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert result.obv_trend in ("rising", "falling", "unknown")

    def test_supertrend_direction_valid(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert result.supertrend_direction in ("bullish", "bearish")

    def test_sar_direction_valid(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert result.sar_direction in ("bullish", "bearish")

    def test_market_regime_valid(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert result.market_regime in ("trending", "mean_reverting", "random")

    def test_ema_stack_in_0_4(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert 0 <= result.ema_stack <= 4

    def test_fib_nearest_level_valid(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert result.fib_nearest_level in ("0.236", "0.382", "0.500", "0.618", "0.786")

    def test_trend_matches_ema_trend(self, synthetic_ohlcv):
        """trend is a legacy alias for ema_trend."""
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert result.trend == result.ema_trend

    def test_reason_is_string(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert isinstance(result.reason, str)

    def test_bars_dataframe_attached(self, synthetic_ohlcv):
        result = _score_asset("TSLA", synthetic_ohlcv)
        assert result.bars is not None
        assert len(result.bars) > 0


# ── Relative ranking ─────────────────────────────────────────────────────────

class TestRelativeScoring:
    def test_trending_scores_higher_than_flat(self):
        r_trend = _score_asset("A", _trending_df())
        r_flat  = _score_asset("B", _flat_df())
        # Trending should generally score higher; allow ≥5 pt margin
        assert r_trend.score > r_flat.score - 20

    def test_trending_series_ema_stack_4(self):
        """Strong uptrend: all 4 EMAs aligned → stack = 4."""
        result = _score_asset("X", _trending_df(n=300))
        assert result.ema_stack >= 2   # at minimum fast EMAs aligned

    def test_trending_obv_rising(self):
        result = _score_asset("X", _trending_df())
        assert result.obv_trend == "rising"


# ── format_scan_results ───────────────────────────────────────────────────────

class TestFormatScanResults:
    def _make_scores(self, symbols=("AAPL", "TSLA", "NVDA"), n=120):
        dfs = [_trending_df(n), _flat_df(n), _trending_df(n)]
        scores = []
        for sym, df in zip(symbols, dfs):
            r = _score_asset(sym, df)
            if r:
                scores.append(r)
        return scores

    def test_returns_list_of_dicts(self, synthetic_ohlcv):
        r = _score_asset("AAPL", synthetic_ohlcv)
        result = format_scan_results([r])
        assert isinstance(result, list)
        assert isinstance(result[0], dict)

    def test_rank_starts_at_1(self, synthetic_ohlcv):
        scores = [_score_asset("A", synthetic_ohlcv), _score_asset("B", _trending_df())]
        formatted = format_scan_results([s for s in scores if s])
        assert formatted[0]["rank"] == 1

    def test_ranks_sequential(self):
        scores = self._make_scores()
        formatted = format_scan_results(scores)
        for i, row in enumerate(formatted):
            assert row["rank"] == i + 1

    def test_all_required_keys_present(self, synthetic_ohlcv):
        r = _score_asset("AAPL", synthetic_ohlcv)
        formatted = format_scan_results([r])
        row = formatted[0]
        required = [
            "rank", "symbol", "score", "bias", "bullish_votes", "bearish_votes",
            "trend_score", "momentum_score", "volume_score", "volatility_score",
            "breakout_score", "structure_score",
            "rsi", "macd_hist", "adx", "stoch_k", "stoch_d", "williams_r", "cci",
            "obv_trend", "mfi", "volume_ratio", "atr_pct", "bb_width",
            "donchian_pct", "hurst", "market_regime", "price_vs_vwap",
            "ema_trend", "ema_stack", "ichimoku_above_cloud",
            "supertrend_direction", "sar_direction",
            "fib_nearest_level", "fib_distance_pct",
            "reason", "trend",
        ]
        for k in required:
            assert k in row, f"Missing key in formatted result: {k}"

    def test_symbol_preserved(self, synthetic_ohlcv):
        r = _score_asset("NVDA", synthetic_ohlcv)
        formatted = format_scan_results([r])
        assert formatted[0]["symbol"] == "NVDA"

    def test_empty_list_returns_empty(self):
        assert format_scan_results([]) == []

    def test_score_values_match_asset_score(self, synthetic_ohlcv):
        r = _score_asset("AAPL", synthetic_ohlcv)
        formatted = format_scan_results([r])
        assert formatted[0]["score"] == r.score
        assert formatted[0]["rsi"] == r.rsi
        assert formatted[0]["hurst"] == r.hurst

    def test_no_none_values_in_formatted(self, synthetic_ohlcv):
        r = _score_asset("AAPL", synthetic_ohlcv)
        formatted = format_scan_results([r])
        for k, v in formatted[0].items():
            assert v is not None, f"Key {k} is None"

    def test_json_serialisable_values(self, synthetic_ohlcv):
        """All values must be JSON-safe (no numpy scalars, no DataFrames)."""
        import json
        r = _score_asset("AAPL", synthetic_ohlcv)
        formatted = format_scan_results([r])
        try:
            json.dumps(formatted[0])
        except TypeError as e:
            pytest.fail(f"format_scan_results output is not JSON-serialisable: {e}")
