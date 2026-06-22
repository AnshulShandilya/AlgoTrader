"""
Bar-by-bar backtesting engine for single-asset strategies.

Execution model (no lookahead bias):
  - Signal generated from bar i CLOSE data
  - Entry fills at bar i+1 OPEN with spread + vol-slippage (CostModel)
  - Stop-loss / take-profit checked intrabar using high/low with gap protection
  - Signal-based exits fill at the bar's CLOSE with spread + vol-slippage
  - Commission charged on both entry and exit legs

Cost tracking:
  - Every trade records gross_pnl (zero-cost) and cost_paid (costs eaten)
  - cumulative_costs accumulates across closed trades
  - gross_equity_curve[i] = net_equity[i] + cumulative_costs[i]  (approximate)

Per CLAUDE.md: costs are ON by default; pass CostModel.zero() to suppress and
label the result "GROSS (no costs)".
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional, Any
import logging

import metrics as _m
from seed import set_seed
from cost_model import CostModel
from position_sizer import FixedFractionSizer, make_sizer
from regime import RegimeGate, make_regime_gate

log = logging.getLogger("backtester")


@dataclass
class BtTrade:
    entry_bar: int
    symbol: str
    side: str            # "long" | "short"
    entry_price: float   # actual fill price (with spread + slippage)
    entry_mid: float     # raw reference price (for gross P&L calculation)
    qty: float
    entry_date: Any
    exit_bar: int = -1
    exit_price: float = 0.0   # actual fill price
    exit_mid: float = 0.0     # raw reference price
    exit_date: Any = None
    pnl: float = 0.0          # net P&L (after all costs)
    gross_pnl: float = 0.0    # P&L with zero costs (mid-to-mid)
    cost_paid: float = 0.0    # = gross_pnl - pnl
    pnl_pct: float = 0.0      # net % return based on notional deployed
    reason: str = ""


def _trailing_vol(closes: np.ndarray, i: int, window: int = 20) -> float:
    """
    Trailing daily vol as a percentage (e.g. 2.0 = 2%).
    Uses up to `window` closes ending at bar i−1 (no lookahead).
    """
    start = max(0, i - window)
    seg   = closes[start:i]
    if len(seg) < 2:
        return 2.0          # fallback: 2% daily vol
    rets = np.diff(seg) / seg[:-1]
    return float(rets.std() * 100)


class BacktestEngine:
    def __init__(
        self,
        initial_capital: float = 100_000.0,
        commission_pct: float = 0.1,       # kept for backward compat; overridden by cost_model
        slippage_pct: float = 0.05,         # kept for backward compat; overridden by cost_model
        position_size_pct: float = 10.0,
        cost_model: Optional[CostModel] = None,
        position_sizer=None,               # FixedFractionSizer | VolTargetSizer | FractionalKellySizer
        regime_gate: Optional[RegimeGate] = None,
    ):
        self.initial_capital = initial_capital
        self.pos_size        = position_size_pct / 100

        if cost_model is not None:
            self.cm = cost_model
        else:
            self.cm = CostModel(
                commission_bps  = commission_pct * 100,
                half_spread_bps = slippage_pct * 100,
                slippage_k      = 0.0,
            )

        self.sizer = position_sizer if position_sizer is not None \
            else FixedFractionSizer(fraction=self.pos_size)
        self.regime_gate = regime_gate   # None → no gating

    def run(self, strategy, df: pd.DataFrame, symbol: str) -> dict:
        set_seed()

        df = df.reset_index(drop=True)
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

        if len(df) < 55:
            raise ValueError(f"Need 55+ bars after cleaning, got {len(df)}")

        sl_pct = strategy.risk_config.get("stop_loss_pct", 1.0) / 100
        tp_pct = strategy.risk_config.get("take_profit_pct", 2.0) / 100
        ann_N  = self._detect_N(df)

        closes        = df["close"].values.astype(float)
        capital       = self.initial_capital
        open_trade: Optional[BtTrade] = None
        pending_signal = None
        trades: List[BtTrade] = []
        equity_curve  = []
        cumul_costs   = 0.0          # running total costs from closed trades

        bah_start = float(closes[50])

        for i in range(50, len(df)):
            row  = df.iloc[i]
            date = row["datetime"] if "datetime" in row.index else i
            o    = float(row["open"])
            h    = float(row["high"])
            l    = float(row["low"])
            c    = float(row["close"])
            vol  = _trailing_vol(closes, i)   # vol at bar i uses closes 0..i-1

            # ── 1. Execute pending entry at today's OPEN ──────────────────────
            if pending_signal is not None and open_trade is None:
                action   = pending_signal.action
                fill_mul = self.cm.fill_multiplier("buy" if action == "buy" else "sell", vol)
                fill     = o * fill_mul
                dollar   = self.sizer.size(capital, fill, vol, [t.pnl for t in trades])
                qty      = dollar / fill
                comm_in  = self.cm.commission_amount(fill, qty)
                cost     = dollar + comm_in
                if cost <= capital * 0.99:
                    capital -= cost
                    open_trade = BtTrade(
                        entry_bar=i, symbol=symbol,
                        side="long" if action == "buy" else "short",
                        entry_price=fill, entry_mid=o,
                        qty=qty, entry_date=str(date)[:19],
                    )
                pending_signal = None

            # ── 2. Stop-loss / take-profit with gap protection ────────────────
            if open_trade:
                ep        = open_trade.entry_price
                mult      = 1 if open_trade.side == "long" else -1
                exit_ref, exit_why = None, None   # reference (mid) price for stop/TP

                if open_trade.side == "long":
                    sl_lvl = open_trade.entry_mid * (1 - sl_pct)
                    tp_lvl = open_trade.entry_mid * (1 + tp_pct)
                    if o <= sl_lvl:
                        exit_ref, exit_why = o, "stop_loss"
                    elif l <= sl_lvl:
                        exit_ref, exit_why = sl_lvl, "stop_loss"
                    elif h >= tp_lvl:
                        exit_ref, exit_why = tp_lvl, "take_profit"
                else:
                    sl_lvl = open_trade.entry_mid * (1 + sl_pct)
                    tp_lvl = open_trade.entry_mid * (1 - tp_pct)
                    if o >= sl_lvl:
                        exit_ref, exit_why = o, "stop_loss"
                    elif h >= sl_lvl:
                        exit_ref, exit_why = sl_lvl, "stop_loss"
                    elif l <= tp_lvl:
                        exit_ref, exit_why = tp_lvl, "take_profit"

                if exit_ref is not None:
                    exit_fill = exit_ref * self.cm.fill_multiplier(
                        "sell" if open_trade.side == "long" else "buy", vol
                    )
                    comm_out   = self.cm.commission_amount(exit_fill, open_trade.qty)
                    net_pnl    = (exit_fill - ep) * open_trade.qty * mult - comm_out
                    gross_pnl  = (exit_ref - open_trade.entry_mid) * open_trade.qty * mult
                    cost_paid  = gross_pnl - net_pnl
                    capital   += open_trade.qty * ep + net_pnl
                    cumul_costs += cost_paid
                    open_trade = self._finalise(
                        open_trade, exit_fill, exit_ref, exit_why, i, date, mult,
                        net_pnl, gross_pnl, cost_paid
                    )
                    trades.append(open_trade)
                    open_trade = None

            # ── 3. Generate signal from today's CLOSE ─────────────────────────
            try:
                signal = strategy.generate_signal(df.iloc[: i + 1])
            except Exception:
                signal = None

            # ── 4. Signal-based exit at today's CLOSE ────────────────────────
            if open_trade and signal and signal.action != "hold":
                if (open_trade.side == "long"  and signal.action == "sell") or \
                   (open_trade.side == "short" and signal.action == "buy"):
                    ep        = open_trade.entry_price
                    mult      = 1 if open_trade.side == "long" else -1
                    exit_side = "sell" if open_trade.side == "long" else "buy"
                    exit_fill = c * self.cm.fill_multiplier(exit_side, vol)
                    comm_out  = self.cm.commission_amount(exit_fill, open_trade.qty)
                    net_pnl   = (exit_fill - ep) * open_trade.qty * mult - comm_out
                    gross_pnl = (c - open_trade.entry_mid) * open_trade.qty * mult
                    cost_paid = gross_pnl - net_pnl
                    capital  += open_trade.qty * ep + net_pnl
                    cumul_costs += cost_paid
                    open_trade = self._finalise(
                        open_trade, exit_fill, c, "signal", i, date, mult,
                        net_pnl, gross_pnl, cost_paid
                    )
                    trades.append(open_trade)
                    open_trade = None

            # ── 5. Queue entry for next bar (subject to regime gate) ──────────
            if open_trade is None and signal and signal.action in ("buy", "sell"):
                regime_ok = (
                    self.regime_gate is None
                    or self.regime_gate.allow_entry(closes, i)
                )
                pending_signal = signal if regime_ok else None

            # ── 6. Mark-to-market — both net and gross equity ─────────────────
            mtm_net = 0.0
            if open_trade:
                m       = 1 if open_trade.side == "long" else -1
                # Net MTM uses actual fill prices
                mtm_net = open_trade.qty * open_trade.entry_price + \
                          (c - open_trade.entry_price) * open_trade.qty * m

            net_equity   = capital + mtm_net
            gross_equity = net_equity + cumul_costs   # approximate gross (add back costs)
            bah_eq       = self.initial_capital * c / bah_start
            equity_curve.append({
                "date":  str(date)[:19],
                "value": round(net_equity, 2),
                "gross": round(gross_equity, 2),
                "bah":   round(bah_eq, 2),
            })

        # ── Close any open trade at end of data ───────────────────────────────
        if open_trade:
            last     = df.iloc[-1]
            lp       = float(last["close"])
            ld       = str(last.get("datetime", len(df)))[:19]
            ep       = open_trade.entry_price
            mult     = 1 if open_trade.side == "long" else -1
            exit_side = "sell" if open_trade.side == "long" else "buy"
            exit_fill = lp * self.cm.fill_multiplier(exit_side, _trailing_vol(closes, len(df)))
            comm_out  = self.cm.commission_amount(exit_fill, open_trade.qty)
            net_pnl   = (exit_fill - ep) * open_trade.qty * mult - comm_out
            gross_pnl = (lp - open_trade.entry_mid) * open_trade.qty * mult
            cost_paid = gross_pnl - net_pnl
            capital  += open_trade.qty * ep + net_pnl
            open_trade = self._finalise(
                open_trade, exit_fill, lp, "end_of_data", len(df) - 1, ld, mult,
                net_pnl, gross_pnl, cost_paid
            )
            trades.append(open_trade)

        return self._build_report(symbol, strategy.__class__.__name__, df, trades, equity_curve, capital, ann_N, bah_start)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _finalise(
        trade: BtTrade, exit_price: float, exit_mid: float, reason: str,
        bar: int, date, mult: int,
        net_pnl: float, gross_pnl: float, cost_paid: float
    ) -> BtTrade:
        trade.exit_bar   = bar
        trade.exit_price = round(exit_price, 6)
        trade.exit_mid   = round(exit_mid, 6)
        trade.exit_date  = str(date)[:19]
        trade.reason     = reason
        trade.pnl        = round(net_pnl, 2)
        trade.gross_pnl  = round(gross_pnl, 2)
        trade.cost_paid  = round(cost_paid, 2)
        trade.pnl_pct    = round(
            (exit_price - trade.entry_price) / trade.entry_price * 100 * mult, 2
        )
        return trade

    def _detect_N(self, df: pd.DataFrame) -> int:
        try:
            dates = pd.to_datetime(df["datetime"])
            span  = max((dates.iloc[-1] - dates.iloc[0]).days, 1)
            return 365 if len(df) * 365 / span > 300 else 252
        except Exception:
            return 252

    # ── Metrics / report ─────────────────────────────────────────────────────

    def _build_report(self, symbol, strat_name, df, trades, equity_curve, final_capital, ann_N, bah_start) -> dict:
        for t in trades:
            if t.pnl == 0.0:    # end_of_data trades may not have been finalised
                mult  = 1 if t.side == "long" else -1
                t.pnl = round((t.exit_price - t.entry_price) * t.qty * mult, 2)

        winners  = [t for t in trades if t.pnl > 0]
        losers   = [t for t in trades if t.pnl <= 0]
        pnls     = [t.pnl for t in trades]
        win_rate = len(winners) / len(trades) * 100 if trades else 0.0

        values    = np.array([e["value"] for e in equity_curve])
        total_ret = (final_capital - self.initial_capital) / self.initial_capital * 100

        m = _m.compute_all(pnls, values, total_ret, ann_N)

        # DSR + MC permutation (CLAUDE.md: DSR must accompany any Sharpe figure)
        skew, ekurt = _m.moments_of_returns(values)
        dsr = _m.deflated_sharpe_ratio(
            sr_annualized=m["sharpe_ratio"],
            n_obs=max(len(values) - 1, 1),
            n_trials=1,           # single-run backtest: 1 trial
            skewness=skew,
            excess_kurtosis=ekurt,
            ann_N=ann_N,
        )
        mc = _m.mc_permutation_test(values, n_permutations=500, ann_N=ann_N)

        total_costs = sum(t.cost_paid for t in trades)
        gross_final = final_capital + total_costs
        gross_ret   = (gross_final - self.initial_capital) / self.initial_capital * 100

        bah_end    = float(df.iloc[-1]["close"])
        bah_return = (bah_end - bah_start) / bah_start * 100
        alpha      = total_ret - bah_return
        low_sample = len(trades) < 30

        step = max(1, len(equity_curve) // 500)

        return {
            "strategy_type":        "single",
            "symbol":               symbol,
            "strategy_name":        strat_name,
            "start_date":           str(df.iloc[50].get("datetime", ""))[:10],
            "end_date":             str(df.iloc[-1].get("datetime", ""))[:10],
            "initial_capital":      self.initial_capital,
            "final_capital":        round(final_capital, 2),
            "total_return_pct":     round(total_ret, 2),
            "gross_return_pct":     round(gross_ret, 2),
            "total_costs_paid":     round(total_costs, 2),
            "benchmark_return_pct": round(bah_return, 2),
            "alpha_pct":            round(alpha, 2),
            "annualization_n":      ann_N,
            "cost_model":           self.cm.as_dict(),
            "position_sizer":       self.sizer.as_dict(),
            "regime_gate":          self.regime_gate.as_dict() if self.regime_gate else None,
            "total_trades":         len(trades),
            "winning_trades":       len(winners),
            "losing_trades":        len(losers),
            # Primary
            "expectancy":           m["expectancy"],
            "profit_factor":        m["profit_factor"],
            "payoff_ratio":         m["payoff_ratio"],
            # Secondary
            "max_drawdown_pct":     round(m["max_drawdown_pct"], 2),
            "sharpe_ratio":         round(m["sharpe_ratio"], 2),
            "sortino_ratio":        round(m["sortino_ratio"], 2),
            "calmar_ratio":         round(m["calmar_ratio"], 2),
            # Informational
            "win_rate":             round(win_rate, 1),
            "avg_win_pct":          round(sum(t.pnl_pct for t in winners) / len(winners) if winners else 0, 2),
            "avg_loss_pct":         round(sum(t.pnl_pct for t in losers)  / len(losers)  if losers  else 0, 2),
            "low_sample_warning":   low_sample,
            "equity_curve":         equity_curve[::step],
            "monthly_returns":      self._monthly(equity_curve),
            "trades": [
                {
                    "entry_date":  t.entry_date,
                    "exit_date":   t.exit_date,
                    "side":        t.side,
                    "entry_price": round(t.entry_price, 4),
                    "exit_price":  round(t.exit_price, 4),
                    "pnl":         t.pnl,
                    "gross_pnl":   t.gross_pnl,
                    "cost_paid":   t.cost_paid,
                    "pnl_pct":     t.pnl_pct,
                    "reason":      t.reason,
                }
                for t in trades
            ],
            # Statistical validity
            "deflated_sharpe_ratio": round(dsr, 4),
            "mc_permutation":        mc,
            "kpi_pass": {
                "expectancy":    m["expectancy"] > 0,
                "profit_factor": m["profit_factor"] >= 1.5,
                "max_drawdown":  m["max_drawdown_pct"] < 10,
                "sharpe":        m["sharpe_ratio"] >= 1.5,
                "dsr":           dsr >= 0.95,
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
