"""
stock_filter.py
===============
4-Layer stock selection funnel that turns the 18,000-ticker universe into
a tight candidate list for Grok to analyse.

Layer 1 — Liquidity gate        (hard pass/fail)
Layer 2 — Activity signal       (at least one trigger)
Layer 3 — Technical scoring     (0–100 points, min 40 to qualify)
Layer 4 — Slot allocation       (top N per asset class → Grok watchlist)

The output is a ranked list of ≤ 20 tickers ready to drop into Grok's
_build_user_prompt() as its watchlist for the session.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Thresholds ───────────────────────────────────────────────────────────────

# Layer 1 — Liquidity
L1_MIN_AVG_VOL_UK  = 50_000       # shares/day
L1_MIN_AVG_VOL_US  = 200_000
L1_MIN_AVG_VOL_CRYPTO = 1_000     # units/day (BTC, ETH, etc.)
L1_MIN_PRICE_GBP_P = 1.0          # pence
L1_MIN_PRICE_USD   = 0.05

# Layer 2 — Activity (at least one must be true)
L2_MIN_VOL_RATIO   = 1.5          # vol today vs 20d avg
L2_MIN_PCT_CHANGE  = 2.0          # % absolute move
L2_MIN_GAP_PCT     = 1.0          # open vs prior close gap %
# (20-day high/low is also a Layer 2 trigger — checked in score())

# Layer 3 — Minimum score to pass to Grok
L3_MIN_SCORE       = 40

# Layer 4 — Grok candidate slots
L4_SLOTS = {
    "uk_stock":  8,
    "us_stock":  7,
    "crypto":    3,
    "commodity": 1,
    "etf":       1,
}
L4_TOTAL = sum(L4_SLOTS.values())   # 20


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass
class StockCandidate:
    symbol:        str
    asset_class:   str
    price:         float
    price_pence:   Optional[float]   # UK stocks only
    pct_change:    float
    vol_ratio:     float
    rsi:           float
    rsi_rising:    bool
    above_ma50:    bool
    at_20d_extreme: bool
    gap_pct:       float
    direction:     str               # "up" | "down"
    is_penny:      bool
    score:         int = 0
    score_breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def display_price(self) -> str:
        if self.price_pence is not None:
            return f"{self.price_pence:.1f}p"
        return f"${self.price:.4f}" if self.price < 1 else f"${self.price:.2f}"


# ─── Scoring ──────────────────────────────────────────────────────────────────

def _score(c: StockCandidate) -> tuple[int, dict]:
    """Return (total_score, breakdown_dict) for one candidate."""
    pts: dict[str, int] = {}

    # Volume ratio
    vr = c.vol_ratio
    if vr >= 5:        pts["volume"] = 35
    elif vr >= 3:      pts["volume"] = 25
    elif vr >= 2:      pts["volume"] = 15
    elif vr >= 1.5:    pts["volume"] = 8
    else:              pts["volume"] = 0

    # Price change
    ac = abs(c.pct_change)
    if ac >= 8:        pts["move"] = 30
    elif ac >= 5:      pts["move"] = 25
    elif ac >= 3:      pts["move"] = 15
    elif ac >= 2:      pts["move"] = 8
    else:              pts["move"] = 0

    # RSI sweet spot for momentum (not overextended)
    if 45 <= c.rsi <= 65:   pts["rsi_zone"]  = 10
    elif 38 <= c.rsi <= 75: pts["rsi_zone"]  = 5
    else:                   pts["rsi_zone"]  = 0

    # RSI direction (rising = bullish momentum confirmation)
    pts["rsi_slope"] = 10 if c.rsi_rising else 0

    # Price above 50-day MA (trending above key level)
    pts["above_ma50"] = 10 if c.above_ma50 else 0

    # At 20-day high or low (breakout / breakdown)
    pts["extreme"] = 10 if c.at_20d_extreme else 0

    # Gap at open vs prior close
    ag = abs(c.gap_pct)
    if ag >= 3:    pts["gap"] = 15
    elif ag >= 1.5: pts["gap"] = 10
    elif ag >= 1:  pts["gap"] = 5
    else:          pts["gap"] = 0

    total = sum(pts.values())
    return total, pts


# ─── Layer checks ─────────────────────────────────────────────────────────────

def _passes_layer1(c: StockCandidate) -> bool:
    """Liquidity gate — hard minimums."""
    ac = c.asset_class
    price = c.price

    if ac == "uk_stock":
        price_p = (c.price_pence or price * 100)
        if price_p < L1_MIN_PRICE_GBP_P:
            return False
        # avg_vol not stored per-candidate; rely on universe screener having
        # already filtered vol == 0 entries
    elif ac in ("us_stock", "etf"):
        if price < L1_MIN_PRICE_USD:
            return False
    elif ac == "crypto":
        if price < 0.0001:
            return False
    # No price floor for commodities/forex

    return True


def _passes_layer2(c: StockCandidate) -> bool:
    """At least one activity signal must be present."""
    return (
        c.vol_ratio >= L2_MIN_VOL_RATIO
        or abs(c.pct_change) >= L2_MIN_PCT_CHANGE
        or abs(c.gap_pct) >= L2_MIN_GAP_PCT
        or c.at_20d_extreme
    )


# ─── Main filter ──────────────────────────────────────────────────────────────

def filter_candidates(raw_items: list[dict]) -> list[StockCandidate]:
    """
    Run all four layers on a list of raw universe items.
    Returns scored + sorted candidates that passed all gates.

    raw_items entries are expected to have at minimum the keys produced by
    universe.screen_universe():
        symbol, asset_class, price, price_pence, pct_change, vol_ratio,
        rsi, rsi_rising, above_ma50, at_20d_extreme, gap_pct, direction, is_penny
    """
    passed: list[StockCandidate] = []

    for item in raw_items:
        try:
            c = StockCandidate(
                symbol        = item["symbol"],
                asset_class   = item.get("asset_class", "us_stock"),
                price         = float(item.get("price", 0)),
                price_pence   = item.get("price_pence"),
                pct_change    = float(item.get("pct_change", 0)),
                vol_ratio     = float(item.get("vol_ratio", 1)),
                rsi           = float(item.get("rsi", 50)),
                rsi_rising    = bool(item.get("rsi_rising", False)),
                above_ma50    = bool(item.get("above_ma50", False)),
                at_20d_extreme= bool(item.get("at_20d_extreme", False)),
                gap_pct       = float(item.get("gap_pct", 0)),
                direction     = item.get("direction", "up"),
                is_penny      = bool(item.get("is_penny", False)),
            )
        except (KeyError, TypeError, ValueError):
            continue

        if not _passes_layer1(c):
            continue
        if not _passes_layer2(c):
            continue

        score, breakdown = _score(c)
        if score < L3_MIN_SCORE:
            continue

        c.score = score
        c.score_breakdown = breakdown
        passed.append(c)

    # Sort by score descending
    passed.sort(key=lambda x: x.score, reverse=True)
    return passed


def select_grok_candidates(
    candidates: list[StockCandidate],
    slots: dict[str, int] = L4_SLOTS,
) -> list[StockCandidate]:
    """
    Layer 4 — fill per-asset-class slots from highest-scoring candidates.
    Returns ≤ L4_TOTAL candidates in score order.
    """
    buckets: dict[str, list[StockCandidate]] = {k: [] for k in slots}
    overflow: list[StockCandidate] = []

    for c in candidates:
        ac = c.asset_class
        if ac in buckets and len(buckets[ac]) < slots[ac]:
            buckets[ac].append(c)
        else:
            overflow.append(c)

    # Fill any unfilled slots from overflow (by score)
    result: list[StockCandidate] = []
    for ac, items in buckets.items():
        result.extend(items)

    # Top up with overflow if any slots were empty
    remaining_slots = L4_TOTAL - len(result)
    if remaining_slots > 0:
        result.extend(overflow[:remaining_slots])

    result.sort(key=lambda x: x.score, reverse=True)
    return result[:L4_TOTAL]


def get_grok_watchlist(candidates: list[StockCandidate]) -> list[str]:
    """Return just the ticker symbols for Grok's watchlist."""
    return [c.symbol for c in candidates]


