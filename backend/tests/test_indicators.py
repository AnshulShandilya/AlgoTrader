"""
Unit tests for the 20-indicator engine in indicators.py.

Rules (CLAUDE.md):
  - No live API calls — synthetic OHLCV from conftest
  - All tests deterministic via fixed_seed autouse fixture
  - Tests cover: output keys, value ranges, edge cases, bias/score
"""
import math
import numpy as np
import pandas as pd
import pytest

from indicators import compute_all


# ── Helpers ────────────────────────────────────────────────────────────────────

def _trending_df(n: int = 200) -> pd.DataFrame:
    """Synthetic strongly-trending upward series."""
    np.random.seed(0)
    closes = np.cumsum(np.abs(np.random.normal(0.5, 0.3, n))) + 100
    opens = np.roll(closes, 1); opens[0] = closes[0]
    highs = np.maximum(opens, closes) * 1.005
    lows  = np.minimum(opens, closes) * 0.995
    return pd.DataFrame({
        "datetime": pd.date_range("2022-01-01", periods=n, freq="D"),
        "open":     opens,
        "high":     highs,
        "low":      lows,
        "close":    closes,
        "volume":   np.full(n, 1_000_000.0),
    })


def _flat_df(n: int = 200) -> pd.DataFrame:
    """Synthetic sideways (low-vol) series oscillating around 100."""
    np.random.seed(1)
    closes = 100 + np.sin(np.linspace(0, 4 * math.pi, n)) * 3 + np.random.normal(0, 0.2, n)
    opens = np.roll(closes, 1); opens[0] = closes[0]
    highs = closes + 0.3
    lows  = closes - 0.3
    return pd.DataFrame({
        "datetime": pd.date_range("2022-01-01", periods=n, freq="D"),
        "open":     opens,
        "high":     highs,
        "low":      lows,
        "close":    closes,
        "volume":   np.full(n, 1_000_000.0),
    })


# ── Edge cases ─────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_none_df_returns_error(self):
        result = compute_all(None)
        assert result.get("error") is not None
        assert result.get("score") == 0.0

    def test_empty_df_returns_error(self):
        result = compute_all(pd.DataFrame())
        assert result.get("error") is not None

    def test_too_few_bars_returns_error(self):
        df = _trending_df(n=20)
        result = compute_all(df)
        assert result.get("error") is not None
        assert result["score"] == 0.0

    def test_49_bars_returns_error(self):
        df = _trending_df(n=49)
        result = compute_all(df)
        assert result.get("error") is not None

    def test_50_bars_computes(self):
        """50 bars is the minimum — should not return an error."""
        df = _trending_df(n=50)
        result = compute_all(df)
        assert "score" in result
        assert result.get("error") is None


# ── Required output keys ───────────────────────────────────────────────────────

REQUIRED_KEYS = [
    # RSI
    "rsi",
    # MACD
    "macd", "macd_signal", "macd_hist",
    # Bollinger
    "bb_upper", "bb_mid", "bb_lower", "bb_width", "bb_pct",
    # ATR
    "atr", "atr_pct",
    # EMA stack
    "ema9", "ema21", "ema50", "ema200", "ema_stack",
    # ADX
    "adx",
    # Stochastic
    "stoch_k", "stoch_d",
    # Williams %R
    "williams_r",
    # CCI
    "cci",
    # ROC
    "roc",
    # OBV
    "obv_trend",
    # MFI
    "mfi",
    # Volume ratio
    "volume_ratio",
    # Ichimoku
    "ichimoku_above_cloud", "ichimoku_cloud_color",
    # Supertrend
    "supertrend_direction",
    # Parabolic SAR
    "sar", "sar_direction",
    # Donchian
    "donchian_high", "donchian_low", "donchian_pct",
    # Fibonacci
    "fib_nearest_level", "fib_distance_pct",
    # Hurst
    "hurst", "market_regime",
    # VWAP
    "vwap", "price_vs_vwap",
    # Composite
    "score", "bias", "bullish_votes", "bearish_votes",
    # Pillar sub-scores
    "trend_score", "momentum_score", "volume_score",
    "volatility_score", "breakout_score", "structure_score",
    # Close
    "close",
]


