"""
Walk-forward validation harness (Phase 2a).

Rolling in-sample (IS) optimisation window → out-of-sample (OOS) test window,
advancing by `oos_bars` each fold.

IS optimisation: grid-search over `param_grid` by IS Sharpe ratio.
OOS evaluation:  run best IS params on unseen data; aggregate across all folds.

Key guarantees:
  - IS and OOS periods never overlap.
  - OOS metrics are computed strictly on OOS data only.
  - The full param_grid is counted as N_trials for Deflated Sharpe Ratio.
  - Each fold uses a fresh engine with the same initial_capital (normalised),
    then the stitched equity curve scales each fold's returns from the
    cumulative ending value of the previous fold.
"""
from itertools import product
from typing import Any, Callable, Dict, List, Optional
import numpy as np
import pandas as pd
import logging

import metrics as _m
from cost_model import CostModel
from seed import set_seed

log = logging.getLogger("walk_forward")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _param_combos(param_grid: Dict[str, List[Any]]) -> List[dict]:
    keys = list(param_grid.keys())
    vals = [param_grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in product(*vals)]


def _stitch_oos_equity(
    chunks: List[list],
    initial_capital: float,
) -> list:
    """
    Chain per-fold OOS equity curves into one compounded curve.

    Each fold re-runs with initial_capital; we scale its returns so that
    fold k+1 starts from where fold k ended.
    """
    stitched: list = []
    carry = initial_capital
    for chunk in chunks:
        if not chunk:
            continue
        fold_start_val = chunk[0]["value"]
        if fold_start_val == 0:
            continue
        for bar in chunk:
            relative = bar["value"] / fold_start_val
            stitched.append({
                "date":  bar["date"],
                "value": round(carry * relative, 2),
            })
        carry = stitched[-1]["value"]
    return stitched


# ── Main entry point ──────────────────────────────────────────────────────────

