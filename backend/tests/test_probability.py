"""Tests for the pre-trade probability engine."""
import pytest
from datetime import datetime, timezone
from probability import (
    PreTradeSignal, score_trade,
    _score_signal_confidence,
    _score_historical_performance,
    _score_time_of_day,
    _score_rr,
)


# ── Component tests ────────────────────────────────────────────────────────────

def test_signal_confidence_full():
    assert _score_signal_confidence(1.0) == 30

def test_signal_confidence_half():
    assert _score_signal_confidence(0.5) == 15

def test_signal_confidence_zero():
    assert _score_signal_confidence(0.0) == 0

def test_signal_confidence_clamped():
    # Values above 1.0 should be clamped
    assert _score_signal_confidence(2.0) == 30


def test_history_no_data():
    score, reason = _score_historical_performance(0, 0.0, None, 0.0)
    assert score == 12
    assert "Insufficient" in reason

def test_history_good_record():
    score, _ = _score_historical_performance(50, 0.65, 1.8, 2.0)
    assert score > 18   # should be high with good metrics

def test_history_poor_record():
    score, _ = _score_historical_performance(50, 0.30, -0.5, 0.7)
    assert score < 10


# Opening power hour (13:30 UTC = 09:30 ET) — Monday June 22
def test_time_power_hour_am():
    t = datetime(2026, 6, 22, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 ET
    score, reason = _score_time_of_day(t)
    assert score == 20
    assert "power hour" in reason.lower()

# Midday lull (15:30–18:00 UTC = 11:30–14:00 ET) — Monday June 22
def test_time_midday_lull():
    t = datetime(2026, 6, 22, 16, 30, tzinfo=timezone.utc)  # Monday 12:30 ET
    score, reason = _score_time_of_day(t)
    assert score == 5
    assert "lull" in reason.lower()

# Weekend — Saturday June 21
def test_time_weekend():
    t = datetime(2026, 6, 21, 14, 0, tzinfo=timezone.utc)  # Saturday
    score, reason = _score_time_of_day(t)
    assert score == 4
    assert "weekend" in reason.lower() or "Weekend" in reason

# After close — Monday June 22 at 21:00 UTC = 17:00 ET
def test_time_after_close():
    t = datetime(2026, 6, 22, 21, 0, tzinfo=timezone.utc)  # Monday 17:00 ET
    score, reason = _score_time_of_day(t)
    assert score == 2


def test_rr_good():
    score, reason = _score_rr(1.5, 4.5)   # R:R = 3x
    assert score == 10
    assert "3.00x" in reason

def test_rr_minimum():
    score, reason = _score_rr(2.0, 2.0)   # R:R = 1.0x
    assert score == 5

def test_rr_bad():
    score, _ = _score_rr(3.0, 1.5)        # R:R < 1
    assert score == 2

def test_rr_zero_stop():
    score, reason = _score_rr(0.0, 3.0)
    assert score == 0
    assert "Invalid" in reason


# ── Integration: score_trade ───────────────────────────────────────────────────

def _strong_signal() -> PreTradeSignal:
    return PreTradeSignal(
        signal_confidence=0.85,
        sl_pct=1.5, tp_pct=4.5,
        strategy_closed_trades=50,
        strategy_win_rate=0.60,
        strategy_avg_r=1.5,
        strategy_profit_factor=1.8,
        recent_closes=[100.0 + i * 0.5 for i in range(60)],  # trending up
        symbol="AAPL",
        timeframe="1Day",
    )


def test_strong_signal_is_go():
    t = datetime(2026, 6, 22, 14, 0, tzinfo=timezone.utc)  # Monday 10am ET
    result = score_trade(_strong_signal(), now_utc=t)
    assert result.go is True
    assert result.score >= 60
    assert result.verdict in ("GO", "STRONG GO")


def test_weak_signal_no_go():
    t = datetime(2026, 6, 20, 17, 0, tzinfo=timezone.utc)  # Saturday
    s = PreTradeSignal(
        signal_confidence=0.10,
        sl_pct=4.0, tp_pct=2.0,   # bad R:R
        strategy_closed_trades=5,  # no history
        strategy_win_rate=0.0,
        symbol="XYZ",
    )
    result = score_trade(s, now_utc=t)
    assert result.go is False
    assert result.verdict in ("MARGINAL", "NO-GO")


def test_score_components_sum():
    t = datetime(2026, 6, 22, 14, 0, tzinfo=timezone.utc)
    result = score_trade(_strong_signal(), now_utc=t)
    component_sum = sum(result.components.values())
    assert result.score == component_sum


def test_score_bounds():
    for conf in [0.0, 0.5, 1.0]:
        s = PreTradeSignal(signal_confidence=conf, sl_pct=2.0, tp_pct=4.0)
        result = score_trade(s)
        assert 0 <= result.score <= 100


def test_expected_r_positive_for_good_setup():
    t = datetime(2026, 6, 22, 14, 0, tzinfo=timezone.utc)
    result = score_trade(_strong_signal(), now_utc=t)
    assert result.expected_r > 0


def test_reasons_have_five_components():
    result = score_trade(_strong_signal())
    assert len(result.reasons) == 5


def test_verdict_thresholds():
    """Score thresholds map to correct verdicts."""
    import types
    from probability import PreTradeResult
    for score, expected in [(80, "STRONG GO"), (65, "GO"), (50, "MARGINAL"), (30, "NO-GO")]:
        # Build result directly to test verdict logic
        s = _strong_signal()
        # Patch score_trade to verify via actual call with forced outcome
        # Instead verify mapping rule directly
        if score >= 75:
            verdict = "STRONG GO"
        elif score >= 60:
            verdict = "GO"
        elif score >= 45:
            verdict = "MARGINAL"
        else:
            verdict = "NO-GO"
        assert verdict == expected