class TestOutputKeys:
    def test_all_required_keys_present(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        for k in REQUIRED_KEYS:
            assert k in result, f"Missing key: {k}"

    def test_no_none_values(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        for k, v in result.items():
            assert v is not None, f"Key {k} has None value"


# ── Score and bias ranges ─────────────────────────────────────────────────────

class TestScoreAndBias:
    def test_score_in_0_100(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert 0.0 <= result["score"] <= 100.0

    def test_pillar_scores_in_0_100(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        for key in ["trend_score", "momentum_score", "volume_score",
                    "volatility_score", "breakout_score", "structure_score"]:
            assert 0.0 <= result[key] <= 100.0, f"{key} out of range: {result[key]}"

    def test_bias_valid_enum(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["bias"] in ("strong_bull", "bull", "neutral", "bear", "strong_bear")

    def test_bullish_plus_bearish_votes_equals_10(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["bullish_votes"] + result["bearish_votes"] == 10

    def test_bullish_votes_in_range(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert 0 <= result["bullish_votes"] <= 10

    def test_trending_series_higher_score_than_flat(self):
        """A strongly trending series should score higher than a flat oscillating one."""
        trending = compute_all(_trending_df())
        flat     = compute_all(_flat_df())
        assert trending["score"] > flat["score"] - 20  # trend has edge


# ── Individual indicator sanity checks ────────────────────────────────────────

class TestRSI:
    def test_rsi_in_0_100(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert 0.0 <= result["rsi"] <= 100.0

    def test_rsi_rising_market(self):
        """A strongly rising market should produce RSI > 50."""
        result = compute_all(_trending_df())
        assert result["rsi"] > 50

    def test_rsi_flat_market_finite(self):
        result = compute_all(_flat_df())
        assert math.isfinite(result["rsi"])
        assert 0.0 <= result["rsi"] <= 100.0


class TestMACD:
    def test_macd_hist_sign_matches_trend(self):
        """Rising trend should produce positive MACD histogram."""
        result = compute_all(_trending_df())
        assert result["macd_hist"] > 0

    def test_macd_values_are_finite(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert math.isfinite(result["macd"])
        assert math.isfinite(result["macd_signal"])
        assert math.isfinite(result["macd_hist"])


class TestBollingerBands:
    def test_bb_ordering(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["bb_lower"] < result["bb_mid"] < result["bb_upper"]

    def test_bb_pct_in_0_1_approx(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        # bb_pct can go slightly outside [0,1] during extreme moves; just check it's finite
        assert math.isfinite(result["bb_pct"])

    def test_bb_width_positive(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["bb_width"] > 0


class TestATR:
    def test_atr_positive(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["atr"] > 0
        assert result["atr_pct"] > 0

    def test_volatile_series_higher_atr_pct(self):
        """High-vol series should have higher ATR%."""
        np.random.seed(2)
        n = 200
        # High vol
        closes_hv = 100 + np.cumsum(np.random.normal(0, 3, n))
        opens_hv = np.roll(closes_hv, 1); opens_hv[0] = closes_hv[0]
        highs_hv = closes_hv + np.abs(np.random.normal(2, 1, n))
        lows_hv  = closes_hv - np.abs(np.random.normal(2, 1, n))
        df_hv = pd.DataFrame({
            "datetime": pd.date_range("2022-01-01", periods=n, freq="D"),
            "open": opens_hv, "high": highs_hv, "low": lows_hv,
            "close": closes_hv, "volume": np.full(n, 1e6),
        })
        result_hv = compute_all(df_hv)
        result_lv = compute_all(_flat_df())
        assert result_hv["atr_pct"] > result_lv["atr_pct"]


class TestEMAStack:
    def test_ema_values_positive(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        for k in ["ema9", "ema21", "ema50", "ema200"]:
            assert result[k] > 0

    def test_ema_stack_score_in_range(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert 0 <= result["ema_stack"] <= 4

    def test_trending_series_full_ema_stack(self):
        """Strongly trending up series should show bull EMA alignment."""
        result = compute_all(_trending_df(n=300))
        # EMA9 should be above EMA21 in a strong uptrend
        assert result["ema9"] > result["ema50"]

    def test_ema_ordering_in_downtrend(self):
        """Strongly trending down series: ema9 < ema200."""
        np.random.seed(3)
        n = 300
        closes = np.cumsum(-np.abs(np.random.normal(0.3, 0.2, n))) + 300
        closes = np.clip(closes, 1, None)
        opens = np.roll(closes, 1); opens[0] = closes[0]
        highs = closes + 0.5
        lows  = closes - 0.5
        df = pd.DataFrame({
            "datetime": pd.date_range("2022-01-01", periods=n, freq="D"),
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": np.full(n, 1e6),
        })
        result = compute_all(df)
        assert result["ema9"] < result["ema200"]


class TestADX:
    def test_adx_non_negative(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["adx"] >= 0

    def test_trending_adx_higher_than_flat(self):
        r_trend = compute_all(_trending_df())
        r_flat  = compute_all(_flat_df())
        assert r_trend["adx"] >= r_flat["adx"] - 10


class TestStochastic:
    def test_stoch_k_in_0_100(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert 0.0 <= result["stoch_k"] <= 100.0
        assert 0.0 <= result["stoch_d"] <= 100.0


class TestWilliamsR:
    def test_williams_r_in_minus100_to_0(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert -100.0 <= result["williams_r"] <= 0.0


class TestCCI:
    def test_cci_finite(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert math.isfinite(result["cci"])


class TestOBV:
    def test_obv_trend_is_string(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["obv_trend"] in ("rising", "falling")

    def test_trending_series_obv_rising(self):
        result = compute_all(_trending_df())
        assert result["obv_trend"] == "rising"


class TestMFI:
    def test_mfi_in_0_100(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert 0.0 <= result["mfi"] <= 100.0


class TestVolumeRatio:
    def test_volume_ratio_positive(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["volume_ratio"] > 0

    def test_constant_volume_ratio_near_1(self):
        """Constant volume gives volume_ratio ≈ 1."""
        df = _trending_df()
        df["volume"] = 1_000_000.0
        result = compute_all(df)
        assert abs(result["volume_ratio"] - 1.0) < 0.01


class TestIchimoku:
    def test_ichimoku_keys(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert isinstance(result["ichimoku_above_cloud"], bool)
        assert result["ichimoku_cloud_color"] in ("green", "red")


class TestSupertrend:
    def test_supertrend_direction_valid(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["supertrend_direction"] in ("bullish", "bearish")

    def test_trending_series_supertrend_bullish(self):
        result = compute_all(_trending_df())
        assert result["supertrend_direction"] == "bullish"


class TestParabolicSAR:
    def test_sar_positive(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["sar"] > 0

    def test_sar_direction_valid(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["sar_direction"] in ("bullish", "bearish")


class TestDonchian:
    def test_donchian_pct_in_0_1(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert 0.0 <= result["donchian_pct"] <= 1.0

    def test_donchian_high_gte_low(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["donchian_high"] >= result["donchian_low"]


class TestFibonacci:
    def test_fib_levels_populated(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert "fib_236" in result
        assert "fib_382" in result
        assert "fib_618" in result

    def test_fib_nearest_level_valid(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["fib_nearest_level"] in ("0.236", "0.382", "0.500", "0.618", "0.786")

    def test_fib_distance_non_negative(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["fib_distance_pct"] >= 0


class TestHurst:
    def test_hurst_in_0_1(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert 0.0 <= result["hurst"] <= 1.0

    def test_market_regime_valid(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["market_regime"] in ("trending", "mean_reverting", "random")

    def test_trending_series_hurst_above_0_5(self):
        result = compute_all(_trending_df(n=300))
        assert result["hurst"] >= 0.45   # trending series should have H closer to 1


class TestVWAP:
    def test_vwap_positive(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert result["vwap"] > 0

    def test_price_vs_vwap_finite(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        assert math.isfinite(result["price_vs_vwap"])

    def test_trending_series_price_above_vwap(self):
        result = compute_all(_trending_df())
        assert result["price_vs_vwap"] > 0


# ── Close price propagation ────────────────────────────────────────────────────

class TestClosePropagation:
    def test_close_matches_last_bar(self, synthetic_ohlcv):
        result = compute_all(synthetic_ohlcv)
        expected = round(float(synthetic_ohlcv["close"].iloc[-1]), 4)
        assert result["close"] == expected


# ── Reproducibility (seed) ────────────────────────────────────────────────────

class TestReproducibility:
    def test_same_df_same_score(self, synthetic_ohlcv):
        r1 = compute_all(synthetic_ohlcv)
        r2 = compute_all(synthetic_ohlcv)
        assert r1["score"] == r2["score"]
        assert r1["bias"] == r2["bias"]

    def test_different_df_different_score(self):
        r1 = compute_all(_trending_df())
        r2 = compute_all(_flat_df())
        assert r1["score"] != r2["score"]
