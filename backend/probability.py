"""
Pre-trade probability engine.

Produces a 0–100 score for a potential trade BEFORE entry, combining:
  1. Signal confidence from the strategy (30%)
  2. Historical strategy performance from closed trades (25%)
  3. Time-of-day window quality (20%)
  4. Market regime from Hurst + volatility (15%)
  5. Risk:reward quality of the setup (10%)

Score interpretation:
  >= 75  → STRONG GO   (high confluence)
  60–74  → GO          (take the trade)
  45–59  → MARGINAL    (reduce size or skip)
  < 45   → NO-GO       (skip this trade)

All inputs must be from data available before the trade is placed —
no lookahead is possible by construction (signal comes from prior closed bars).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import numpy as np


@dataclass
class PreTradeSignal:
    """Input bundle for the probability engine."""
    signal_confidence: float           # 0.0–1.0 from strategy.generate_signal()
    sl_pct: float                      # stop-loss % from entry
    tp_pct: float                      # take-profit % from entry
    strategy_closed_trades: int = 0    # total closed trades for this strategy
    strategy_win_rate: float = 0.0     # historical win rate 0.0–1.0
    strategy_avg_r: Optional[float] = None   # historical avg R-multiple
    strategy_profit_factor: float = 0.0
    current_price: float = 0.0
    recent_closes: list[float] = field(default_factory=list)   # last N daily closes
    symbol: str = ""
    timeframe: str = "1Day"


@dataclass
class PreTradeResult:
    score: int                  # 0–100 composite probability score
    verdict: str                # "STRONG GO" | "GO" | "MARGINAL" | "NO-GO"
    expected_r: float           # estimated R if trade wins (tp/sl ratio adjusted for win prob)
    components: dict            # breakdown of each score component
    reasons: list[str]          # human-readable explanation bullets
    go: bool                    # True if score >= 60


# ── Component scorers ──────────────────────────────────────────────────────────

def _score_signal_confidence(conf: float) -> int:
    """Strategy signal confidence (0–1) → 0–30 pts."""
    return round(min(conf, 1.0) * 30)


def _score_historical_performance(
    closed_trades: int,
    win_rate: float,
    avg_r: Optional[float],
    profit_factor: float,
) -> tuple[int, str]:
    """
    Historical track record of this strategy → 0–25 pts.
    Returns (score, reason).
    No history → neutral 12 pts with a caveat.
    """
    if closed_trades < 10:
        return 12, f"Insufficient history ({closed_trades} trades) — neutral score"

    pts = 0
    # Win rate component (max 10 pts)
    pts += round(min(win_rate, 0.80) / 0.80 * 10)

    # Avg R component (max 8 pts) — positive R > 1 gets full marks
    if avg_r is not None:
        r_score = max(0, min(avg_r / 2.0, 1.0))  # 0→0pts, 2R→8pts
        pts += round(r_score * 8)

    # Profit factor (max 7 pts) — PF >= 1.5 gets full marks
    pf_score = max(0, min((profit_factor - 1.0) / 0.5, 1.0))
    pts += round(pf_score * 7)

    reason = (
        f"History: {closed_trades} trades, WR {win_rate*100:.0f}%, "
        f"AvgR {avg_r or 0:.2f}R, PF {profit_factor:.2f}"
    )
    return min(pts, 25), reason


def _score_time_of_day(now_utc: Optional[datetime] = None) -> tuple[int, str]:
    """
    Time-of-day window quality → 0–20 pts.
    Best windows (per Carter / Aziz): US open 13:30–15:30 UTC, EU close 15:30–16:30 UTC.
    Dead zone: 17:30–19:30 UTC (US midday lull).
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    hm = now_utc.hour * 60 + now_utc.minute  # minutes since midnight UTC
    weekday = now_utc.weekday()               # 0=Mon, 4=Fri, 5=Sat, 6=Sun

    if weekday >= 5:
        return 4, "Weekend — no US equity session"

    # US session windows in UTC minutes
    PRE_MARKET     = (12 * 60, 13 * 60 + 30)     # 08:00–09:30 ET
    POWER_HOUR_AM  = (13 * 60 + 30, 15 * 60 + 30) # 09:30–11:30 ET  ← best
    MIDDAY_LULL    = (15 * 60 + 30, 18 * 60)       # 11:30–14:00 ET  ← avoid
    POWER_HOUR_PM  = (18 * 60, 20 * 60)            # 14:00–16:00 ET  ← good
    AFTER_CLOSE    = (20 * 60, 24 * 60)

    if PRE_MARKET[0] <= hm < PRE_MARKET[1]:
        return 12, "Pre-market — reduced liquidity, gaps possible"
    elif POWER_HOUR_AM[0] <= hm < POWER_HOUR_AM[1]:
        return 20, "Opening power hour — highest opportunity window"
    elif MIDDAY_LULL[0] <= hm < MIDDAY_LULL[1]:
        return 5,  "Midday lull — thin edge, avoid new entries"
    elif POWER_HOUR_PM[0] <= hm < POWER_HOUR_PM[1]:
        return 16, "Afternoon session — good momentum continuation"
    elif AFTER_CLOSE[0] <= hm < AFTER_CLOSE[1]:
        return 2,  "After market close — do not enter new positions"
    else:
        # Crypto / 24h assets: treat off-hours as reduced
        return 8, "Off-hours — reduced participation"