def summarise(candidates: list[StockCandidate]) -> str:
    """One-line human-readable summary for logging."""
    if not candidates:
        return "No candidates passed the filter."
    lines = [f"Top {len(candidates)} Grok candidates (score / move / vol):"]
    for c in candidates[:10]:
        lines.append(
            f"  {c.symbol:<14} score={c.score:>3}  "
            f"{c.pct_change:+.1f}%  vol={c.vol_ratio:.1f}x  "
            f"RSI={c.rsi:.0f}  {c.display_price}"
        )
    if len(candidates) > 10:
        lines.append(f"  ... +{len(candidates)-10} more")
    return "\n".join(lines)


# ─── Cache (populated by universe screener, consumed by grok_trader) ──────────

_grok_candidates: list[StockCandidate] = []


def update_grok_candidates(raw_items: list[dict]) -> list[StockCandidate]:
    """Called by the universe screener after each run."""
    global _grok_candidates
    all_passed  = filter_candidates(raw_items)
    _grok_candidates = select_grok_candidates(all_passed)
    logger.info(f"Stock filter: {len(raw_items)} in → {len(all_passed)} passed → "
                f"{len(_grok_candidates)} Grok candidates selected")
    logger.info(summarise(_grok_candidates))
    return _grok_candidates


def get_grok_candidates() -> list[StockCandidate]:
    """Return latest cached Grok candidates."""
    return _grok_candidates
