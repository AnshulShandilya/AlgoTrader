"""
Transaction-cost model for backtesting.

Per CLAUDE.md: costs are ON by default. A zero-cost ("gross") run must be
explicitly requested and labelled.

Three cost components — all paid per side (entry AND exit):
  commission_bps   Fixed brokerage fee as basis points of notional.
  half_spread_bps  Half the bid-ask spread: you buy at the ask, sell at the bid.
  slippage_k       Market-impact coefficient: slippage_bps = slippage_k × trailing_vol_pct × 100
                   A quiet day (0.5% vol) → small slippage; a wild day (3% vol) → larger.

Default presets (CLAUDE.md):
  Crypto:   10 bps commission + 5 bps half-spread + k=0.1  (≈20–40 bps round-trip)
  Equities:  1 bps commission + 2 bps half-spread + k=0.05 (≈ 5–15 bps round-trip)
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CostModel:
    commission_bps: float = 10.0      # per side
    half_spread_bps: float = 5.0      # per side
    slippage_k: float = 0.1           # slippage_bps = k * vol_pct * 100

    # ── Presets ───────────────────────────────────────────────────────────────

    @classmethod
    def crypto(cls) -> "CostModel":
        """Binance Testnet / spot crypto. Tight book, but volatile."""
        return cls(commission_bps=10.0, half_spread_bps=5.0, slippage_k=0.1)

    @classmethod
    def equity(cls) -> "CostModel":
        """Alpaca Paper / US equities. Very tight spreads for liquid names."""
        return cls(commission_bps=1.0, half_spread_bps=2.0, slippage_k=0.05)

    @classmethod
    def zero(cls) -> "CostModel":
        """Zero-cost model — MUST be labelled 'GROSS (no costs)' in reports."""
        return cls(commission_bps=0.0, half_spread_bps=0.0, slippage_k=0.0)

    @classmethod
    def from_symbol(cls, symbol: str) -> "CostModel":
        """Auto-detect asset class from symbol string."""
        sym = symbol.upper()
        if "/" in sym or sym.endswith("-USD") or sym.endswith("USDT") or sym.endswith("USDC"):
            return cls.crypto()
        if sym.endswith(".L"):      # London Stock Exchange
            return cls.equity()
        return cls.equity()

    # ── Core calculations ─────────────────────────────────────────────────────

    def fill_multiplier(self, side: str, trailing_vol_pct: float) -> float:
        """
        Multiplicative adjustment to the reference (mid) price.

        For a buy  → fills HIGHER than mid (we pay the ask + slippage).
        For a sell → fills LOWER  than mid (we receive the bid - slippage).

        trailing_vol_pct: recent daily vol expressed as a percentage (e.g. 2.0 = 2%).
        """
        vol_slip_bps = self.slippage_k * trailing_vol_pct * 100
        total_bps    = self.half_spread_bps + vol_slip_bps
        adj          = total_bps / 10_000
        return (1.0 + adj) if side == "buy" else (1.0 - adj)

    def commission_amount(self, fill_price: float, qty: float) -> float:
        """Dollar commission for one side of a trade."""
        return fill_price * qty * self.commission_bps / 10_000

    def total_round_trip_bps(self, trailing_vol_pct: float) -> float:
        """Indicative round-trip cost in basis points for reporting."""
        vol_slip_bps = self.slippage_k * trailing_vol_pct * 100
        per_side     = self.commission_bps + self.half_spread_bps + vol_slip_bps
        return round(per_side * 2, 1)

    def as_dict(self) -> dict:
        return {
            "commission_bps":  self.commission_bps,
            "half_spread_bps": self.half_spread_bps,
            "slippage_k":      self.slippage_k,
        }
