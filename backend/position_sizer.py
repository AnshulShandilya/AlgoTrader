"""
Phase 3: Pluggable position-sizing strategies.

Three implementations:
  FixedFractionSizer  — deploy a constant % of capital (current default behaviour)
  VolTargetSizer      — size so that expected daily dollar P&L vol is constant
  FractionalKellySizer — half-Kelly criterion from historical trade P&Ls

All sizers implement the same interface:
    .size(capital, price, trailing_vol_pct, pnl_history) -> dollar_amount

CLAUDE.md: sizer is pluggable (not hard-wired into the backtest loop).
"""
from dataclasses import dataclass
from typing import List
import numpy as np


# ── Fixed Fraction ────────────────────────────────────────────────────────────

@dataclass
class FixedFractionSizer:
    """Deploy `fraction` of current capital per trade."""
    fraction: float = 0.10

    def size(
        self,
        capital: float,
        price: float,
        trailing_vol_pct: float,
        pnl_history: List[float],
    ) -> float:
        return capital * self.fraction

    def as_dict(self) -> dict:
        return {"type": "fixed", "fraction": self.fraction}


# ── Volatility Targeting ──────────────────────────────────────────────────────

@dataclass
class VolTargetSizer:
    """
    Size positions so that the expected daily dollar P&L volatility is
    a constant fraction of capital (vol-targeting).

    dollar_size = capital × (daily_vol_target / asset_daily_vol)

    daily_vol_target = annual_vol_target / sqrt(ann_N)

    For a 15% annual vol target and 2% daily asset vol:
        fraction ≈ (0.15 / sqrt(252)) / 0.02 ≈ 47% → capped at max_fraction

    Capped at max_fraction × capital for risk management.
    Floored at min_vol_pct to avoid oversizing on zero-vol days (e.g. weekends).
    """
    annual_vol_target: float = 0.15    # 15% annualised portfolio vol target
    ann_N: int = 252
    max_fraction: float = 0.20         # hard cap: never deploy > 20% per trade
    min_vol_pct: float = 0.5           # assume at least 0.5% daily vol

    def size(
        self,
        capital: float,
        price: float,
        trailing_vol_pct: float,
        pnl_history: List[float],
    ) -> float:
        asset_vol = max(trailing_vol_pct / 100.0, self.min_vol_pct / 100.0)
        daily_target = self.annual_vol_target / np.sqrt(self.ann_N)
        fraction = min(daily_target / asset_vol, self.max_fraction)
        return capital * fraction

    def as_dict(self) -> dict:
        return {
            "type": "vol_target",
            "annual_vol_target": self.annual_vol_target,
            "max_fraction": self.max_fraction,
        }


# ── Fractional Kelly ──────────────────────────────────────────────────────────

@dataclass
class FractionalKellySizer:
    """
    Half-Kelly (or fractional-Kelly) criterion from historical trade P&Ls.

    Kelly fraction  = μ / σ²   (% of capital to wager per trade)
    Fractional Kelly = kelly_fraction × (μ / σ²)   where kelly_fraction ≤ 1

    Falls back to fixed_fallback_fraction until min_trades of history exist.
    Capped at max_fraction and floored at 0.01 (1%).

    Why fractional? Full Kelly is theoretically optimal under log-utility but
    produces extreme swings in practice. Half-Kelly (0.5) halves the drawdown
    at the cost of ~25% less long-run growth.
    """
    kelly_fraction: float = 0.5              # 1.0 = full Kelly, 0.5 = half-Kelly
    min_trades: int = 20                     # minimum trades before switching from fallback
    max_fraction: float = 0.20              # hard cap
    fixed_fallback_fraction: float = 0.10   # used before min_trades reached

    def size(
        self,
        capital: float,
        price: float,
        trailing_vol_pct: float,
        pnl_history: List[float],
    ) -> float:
        if len(pnl_history) < self.min_trades:
            return capital * self.fixed_fallback_fraction

        pnls = np.array(pnl_history, dtype=float)
        mu  = float(pnls.mean())
        var = float(pnls.var())

        if var <= 0 or mu <= 0:
            return capital * self.fixed_fallback_fraction

        # Convert to % returns relative to approximate notional
        notional = capital * self.fixed_fallback_fraction
        mu_ret  = mu  / notional
        var_ret = var / (notional ** 2)

        kelly    = mu_ret / var_ret
        fraction = float(np.clip(kelly * self.kelly_fraction, 0.01, self.max_fraction))
        return capital * fraction

    def as_dict(self) -> dict:
        return {
            "type": "kelly",
            "kelly_fraction": self.kelly_fraction,
            "max_fraction": self.max_fraction,
            "min_trades": self.min_trades,
        }


# ── Factory ───────────────────────────────────────────────────────────────────

def make_sizer(
    sizer_type: str = "fixed",
    fraction: float = 0.10,
    annual_vol_target: float = 0.15,
    max_fraction: float = 0.20,
    kelly_fraction: float = 0.5,
    min_trades: int = 20,
    ann_N: int = 252,
):
    """Create a PositionSizer from keyword arguments."""
    if sizer_type == "vol_target":
        return VolTargetSizer(
            annual_vol_target=annual_vol_target,
            ann_N=ann_N,
            max_fraction=max_fraction,
        )
    if sizer_type == "kelly":
        return FractionalKellySizer(
            kelly_fraction=kelly_fraction,
            max_fraction=max_fraction,
            min_trades=min_trades,
            fixed_fallback_fraction=fraction,
        )
    return FixedFractionSizer(fraction=fraction)