def run_walk_forward(
    strategy_factory: Callable,
    param_grid: Dict[str, List[Any]],
    risk_config: dict,
    df: pd.DataFrame,
    symbol: str,
    engine_factory: Callable,
    is_bars: int = 252,
    oos_bars: int = 63,
    initial_capital: float = 100_000.0,
    position_size_pct: float = 10.0,
    cost_model: Optional[CostModel] = None,
    ann_N: int = 252,
    n_mc: int = 500,
) -> dict:
    """
    Rolling walk-forward optimisation + validation.

    Args:
        strategy_factory: callable(params, risk_config, symbol) → strategy
        param_grid:       {"param_name": [v1, v2, ...], ...}  for grid search
        risk_config:      stop_loss_pct / take_profit_pct / etc.
        df:               full OHLCV DataFrame (datetime, open, high, low, close, volume)
        symbol:           ticker symbol (used for cost-model detection)
        engine_factory:   callable(initial_capital, position_size_pct, cost_model) → BacktestEngine
        is_bars:          IS window length in bars  (default 252 = 1 trading year)
        oos_bars:         OOS window length in bars (default 63 = 1 calendar quarter)
        initial_capital:  starting capital for each fold normalisation
        position_size_pct: fraction of capital per trade
        cost_model:       CostModel to use (if None, auto-detected from symbol)
        ann_N:            annualisation factor (252 equity / 365 crypto)
        n_mc:             number of Monte Carlo permutations

    Returns a dict with:
        n_folds, n_trials, folds[], oos_equity_curve[],
        oos_* aggregate metrics, dsr, mc_pvalue, mc_verdict, kpi_pass{}
    """
    set_seed()

    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    n  = len(df)

    if n < is_bars + oos_bars:
        raise ValueError(
            f"Need {is_bars + oos_bars}+ clean bars for one fold, got {n}. "
            f"Use a longer period or smaller is_bars/oos_bars."
        )

    combos     = _param_combos(param_grid)
    n_combos   = max(len(combos), 1)
    folds:     list = []
    oos_chunks: list = []
    oos_pnls:   list = []
    n_trials_total = 0

    fold_start = 0
    while fold_start + is_bars + oos_bars <= n:
        is_df  = df.iloc[fold_start : fold_start + is_bars].reset_index(drop=True)
        oos_df = df.iloc[fold_start + is_bars : fold_start + is_bars + oos_bars].reset_index(drop=True)

        is_start_label  = str(df.iloc[fold_start].get("datetime", fold_start))[:10]
        is_end_label    = str(df.iloc[fold_start + is_bars - 1].get("datetime", fold_start + is_bars))[:10]
        oos_start_label = str(df.iloc[fold_start + is_bars].get("datetime", fold_start + is_bars))[:10]
        oos_end_label   = str(df.iloc[fold_start + is_bars + oos_bars - 1].get("datetime", fold_start + is_bars + oos_bars))[:10]

        # ── IS optimisation: pick params with best IS Sharpe ──────────────────
        best_sharpe   = -np.inf
        best_params   = combos[0] if combos else {}
        best_is_result: Optional[dict] = None

        for params in combos:
            try:
                strat  = strategy_factory(params, risk_config, symbol)
                engine = engine_factory(initial_capital, position_size_pct, cost_model)
                r      = engine.run(strat, is_df.copy(), symbol)
                if r["sharpe_ratio"] > best_sharpe:
                    best_sharpe   = r["sharpe_ratio"]
                    best_params   = params
                    best_is_result = r
            except Exception:
                continue

        n_trials_total += n_combos

        # ── OOS run with best IS params ───────────────────────────────────────
        try:
            strat      = strategy_factory(best_params, risk_config, symbol)
            engine     = engine_factory(initial_capital, position_size_pct, cost_model)
            oos_result = engine.run(strat, oos_df.copy(), symbol)
        except Exception as exc:
            log.warning(f"WF fold {len(folds)} OOS run failed: {exc}")
            fold_start += oos_bars
            continue

        fold_record = {
            "fold_index":       len(folds),
            "is_start":         is_start_label,
            "is_end":           is_end_label,
            "oos_start":        oos_start_label,
            "oos_end":          oos_end_label,
            "best_params":      best_params,
            "is_sharpe":        best_is_result["sharpe_ratio"] if best_is_result else 0.0,
            "is_return_pct":    best_is_result["total_return_pct"] if best_is_result else 0.0,
            "oos_sharpe":       oos_result["sharpe_ratio"],
            "oos_return_pct":   oos_result["total_return_pct"],
            "oos_expectancy":   oos_result["expectancy"],
            "oos_profit_factor": oos_result["profit_factor"],
            "oos_trades":       oos_result["total_trades"],
        }
        folds.append(fold_record)
        oos_chunks.append(oos_result["equity_curve"])
        oos_pnls.extend(t["pnl"] for t in oos_result["trades"])

        fold_start += oos_bars

    if not folds:
        raise ValueError("No folds completed. Increase data period or reduce is_bars/oos_bars.")

    # ── Stitch OOS equity & compute aggregate OOS metrics ─────────────────────
    stitched   = _stitch_oos_equity(oos_chunks, initial_capital)
    oos_values = np.array([p["value"] for p in stitched])
    final_val  = float(oos_values[-1]) if len(oos_values) > 0 else initial_capital
    oos_ret    = (final_val - initial_capital) / initial_capital * 100
    oos_m      = _m.compute_all(oos_pnls, oos_values, oos_ret, ann_N)

    # ── Deflated Sharpe Ratio ─────────────────────────────────────────────────
    oos_rets = np.diff(oos_values) / oos_values[:-1] if len(oos_values) > 1 else np.array([])
    sk, ku   = _m.moments_of_returns(oos_values)
    dsr      = _m.deflated_sharpe_ratio(
        sr_annualized=oos_m["sharpe_ratio"],
        n_obs=len(oos_values),
        n_trials=n_trials_total,
        skewness=sk,
        excess_kurtosis=ku,
        ann_N=ann_N,
    )

    # ── Monte Carlo permutation test ──────────────────────────────────────────
    mc = _m.mc_permutation_test(oos_values, n_permutations=n_mc, ann_N=ann_N)

    step = max(1, len(stitched) // 500)

    return {
        "n_folds":              len(folds),
        "n_trials":             n_trials_total,
        "is_bars":              is_bars,
        "oos_bars":             oos_bars,
        "folds":                folds,
        "oos_equity_curve":     stitched[::step],
        "oos_total_trades":     len(oos_pnls),
        # OOS aggregate metrics (label clearly as OOS per CLAUDE.md)
        "oos_return_pct":       round(oos_ret, 2),
        "oos_sharpe":           round(oos_m["sharpe_ratio"], 2),
        "oos_sortino":          round(oos_m["sortino_ratio"], 2),
        "oos_expectancy":       oos_m["expectancy"],
        "oos_profit_factor":    oos_m["profit_factor"],
        "oos_max_drawdown_pct": round(oos_m["max_drawdown_pct"], 2),
        # Statistical validity
        "dsr":                  round(dsr, 4),
        "mc_pvalue":            mc["p_value"],
        "mc_significant":       mc["significant"],
        "mc_verdict":           mc["verdict"],
        "kpi_pass": {
            "oos_expectancy":    bool(oos_m["expectancy"] > 0),
            "oos_profit_factor": bool(oos_m["profit_factor"] >= 1.5),
            "oos_sharpe":        bool(oos_m["sharpe_ratio"] >= 1.5),
            "oos_max_drawdown":  bool(oos_m["max_drawdown_pct"] < 10),
            "dsr_significant":   bool(dsr >= 0.95),
            "mc_significant":    bool(mc["significant"]),
        },
    }
