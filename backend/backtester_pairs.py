"""
Market-neutral pairs backtester.

Execution model (no lookahead bias):
  - Signal generated from bar i CLOSE prices
  - Entry fills both legs at bar i+1 OPEN with slippage
  - Exit fills both legs at bar i CLOSE on exit signal (market-on-close)
  - Total equity = cash + mark-to-market of both legs
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional, Any
import logging

import metrics as _m
from seed import set_seed
from cost_model import CostModel
from backtester import _trailing_vol
from strategies.pairs import (
    PairsStrategy, PairsSignal,
    KalmanHedgeFilter, estimate_halflife, check_cointegration,
)

log = logging.getLogger("backtester_pairs")


@dataclass
class Leg:
    symbol: str
    side: str          # "long" | "short"
    entry_price: float # actual fill price
    entry_mid: float   # raw reference price (for gross calc)
    qty: float
    entry_date: Any


@dataclass
class PairsTrade:
    leg1: Optional[Leg] = None
    leg2: Optional[Leg] = None
    entry_date: Any = None
    exit_date: Any = None
    entry_zscore: float = 0.0
    exit_zscore: float = 0.0
    pnl: float = 0.0
    gross_pnl: float = 0.0
    cost_paid: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""


class PairsBacktestEngine:
    def __init__(
        self,
        initial_capital: float = 100_000.0,
        commission_pct: float = 0.1,
        slippage_pct: float = 0.05,
        position_size_pct: float = 20.0,
        cost_model: Optional[CostModel] = None,
        use_kalman: bool = True,           # Phase 5: Kalman hedge ratio
        kalman_delta: float = 1e-4,        # process noise (drift rate of β)
        kalman_R: float = 1e-2,            # observation noise
    ):
        self.initial_capital = initial_capital
        self.pos_size = position_size_pct / 100

        if cost_model is not None:
            self.cm = cost_model
        else:
            self.cm = CostModel(
                commission_bps  = commission_pct * 100,
                half_spread_bps = slippage_pct * 100,
                slippage_k      = 0.0,
            )
        self.pos_size     = position_size_pct / 100
        self.use_kalman   = use_kalman
        self.kalman_delta = kalman_delta
        self.kalman_R     = kalman_R

    # ── Data alignment ────────────────────────────────────────────────────────

    def _align(self, df1: pd.DataFrame, df2: pd.DataFrame):
        d1 = df1.dropna(subset=["open", "high", "low", "close"]).copy()
        d2 = df2.dropna(subset=["open", "high", "low", "close"]).copy()

        col = "datetime" if "datetime" in d1.columns else d1.index.name or "index"
        d1["_date"] = pd.to_datetime(d1["datetime"] if "datetime" in d1.columns else d1.index).dt.date
        d2["_date"] = pd.to_datetime(d2["datetime"] if "datetime" in d2.columns else d2.index).dt.date

        common = set(d1["_date"]) & set(d2["_date"])
        d1 = d1[d1["_date"].isin(common)].reset_index(drop=True)
        d2 = d2[d2["_date"].isin(common)].reset_index(drop=True)
        return d1.drop(columns=["_date"]), d2.drop(columns=["_date"])

    def _detect_N(self, df: pd.DataFrame) -> int:
        try:
            dates = pd.to_datetime(df["datetime"])
            span_days = max((dates.iloc[-1] - dates.iloc[0]).days, 1)
            bars_per_year = len(df) * 365 / span_days
            return 365 if bars_per_year > 300 else 252
        except Exception:
            return 252

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self, strategy: PairsStrategy, df1: pd.DataFrame, df2: pd.DataFrame) -> dict:
        set_seed()   # reproducibility

        df1, df2 = self._align(df1, df2)
        n = min(len(df1), len(df2))

        if n < strategy.lookback + 10:
            raise ValueError(f"Need {strategy.lookback + 10}+ aligned bars, got {n}")

        ann_N = self._detect_N(df1)

        p1_close = df1["close"].values.astype(float)
        p2_close = df2["close"].values.astype(float)
        p1_open  = df1["open"].values.astype(float)
        p2_open  = df2["open"].values.astype(float)

        capital  = self.initial_capital
        position = "none"
        active: Optional[PairsTrade] = None
        pending_entry = None
        trades: List[PairsTrade] = []
        equity_curve  = []
        zscore_series = []
        peak = capital

        bah_start1 = float(p1_close[strategy.lookback])
        bah_start2 = float(p2_close[strategy.lookback])

        cumul_costs = 0.0

        # ── Phase 5: Kalman filter initialisation ─────────────────────────────
        kalman: Optional[KalmanHedgeFilter] = None
        if self.use_kalman:
            kalman = KalmanHedgeFilter(delta=self.kalman_delta, R=self.kalman_R)
            # Bootstrap with first `lookback` bars of data
            kalman.initialize(
                p1_close[: strategy.lookback],
                p2_close[: strategy.lookback],
            )
            # Warm-up Kalman on the IS window (no lookahead: uses only closes up to i-1)
            for _k in range(strategy.lookback):
                kalman.update(p2_close[_k], p1_close[_k])

        # Pre-compute cointegration check on the full available history
        coint_info = check_cointegration(p1_close, p2_close)
        halflife_bars = estimate_halflife(p1_close - p2_close)  # raw diff spread estimate

        for i in range(strategy.lookback, n):
            date  = str(df1.iloc[i].get("datetime", i))[:19]
            o1    = float(p1_open[i])
            o2    = float(p2_open[i])
            c1    = float(p1_close[i])
            c2    = float(p2_close[i])
            vol1  = _trailing_vol(p1_close, i)
            vol2  = _trailing_vol(p2_close, i)

            # ── 1. Execute pending entry at today's OPEN ──────────────────────
            if pending_entry is not None and active is None:
                sig  = pending_entry
                half = capital * self.pos_size / 2

                s1    = "buy" if sig.leg1_action == "buy" else "sell"
                s2    = "buy" if sig.leg2_action == "buy" else "sell"
                fill1 = o1 * self.cm.fill_multiplier(s1, vol1)
                fill2 = o2 * self.cm.fill_multiplier(s2, vol2)
                qty1  = half / fill1
                qty2  = half / fill2
                comm1 = self.cm.commission_amount(fill1, qty1)
                comm2 = self.cm.commission_amount(fill2, qty2)
                cost  = half * 2 + comm1 + comm2

                if cost <= capital * 0.99:
                    capital -= cost
                    active = PairsTrade(
                        leg1=Leg(strategy.symbol1,
                                 "long" if s1 == "buy" else "short",
                                 fill1, o1, qty1, date),
                        leg2=Leg(strategy.symbol2,
                                 "long" if s2 == "buy" else "short",
                                 fill2, o2, qty2, date),
                        entry_date=date,
                        entry_zscore=sig.zscore,
                    )
                    position = "long_spread" if s1 == "buy" else "short_spread"
                pending_entry = None

            # ── 2. Update Kalman and generate signal from today's CLOSE ──────
            if kalman is not None:
                kalman.update(p2_close[i], p1_close[i])  # update with bar i data
            kalman_beta = kalman.beta if kalman is not None else None

            sig = strategy.generate_signal(
                p1_close[: i + 1], p2_close[: i + 1], position,
                hedge_ratio_override=kalman_beta,
            )
            zscore_series.append({"date": date, "zscore": round(sig.zscore, 3)})

            # ── 3. Close active trade on exit signal at today's CLOSE ─────────
            if active and sig.action == "exit":
                pnl_info = self._close(active, c1, c2, vol1, vol2)
                capital += pnl_info["total_recovered"]
                cumul_costs += pnl_info["cost_paid"]
                active.exit_date   = date
                active.exit_zscore = sig.zscore
                active.pnl         = round(pnl_info["net_pnl"], 2)
                active.gross_pnl   = round(pnl_info["gross_pnl"], 2)
                active.cost_paid   = round(pnl_info["cost_paid"], 2)
                active.pnl_pct     = round(pnl_info["net_pnl"] / (self.pos_size * self.initial_capital) * 100, 2)
                active.reason      = "mean_reversion" if abs(sig.zscore) < strategy.exit_z else "stop_loss"
                trades.append(active)
                active   = None
                position = "none"

            # ── 4. Queue new entry for next bar ───────────────────────────────
            if active is None and sig.action in ("long_spread", "short_spread"):
                pending_entry = sig

            # ── 5. Mark-to-market ─────────────────────────────────────────────
            mtm = 0.0
            if active:
                for leg, px in [(active.leg1, c1), (active.leg2, c2)]:
                    if leg:
                        m   = 1 if leg.side == "long" else -1
                        mtm += leg.qty * leg.entry_price + (px - leg.entry_price) * leg.qty * m

            net_equity   = capital + mtm
            gross_equity = net_equity + cumul_costs
            # Average B&H: equal weight in both assets (buy-and-hold the pair)
            bah_eq = self.initial_capital * 0.5 * (c1 / bah_start1 + c2 / bah_start2)
            equity_curve.append({
                "date":  date,
                "value": round(net_equity, 2),
                "gross": round(gross_equity, 2),
                "bah":   round(bah_eq, 2),
            })
            if net_equity > peak:
                peak = net_equity

        # ── Close open trade at end of data ───────────────────────────────────
        if active:
            lp1       = float(p1_close[-1])
            lp2       = float(p2_close[-1])
            last_date = str(df1.iloc[-1].get("datetime", n))[:19]
            vol1_last = _trailing_vol(p1_close, n)
            vol2_last = _trailing_vol(p2_close, n)
            pnl_info  = self._close(active, lp1, lp2, vol1_last, vol2_last)
            capital  += pnl_info["total_recovered"]
            active.exit_date   = last_date
            active.exit_zscore = zscore_series[-1]["zscore"] if zscore_series else 0
            active.pnl         = round(pnl_info["net_pnl"], 2)
            active.gross_pnl   = round(pnl_info["gross_pnl"], 2)
            active.cost_paid   = round(pnl_info["cost_paid"], 2)
            active.pnl_pct     = round(pnl_info["net_pnl"] / (self.pos_size * self.initial_capital) * 100, 2)
            active.reason      = "end_of_data"
            trades.append(active)

        return self._build_report(
            strategy, df1, df2, trades, equity_curve, zscore_series,
            capital, ann_N, bah_start1, bah_start2,
            kalman_beta=kalman.beta if kalman else None,
            halflife_bars=halflife_bars,
            coint_info=coint_info,
            use_kalman=self.use_kalman,
        )

    # ── Trade close ───────────────────────────────────────────────────────────

    def _close(self, trade: PairsTrade, p1: float, p2: float, vol1: float = 2.0, vol2: float = 2.0) -> dict:
        net_pnl        = 0.0
        gross_pnl      = 0.0
        position_value = 0.0
        for leg, px, vol in [(trade.leg1, p1, vol1), (trade.leg2, p2, vol2)]:
            if leg:
                m         = 1 if leg.side == "long" else -1
                exit_side = "sell" if leg.side == "long" else "buy"
                exit_fill = px * self.cm.fill_multiplier(exit_side, vol)
                comm      = self.cm.commission_amount(exit_fill, leg.qty)
                net_pnl        += (exit_fill - leg.entry_price) * leg.qty * m - comm
                gross_pnl      += (px - leg.entry_mid) * leg.qty * m
                position_value += leg.qty * leg.entry_price
        cost_paid = gross_pnl - net_pnl
        return {
            "net_pnl":        net_pnl,
            "gross_pnl":      gross_pnl,
            "cost_paid":      cost_paid,
            "total_recovered": net_pnl + position_value,
        }

    # ── Report ────────────────────────────────────────────────────────────────

    def _build_report(self, strategy, df1, df2, trades, equity_curve,
                      zscore_series, final_capital, ann_N, bah_start1, bah_start2,
                      kalman_beta=None, halflife_bars=None, coint_info=None,
                      use_kalman=True) -> dict:
        winners  = [t for t in trades if t.pnl > 0]
        losers   = [t for t in trades if t.pnl <= 0]
        pnls     = [t.pnl for t in trades]
        win_rate = len(winners) / len(trades) * 100 if trades else 0.0

        values    = np.array([e["value"] for e in equity_curve])
        total_ret = (final_capital - self.initial_capital) / self.initial_capital * 100

        m = _m.compute_all(pnls, values, total_ret, ann_N)

        total_costs = sum(t.cost_paid for t in trades)
        gross_final = final_capital + total_costs
        gross_ret   = (gross_final - self.initial_capital) / self.initial_capital * 100

        bah_end1   = float(df1.iloc[-1]["close"])
        bah_end2   = float(df2.iloc[-1]["close"])
        bah_return = 0.5 * ((bah_end1 / bah_start1 - 1) + (bah_end2 / bah_start2 - 1)) * 100
        alpha      = total_ret - bah_return
        low_sample = len(trades) < 30

        step = max(1, len(equity_curve) // 500)

        return {
            "strategy_type":        "pairs",
            "symbol1":              strategy.symbol1,
            "symbol2":              strategy.symbol2,
            "start_date":           str(equity_curve[0]["date"])[:10]  if equity_curve else "N/A",
            "end_date":             str(equity_curve[-1]["date"])[:10] if equity_curve else "N/A",
            "initial_capital":      self.initial_capital,
            "final_capital":        round(final_capital, 2),
            "total_return_pct":     round(total_ret, 2),
            "gross_return_pct":     round(gross_ret, 2),
            "total_costs_paid":     round(total_costs, 2),
            "benchmark_return_pct": round(bah_return, 2),
            "alpha_pct":            round(alpha, 2),
            "annualization_n":      ann_N,
            "cost_model":           self.cm.as_dict(),
            # ── Phase 5 analytics ──────────────────────────────────────────────
            "hedge_ratio_method":   "kalman" if use_kalman else "ols",
            "kalman_beta_final":    round(float(kalman_beta), 4) if kalman_beta is not None else None,
            "halflife_bars":        round(halflife_bars, 1) if halflife_bars != float("inf") else None,
            "halflife_warning":     halflife_bars == float("inf") or (halflife_bars is not None and halflife_bars > 252),
            "cointegration_pvalue": coint_info["pvalue"] if coint_info else None,
            "cointegrated":         coint_info["is_cointegrated"] if coint_info else True,
            "total_trades":         len(trades),
            "winning_trades":       len(winners),
            "losing_trades":        len(losers),
            # ── Primary profitability ───────────────────────────────────────────
            "expectancy":           m["expectancy"],
            "profit_factor":        m["profit_factor"],
            "payoff_ratio":         m["payoff_ratio"],
            # ── Secondary / risk ───────────────────────────────────────────────
            "max_drawdown_pct":     round(m["max_drawdown_pct"], 2),
            "sharpe_ratio":         round(m["sharpe_ratio"], 2),
            "sortino_ratio":        round(m["sortino_ratio"], 2),
            "calmar_ratio":         round(m["calmar_ratio"], 2),
            # ── Informational ──────────────────────────────────────────────────
            "win_rate":             round(win_rate, 1),
            "low_sample_warning":   low_sample,
            "equity_curve":         equity_curve[::step],
            "zscore_series":        zscore_series[::step],
            "monthly_returns":      self._monthly(equity_curve),
            "trades": [
                {
                    "entry_date":   str(t.entry_date)[:19],
                    "exit_date":    str(t.exit_date)[:19] if t.exit_date else None,
                    "entry_zscore": round(t.entry_zscore, 3),
                    "exit_zscore":  round(t.exit_zscore, 3),
                    "pnl":          t.pnl,
                    "gross_pnl":    t.gross_pnl,
                    "cost_paid":    t.cost_paid,
                    "pnl_pct":      t.pnl_pct,
                    "reason":       t.reason,
                }
                for t in trades
            ],
            "kpi_pass": {
                # Primary
                "expectancy":    m["expectancy"] > 0,
                "profit_factor": m["profit_factor"] >= 1.5,
                # Secondary
                "max_drawdown":  m["max_drawdown_pct"] < 10,
                "sharpe":        m["sharpe_ratio"] >= 1.5,
            },
        }

    def _monthly(self, equity_curve: list) -> list:
        monthly: dict = {}
        for p in equity_curve:
            m = str(p["date"])[:7]
            monthly[m] = p["value"]
        keys   = sorted(monthly)
        result = []
        for i in range(1, len(keys)):
            prev, cur = monthly[keys[i - 1]], monthly[keys[i]]
            result.append({
                "month":      keys[i],
                "return_pct": round((cur - prev) / prev * 100, 2) if prev else 0,
            })
        return result