def _score_regime(recent_closes: list[float], timeframe: str) -> tuple[int, str]:
    """
    Market regime from Hurst exponent → 0–15 pts.
    Only meaningful with >= 30 bars; returns neutral 7 pts with < 30.
    """
    if len(recent_closes) < 30:
        return 7, f"Insufficient bars ({len(recent_closes)}) for regime detection"

    try:
        from regime import hurst_exponent, vol_percentile
        prices = np.array(recent_closes[-60:], dtype=float)
        h = hurst_exponent(prices)
        vol_pct = vol_percentile(prices)

        # Trending regime (H > 0.55) → good for momentum strategies
        # Mean-reverting (H < 0.45) → good for mean-reversion
        # High vol (> 85th pct) → reduce score (risky entries)
        if h > 0.55:
            regime_score = 15
            regime_label = f"Trending (H={h:.2f}) — momentum setups favoured"
        elif h < 0.45:
            regime_score = 10
            regime_label = f"Mean-reverting (H={h:.2f}) — reversal setups favoured"
        else:
            regime_score = 7
            regime_label = f"Neutral regime (H={h:.2f})"

        if vol_pct > 85:
            regime_score = max(0, regime_score - 5)
            regime_label += f" | High vol ({vol_pct:.0f}th pct) — reduced score"

        return regime_score, regime_label
    except Exception:
        return 7, "Regime detection unavailable — neutral"


def _score_rr(sl_pct: float, tp_pct: float) -> tuple[int, str]:
    """
    Reward:risk ratio of this specific setup → 0–10 pts.
    R:R = tp_pct / sl_pct.  Full marks at R:R >= 2.0.
    """
    if sl_pct <= 0:
        return 0, "Invalid stop — zero or negative"
    rr = tp_pct / sl_pct
    score = round(min(rr / 2.0, 1.0) * 10)
    return score, f"R:R = {rr:.2f}x (TP {tp_pct}% / SL {sl_pct}%)"


# ── Main scorer ───────────────────────────────────────────────────────────────

def score_trade(s: PreTradeSignal, now_utc: Optional[datetime] = None) -> PreTradeResult:
    """
    Compute the pre-trade probability score for a potential trade.
    Call this BEFORE placing an order; pass the result to the Trade record.
    """
    c_signal = _score_signal_confidence(s.signal_confidence)
    c_history, r_history = _score_historical_performance(
        s.strategy_closed_trades, s.strategy_win_rate,
        s.strategy_avg_r, s.strategy_profit_factor,
    )
    c_time, r_time = _score_time_of_day(now_utc)
    c_regime, r_regime = _score_regime(s.recent_closes, s.timeframe)
    c_rr, r_rr = _score_rr(s.sl_pct, s.tp_pct)

    total = c_signal + c_history + c_time + c_regime + c_rr

    # Clamp 0–100
    total = max(0, min(total, 100))

    if total >= 75:
        verdict = "STRONG GO"
    elif total >= 60:
        verdict = "GO"
    elif total >= 45:
        verdict = "MARGINAL"
    else:
        verdict = "NO-GO"

    # Expected R = (win_prob × avg_win_R) - (loss_prob × 1.0)
    win_prob = s.strategy_win_rate if s.strategy_closed_trades >= 10 else 0.5
    avg_win_r = max(s.strategy_avg_r or 1.5, 0.1)
    rr_ratio  = (s.tp_pct / s.sl_pct) if s.sl_pct > 0 else 2.0
    expected_r = round(win_prob * min(avg_win_r, rr_ratio) - (1 - win_prob) * 1.0, 2)

    reasons = [
        f"Signal confidence: {s.signal_confidence*100:.0f}% → {c_signal}/30 pts",
        r_history + f" → {c_history}/25 pts",
        r_time + f" → {c_time}/20 pts",
        r_regime + f" → {c_regime}/15 pts",
        r_rr + f" → {c_rr}/10 pts",
    ]

    return PreTradeResult(
        score=total,
        verdict=verdict,
        expected_r=expected_r,
        go=(total >= 60),
        components={
            "signal":  c_signal,
            "history": c_history,
            "time":    c_time,
            "regime":  c_regime,
            "rr":      c_rr,
        },
        reasons=reasons,
    )
