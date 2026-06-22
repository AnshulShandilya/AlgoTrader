"use client";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { runBacktest, runPairsBacktest, findPairs, runWalkForward } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  AreaChart, Area, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { FlaskConical, TrendingUp, TrendingDown, CheckCircle, XCircle, Loader2, Search, AlertTriangle } from "lucide-react";
import AssetSearch from "@/components/asset-search";
import { RefreshProgress } from "@/components/refresh-progress";

// ── Types ─────────────────────────────────────────────────────────────────────
type BacktestResult = {
  strategy_type: string;
  strategy_name?: string;
  symbol?: string; symbol1?: string; symbol2?: string;
  start_date: string; end_date: string;
  initial_capital: number; final_capital: number;
  total_return_pct: number;
  gross_return_pct?: number;
  total_costs_paid?: number;
  benchmark_return_pct?: number;
  alpha_pct?: number;
  annualization_n?: number;
  low_sample_warning?: boolean;
  cost_model?: { commission_bps: number; half_spread_bps: number; slippage_k: number };
  position_sizer?: { type: string; fraction?: number; annual_vol_target?: number };
  regime_gate?: { hurst_min: number; hurst_max: number; vol_pct_max: number } | null;
  // Phase 5 pairs fields
  hedge_ratio_method?: string;
  kalman_beta_final?: number | null;
  halflife_bars?: number | null;
  halflife_warning?: boolean;
  cointegration_pvalue?: number | null;
  cointegrated?: boolean;
  total_trades: number; winning_trades: number; losing_trades: number;
  // Primary profitability
  expectancy: number;
  profit_factor: number;
  payoff_ratio: number;
  // Secondary risk
  max_drawdown_pct: number; sharpe_ratio: number; sortino_ratio: number; calmar_ratio: number;
  // Statistical significance
  deflated_sharpe_ratio?: number;
  mc_permutation?: { p_value: number; significant: boolean; verdict: string };
  // Informational
  win_rate: number;
  avg_win_pct?: number; avg_loss_pct?: number;
  equity_curve: { date: string; value: number; gross?: number; bah?: number }[];
  zscore_series?: { date: string; zscore: number }[];
  monthly_returns: { month: string; return_pct: number }[];
  trades: {
    entry_date: string; exit_date: string; side?: string;
    entry_price?: number; exit_price?: number;
    entry_zscore?: number; exit_zscore?: number;
    pnl: number; gross_pnl?: number; cost_paid?: number; pnl_pct: number; reason: string;
  }[];
  kpi_pass: { expectancy: boolean; profit_factor: boolean; max_drawdown: boolean; sharpe: boolean; dsr?: boolean };
};

type PairResult = { symbol1: string; symbol2: string; pvalue: number; correlation: number; cointegrated: boolean };

type WFResult = {
  n_folds: number;
  n_trials: number;
  is_bars: number;
  oos_bars: number;
  folds: {
    fold_index: number;
    is_start: string; is_end: string;
    oos_start: string; oos_end: string;
    best_params: Record<string, number>;
    is_sharpe: number; is_return_pct: number;
    oos_sharpe: number; oos_return_pct: number;
    oos_expectancy: number; oos_profit_factor: number;
    oos_trades: number;
  }[];
  oos_equity_curve: { date: string; value: number }[];
  oos_total_trades: number;
  oos_return_pct: number;
  oos_sharpe: number;
  oos_sortino: number;
  oos_expectancy: number;
  oos_profit_factor: number;
  oos_max_drawdown_pct: number;
  dsr: number;
  mc_pvalue: number;
  mc_significant: boolean;
  mc_verdict: string;
  kpi_pass: {
    oos_expectancy: boolean;
    oos_profit_factor: boolean;
    oos_sharpe: boolean;
    oos_max_drawdown: boolean;
    dsr_significant: boolean;
    mc_significant: boolean;
  };
};

// ── KPI Card ──────────────────────────────────────────────────────────────────
function KpiCard({
  label, value, sub, pass, neutral
}: { label: string; value: string; sub?: string; pass?: boolean; neutral?: boolean }) {
  const color = neutral ? "text-white" : pass ? "text-green-400" : "text-red-400";
  const bg    = neutral ? "bg-gray-800" : pass ? "bg-green-500/10 border-green-500/30" : "bg-red-500/10 border-red-500/30";
  return (
    <Card className={cn("border", bg)}>
      <CardContent className="p-4">
        <p className="text-xs text-gray-400 uppercase tracking-wide">{label}</p>
        <p className={cn("text-2xl font-bold mt-1", color)}>{value}</p>
        {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
        {pass !== undefined && (
          <div className="flex items-center gap-1 mt-2">
            {pass
              ? <><CheckCircle className="w-3.5 h-3.5 text-green-400" /><span className="text-xs text-green-400">Pass</span></>
              : <><XCircle  className="w-3.5 h-3.5 text-red-400"   /><span className="text-xs text-red-400">Below target</span></>}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Monthly returns heatmap ───────────────────────────────────────────────────
function MonthlyHeatmap({ data }: { data: { month: string; return_pct: number }[] }) {
  if (!data?.length) return null;
  return (
    <div>
      <p className="text-xs text-gray-400 mb-2 uppercase tracking-wide">Monthly Returns</p>
      <div className="flex flex-wrap gap-1">
        {data.map(m => {
          const pct = m.return_pct;
          const bg  = pct > 3 ? "bg-green-600" : pct > 1 ? "bg-green-700/70" : pct > 0 ? "bg-green-900/60"
                    : pct < -3 ? "bg-red-600"  : pct < -1 ? "bg-red-700/70"  : "bg-red-900/60";
          return (
            <div
              key={m.month}
              title={`${m.month}: ${pct > 0 ? "+" : ""}${pct}%`}
              className={cn("rounded px-1.5 py-0.5 text-xs font-mono text-white cursor-default", bg)}
            >
              {m.month.slice(5)} {pct > 0 ? "+" : ""}{pct}%
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Result Analysis (plain-English interpreter) ───────────────────────────────
function ResultAnalysis({ result, template }: { result: BacktestResult; template: string }) {
  const {
    total_trades, winning_trades, losing_trades, win_rate,
    expectancy, profit_factor, payoff_ratio,
    max_drawdown_pct, sharpe_ratio, sortino_ratio,
    total_return_pct, benchmark_return_pct, alpha_pct,
    total_costs_paid, kpi_pass, low_sample_warning,
    avg_win_pct, avg_loss_pct,
    cointegrated, halflife_bars, halflife_warning,
    cointegration_pvalue, strategy_type,
  } = result;

  const passCount  = Object.values(kpi_pass).filter(Boolean).length;
  const totalKpis  = Object.values(kpi_pass).length;
  const hasEdge    = kpi_pass.expectancy && kpi_pass.profit_factor;
  const startYear  = result.start_date.slice(0, 4);
  const endYear    = result.end_date.slice(0, 4);
  const years      = Math.max(0.1, (new Date(result.end_date).getTime() - new Date(result.start_date).getTime()) / (365.25 * 86400000));
  const tradesPerWeek = (total_trades / (years * 52)).toFixed(1);
  const costDrag   = total_costs_paid && result.gross_return_pct != null
    ? (result.gross_return_pct - total_return_pct).toFixed(2) : null;
  const isPairs    = strategy_type === "pairs";

  // ── Verdict ────────────────────────────────────────────────────────────────
  const verdictColor = hasEdge
    ? passCount === totalKpis ? "text-green-400" : "text-yellow-400"
    : "text-red-400";
  const verdictIcon  = hasEdge ? (passCount === totalKpis ? "✅" : "⚠️") : "❌";
  const verdictText  = hasEdge
    ? passCount === totalKpis
      ? "Strong edge detected — all KPIs pass."
      : "Edge exists but some risk KPIs need attention."
    : "No statistical edge yet — strategy loses money per trade after costs.";

  // ── Sections ────────────────────────────────────────────────────────────────
  type Bullet = { icon: string; color: string; text: string };
  const bullets: Bullet[] = [];

  // Sample size
  if (low_sample_warning || total_trades < 30) {
    bullets.push({
      icon: "⚠️", color: "text-yellow-400",
      text: `Only ${total_trades} trade${total_trades !== 1 ? "s" : ""} in ${years.toFixed(1)} years — results may not be statistically reliable. Aim for 30+ trades before trusting any metric.`,
    });
  } else {
    bullets.push({
      icon: "📊", color: "text-blue-400",
      text: `${total_trades} trades over ${years.toFixed(1)} years (${tradesPerWeek} per week). ${total_trades >= 50 ? "Good sample size — metrics are meaningful." : "Moderate sample — treat with caution until you see 50+ trades."}`,
    });
  }

  // Expectancy
  if (expectancy > 0) {
    bullets.push({
      icon: "💰", color: "text-green-400",
      text: `Expectancy is $+${expectancy.toFixed(2)} per trade after costs. Every time this strategy fires, it is expected to earn that amount on average. ${expectancy > 200 ? "That is a strong edge." : expectancy > 50 ? "Modest but real edge." : "Very thin edge — fragile to market changes."}`,
    });
  } else {
    bullets.push({
      icon: "🔴", color: "text-red-400",
      text: `Expectancy is $${expectancy.toFixed(2)} per trade — negative after costs. The strategy is losing money on average even before considering drawdowns. This is the most important metric to fix first.`,
    });
  }

  // Profit factor
  if (profit_factor >= 1.5) {
    bullets.push({
      icon: "✅", color: "text-green-400",
      text: `Profit factor ${profit_factor}× means for every $1 lost, the strategy earns $${profit_factor.toFixed(2)}. ${profit_factor >= 2.0 ? "Excellent — well above the 1.5× target." : "Above target, but keep monitoring."}`,
    });
  } else if (profit_factor >= 1.0) {
    bullets.push({
      icon: "⚠️", color: "text-yellow-400",
      text: `Profit factor ${profit_factor}× — above 1 (not losing) but below the 1.5× live-trading threshold. Winners are not earning enough versus losers to be confident.`,
    });
  } else {
    bullets.push({
      icon: "❌", color: "text-red-400",
      text: `Profit factor ${profit_factor}× is below 1.0 — the strategy's total losses exceed total gains. Even with a high win rate this is not profitable.`,
    });
  }

  // Win rate / payoff interpretation
  if (avg_win_pct != null && avg_loss_pct != null) {
    const wrPct = (win_rate * 100).toFixed(0);
    bullets.push({
      icon: "🎯", color: "text-gray-300",
      text: `Win rate ${wrPct}% (${winning_trades}W / ${losing_trades}L). Average winner +${avg_win_pct}% vs average loser ${avg_loss_pct}%. ${payoff_ratio >= 2 ? "Payoff ratio is strong — losses are small relative to wins." : payoff_ratio >= 1 ? "Payoff ratio is balanced." : "Losers are bigger than winners — you need a high win rate to be profitable."}`,
    });
  }

  // Drawdown
  if (max_drawdown_pct < 5) {
    bullets.push({
      icon: "🛡️", color: "text-green-400",
      text: `Maximum drawdown ${max_drawdown_pct.toFixed(2)}% — very low. The strategy never fell more than ${max_drawdown_pct.toFixed(2)}% below its peak. Excellent capital protection.`,
    });
  } else if (max_drawdown_pct < 10) {
    bullets.push({
      icon: "🛡️", color: "text-yellow-400",
      text: `Maximum drawdown ${max_drawdown_pct.toFixed(2)}% — within the 10% target, but getting close. Expect ${max_drawdown_pct.toFixed(0)}% paper loss at worst before recovery.`,
    });
  } else {
    bullets.push({
      icon: "⚠️", color: "text-red-400",
      text: `Maximum drawdown ${max_drawdown_pct.toFixed(2)}% exceeds the 10% target. A real account would have lost ${max_drawdown_pct.toFixed(0)}% at the worst point. Consider tightening your stop loss or reducing position size.`,
    });
  }

  // Sharpe
  if (sharpe_ratio >= 1.5) {
    bullets.push({
      icon: "📈", color: "text-green-400",
      text: `Sharpe ratio ${sharpe_ratio.toFixed(2)} — excellent risk-adjusted return. The strategy earns ${sharpe_ratio.toFixed(2)}× the volatility it takes on. Sortino ${sortino_ratio.toFixed(2)} (downside-only vol).`,
    });
  } else if (sharpe_ratio >= 0.5) {
    bullets.push({
      icon: "📉", color: "text-yellow-400",
      text: `Sharpe ratio ${sharpe_ratio.toFixed(2)} — below the 1.5 target. Returns are not well compensated for the volatility taken. ${sharpe_ratio < 0 ? "Negative Sharpe means risk-free cash would have done better." : "Passable but insufficient for live deployment."}`,
    });
  } else {
    bullets.push({
      icon: "📉", color: "text-red-400",
      text: `Sharpe ratio ${sharpe_ratio.toFixed(2)}${sharpe_ratio < 0 ? " — negative. This strategy underperformed holding cash. Flat periods between trades drag the Sharpe down even when individual trades are profitable." : " — very low risk-adjusted return."}`,
    });
  }

  // Benchmark / alpha
  if (benchmark_return_pct != null && alpha_pct != null) {
    const beatsBH = (result.total_return_pct) > benchmark_return_pct;
    bullets.push({
      icon: beatsBH ? "🏆" : "📊", color: beatsBH ? "text-green-400" : "text-gray-400",
      text: `Strategy returned ${total_return_pct > 0 ? "+" : ""}${total_return_pct.toFixed(2)}% vs Buy & Hold ${benchmark_return_pct > 0 ? "+" : ""}${benchmark_return_pct.toFixed(2)}%. Alpha: ${alpha_pct > 0 ? "+" : ""}${alpha_pct.toFixed(2)}%. ${beatsBH ? "Outperformed buy & hold." : "Underperformed buy & hold — this strategy spends time flat while the asset compounds."}`,
    });
  }

  // Cost drag
  if (costDrag && parseFloat(costDrag) > 0.5) {
    bullets.push({
      icon: "💸", color: "text-orange-400",
      text: `Transaction costs dragged performance by ${costDrag}% (${total_costs_paid != null ? "$" + total_costs_paid.toLocaleString("en-US", { maximumFractionDigits: 0 }) : ""} total). ${parseFloat(costDrag) > 2 ? "High cost impact — consider fewer trades or a lower-cost broker." : "Moderate cost impact — within acceptable range."}`,
    });
  }

  // Pairs-specific
  if (isPairs) {
    if (cointegrated === false) {
      bullets.push({
        icon: "⚠️", color: "text-yellow-400",
        text: `Cointegration test failed (p=${cointegration_pvalue?.toFixed(3)}). The pair may not move together reliably — spread could diverge instead of reverting. Consider a different pair.`,
      });
    } else if (cointegration_pvalue != null) {
      bullets.push({
        icon: "🔗", color: "text-teal-400",
        text: `Pair is cointegrated (p=${cointegration_pvalue.toFixed(3)}) — the spread between the two assets has a statistical tendency to revert to its mean. This is the core requirement for pairs trading.`,
      });
    }
    if (halflife_bars != null) {
      const days = halflife_bars;
      bullets.push({
        icon: halflife_warning ? "⚠️" : "⏱️",
        color: halflife_warning ? "text-yellow-400" : "text-blue-400",
        text: `Spread half-life is ${days} bars. ${days < 5 ? "Very fast reversion — tight stops and exits work well." : days < 20 ? "Moderate reversion — aligns well with the strategy parameters." : days > 60 ? "Slow reversion — trades may be open for weeks. Consider longer exits." : "Good half-life for daily-bar pairs trading."}${halflife_warning ? " Half-life is unusually long — check if the pair is still cointegrated." : ""}`,
      });
    }
  }

  // Adaptive regime
  if (template === "adaptive") {
    bullets.push({
      icon: "🔄", color: "text-blue-400",
      text: `Adaptive strategy detected market regimes automatically. In trending periods it followed EMA crossovers (long/short); in sideways periods it used RSI mean reversion; in crash periods it went short or flat. Check the Trade Log to see which exit reasons dominated.`,
    });
  }

  // Recommendations
  const recs: string[] = [];
  if (!kpi_pass.expectancy || !kpi_pass.profit_factor) {
    if (total_trades < 30) recs.push("Run over a longer period (3–5 years) to get 30+ trades for reliable statistics.");
    if (total_costs_paid && total_costs_paid > 0) recs.push("Try 'Zero Cost' mode to separate strategy alpha from friction — if expectancy is positive with zero costs but negative with real costs, the strategy works but fees are too high.");
    recs.push("Tighten stop loss and widen take profit to improve payoff ratio — aim for winners at least 1.5× the size of losers.");
  }
  if (!kpi_pass.max_drawdown) recs.push("Reduce position size % or tighten stop loss % to contain the drawdown below 10%.");
  if (!kpi_pass.sharpe) {
    if (total_trades < 10) recs.push("Very few trades — Sharpe is low because the strategy sits flat most of the time. Try faster EMAs or remove the regime gate.");
    else recs.push("Sharpe is below target — run Walk-Forward Validation to check if the edge holds out-of-sample before going live.");
  }
  if (passCount === totalKpis && total_trades >= 30) recs.push("All KPIs pass. Next step: run Walk-Forward Validation to confirm the edge is not curve-fitted to this specific period.");

  return (
    <Card className="bg-gray-900 border-gray-700/50">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">{verdictIcon}</span>
          <div>
            <CardTitle className="text-sm text-white">Strategy Analysis</CardTitle>
            <CardDescription className={cn("text-xs font-medium mt-0.5", verdictColor)}>
              {verdictText}
            </CardDescription>
          </div>
          <div className="ml-auto flex items-center gap-1.5">
            <span className="text-xs text-gray-500">{passCount}/{totalKpis} KPIs pass</span>
            <div className="flex gap-0.5">
              {Array.from({ length: totalKpis }, (_, i) => (
                <div key={i} className={cn("w-2 h-2 rounded-full", i < passCount ? "bg-green-500" : "bg-red-800")} />
              ))}
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent className="pt-0 space-y-2.5">
        {bullets.map((b, i) => (
          <div key={i} className="flex gap-2.5 text-xs leading-relaxed">
            <span className="mt-0.5 shrink-0">{b.icon}</span>
            <p className={cn("text-gray-300", b.color === "text-gray-300" ? "" : "")}
               style={{ color: undefined }}>
              <span className={b.color}>{b.text.split(" — ")[0]}</span>
              {b.text.includes(" — ") && (
                <span className="text-gray-400"> — {b.text.split(" — ").slice(1).join(" — ")}</span>
              )}
            </p>
          </div>
        ))}

        {recs.length > 0 && (
          <div className="mt-3 pt-3 border-t border-gray-800">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Recommended Next Steps</p>
            <ul className="space-y-1.5">
              {recs.map((r, i) => (
                <li key={i} className="flex gap-2 text-xs text-gray-300">
                  <span className="text-blue-400 shrink-0 font-bold">{i + 1}.</span>
                  <span>{r}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Trade Table ───────────────────────────────────────────────────────────────
type Trade = BacktestResult["trades"][number];

type SortKey = "entry_date" | "exit_date" | "pnl" | "pnl_pct" | "duration" | "invested";
type SortDir = "asc" | "desc";

function tradeDuration(entry: string, exit: string): string {
  const ms = new Date(exit).getTime() - new Date(entry).getTime();
  const days = Math.floor(ms / 86400000);
  if (days >= 1) return `${days}d`;
  const hrs = Math.floor(ms / 3600000);
  if (hrs >= 1) return `${hrs}h`;
  return `${Math.floor(ms / 60000)}m`;
}

function TradeTable({
  trades,
  isPairs,
  initialCapital,
  positionSizePct,
}: {
  trades: Trade[];
  isPairs: boolean;
  initialCapital: number;
  positionSizePct: number;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("entry_date");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [filter, setFilter] = useState<"all" | "win" | "loss">("all");
  const [page, setPage] = useState(0);
  const PAGE = 20;

  function toggleSort(k: SortKey) {
    if (sortKey === k) setSortDir(d => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(k); setSortDir("desc"); }
  }

  const estimated = (initialCapital * positionSizePct) / 100;

  const filtered = trades.filter(t =>
    filter === "all" ? true : filter === "win" ? t.pnl >= 0 : t.pnl < 0
  );

  const sorted = [...filtered].sort((a, b) => {
    let va: number, vb: number;
    switch (sortKey) {
      case "entry_date": va = new Date(a.entry_date).getTime(); vb = new Date(b.entry_date).getTime(); break;
      case "exit_date":  va = new Date(a.exit_date).getTime();  vb = new Date(b.exit_date).getTime();  break;
      case "pnl":        va = a.pnl;     vb = b.pnl;     break;
      case "pnl_pct":    va = a.pnl_pct; vb = b.pnl_pct; break;
      case "duration":
        va = new Date(a.exit_date).getTime() - new Date(a.entry_date).getTime();
        vb = new Date(b.exit_date).getTime() - new Date(b.entry_date).getTime();
        break;
      default: va = 0; vb = 0;
    }
    return sortDir === "asc" ? va - vb : vb - va;
  });

  const totalPages = Math.ceil(sorted.length / PAGE);
  const page_trades = sorted.slice(page * PAGE, (page + 1) * PAGE);

  const wins  = filtered.filter(t => t.pnl >= 0).length;
  const losses = filtered.length - wins;
  const totalPnl = filtered.reduce((s, t) => s + t.pnl, 0);

  function Th({ label, k }: { label: string; k?: SortKey }) {
    return (
      <th
        className={cn(
          "px-3 py-2 text-left text-xs font-medium text-gray-400 uppercase tracking-wider whitespace-nowrap",
          k && "cursor-pointer hover:text-white select-none"
        )}
        onClick={k ? () => toggleSort(k) : undefined}
      >
        {label}
        {k && sortKey === k && (
          <span className="ml-1 text-blue-400">{sortDir === "asc" ? "↑" : "↓"}</span>
        )}
      </th>
    );
  }

  return (
    <Card className="bg-gray-900 border-gray-800">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <CardTitle className="text-sm text-white flex items-center gap-2">
              Trade Log
              <Badge variant="outline" className="text-xs border-gray-700 text-gray-400">
                {filtered.length} trades
              </Badge>
            </CardTitle>
            <CardDescription className="text-xs text-gray-500 mt-0.5">
              {wins} wins · {losses} losses ·{" "}
              <span className={cn("font-semibold", totalPnl >= 0 ? "text-green-400" : "text-red-400")}>
                {totalPnl >= 0 ? "+" : ""}${totalPnl.toLocaleString("en-US", { maximumFractionDigits: 0 })} net P&L
              </span>
            </CardDescription>
          </div>
          {/* Filter pills */}
          <div className="flex gap-1">
            {(["all", "win", "loss"] as const).map(f => (
              <button
                key={f}
                onClick={() => { setFilter(f); setPage(0); }}
                className={cn(
                  "px-3 py-1 rounded-full text-xs font-medium transition-colors",
                  filter === f
                    ? f === "win" ? "bg-green-500/20 text-green-400 border border-green-500/40"
                      : f === "loss" ? "bg-red-500/20 text-red-400 border border-red-500/40"
                      : "bg-blue-500/20 text-blue-400 border border-blue-500/40"
                    : "bg-gray-800 text-gray-500 border border-gray-700 hover:text-gray-300"
                )}
              >
                {f === "all" ? "All" : f === "win" ? "Winners" : "Losers"}
              </button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="border-b border-gray-800 bg-gray-950/50">
              <tr>
                <Th label="#" />
                <Th label="Direction" />
                <Th label="Entry Date" k="entry_date" />
                <Th label="Exit Date"  k="exit_date"  />
                <Th label="Duration"   k="duration"   />
                <Th label="Entry Price" />
                <Th label="Exit Price"  />
                <Th label="Invested"   />
                <Th label="Gross P&L"  />
                <Th label="Costs"      />
                <Th label="Net P&L"    k="pnl"        />
                <Th label="Net %"      k="pnl_pct"    />
                <Th label="Exit Reason"/>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {page_trades.map((t, i) => {
                const rowNum = page * PAGE + i + 1;
                const isWin  = t.pnl >= 0;
                const grossPnl = t.gross_pnl ?? t.pnl;
                const costs    = t.cost_paid ?? 0;
                const invested = t.entry_price
                  ? (estimated / (t.entry_price || 1)) * (t.entry_price || 0)
                  : estimated;
                const direction = isPairs
                  ? ((t.entry_zscore ?? 0) > 0 ? "SHORT SPREAD" : "LONG SPREAD")
                  : (t.side ?? "LONG").toUpperCase();
                const dirColor = isPairs
                  ? ((t.entry_zscore ?? 0) > 0 ? "text-red-400" : "text-green-400")
                  : t.side === "long" ? "text-green-400" : "text-red-400";

                return (
                  <tr
                    key={i}
                    className={cn(
                      "transition-colors hover:bg-gray-800/40",
                      isWin ? "border-l-2 border-l-green-600/30" : "border-l-2 border-l-red-600/30"
                    )}
                  >
                    <td className="px-3 py-2.5 text-gray-600 tabular-nums">{rowNum}</td>

                    {/* Direction */}
                    <td className="px-3 py-2.5">
                      <span className={cn("font-semibold", dirColor)}>{direction}</span>
                    </td>

                    {/* Entry date */}
                    <td className="px-3 py-2.5 tabular-nums">
                      <div className="text-gray-200">{t.entry_date?.slice(0, 10)}</div>
                      <div className="text-gray-600">{t.entry_date?.slice(11, 16) || "—"}</div>
                    </td>

                    {/* Exit date */}
                    <td className="px-3 py-2.5 tabular-nums">
                      <div className="text-gray-200">{t.exit_date?.slice(0, 10)}</div>
                      <div className="text-gray-600">{t.exit_date?.slice(11, 16) || "—"}</div>
                    </td>

                    {/* Duration */}
                    <td className="px-3 py-2.5 text-gray-400 tabular-nums">
                      {t.exit_date ? tradeDuration(t.entry_date, t.exit_date) : "—"}
                    </td>

                    {/* Entry price */}
                    <td className="px-3 py-2.5 text-gray-300 tabular-nums">
                      {t.entry_price != null ? `$${t.entry_price.toLocaleString("en-US", { maximumFractionDigits: 4 })}` : "—"}
                    </td>

                    {/* Exit price */}
                    <td className="px-3 py-2.5 text-gray-300 tabular-nums">
                      {t.exit_price != null ? `$${t.exit_price.toLocaleString("en-US", { maximumFractionDigits: 4 })}` : "—"}
                    </td>

                    {/* Invested (estimated position size) */}
                    <td className="px-3 py-2.5 text-blue-300 tabular-nums font-medium">
                      ${estimated.toLocaleString("en-US", { maximumFractionDigits: 0 })}
                    </td>

                    {/* Gross P&L */}
                    <td className={cn("px-3 py-2.5 tabular-nums", grossPnl >= 0 ? "text-green-300" : "text-red-300")}>
                      {grossPnl >= 0 ? "+" : ""}${grossPnl.toFixed(0)}
                    </td>

                    {/* Costs */}
                    <td className="px-3 py-2.5 text-orange-400 tabular-nums">
                      {costs > 0 ? `-$${costs.toFixed(0)}` : <span className="text-gray-700">—</span>}
                    </td>

                    {/* Net P&L */}
                    <td className={cn("px-3 py-2.5 tabular-nums font-bold", isWin ? "text-green-400" : "text-red-400")}>
                      {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(0)}
                    </td>

                    {/* Net % */}
                    <td className="px-3 py-2.5 tabular-nums">
                      <span className={cn(
                        "inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium",
                        isWin ? "bg-green-500/15 text-green-400" : "bg-red-500/15 text-red-400"
                      )}>
                        {t.pnl_pct > 0 ? "+" : ""}{t.pnl_pct}%
                      </span>
                    </td>

                    {/* Exit reason */}
                    <td className="px-3 py-2.5">
                      <span className={cn(
                        "inline-flex items-center px-2 py-0.5 rounded-full text-xs border",
                        t.reason === "take_profit"
                          ? "bg-green-500/10 text-green-400 border-green-500/30"
                          : t.reason === "stop_loss"
                          ? "bg-red-500/10 text-red-400 border-red-500/30"
                          : "bg-gray-700/50 text-gray-400 border-gray-600/50"
                      )}>
                        {t.reason?.replace(/_/g, " ") ?? "—"}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {page_trades.length === 0 && (
                <tr>
                  <td colSpan={13} className="px-3 py-8 text-center text-gray-600">No trades match this filter</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-800">
            <span className="text-xs text-gray-500">
              Showing {page * PAGE + 1}–{Math.min((page + 1) * PAGE, sorted.length)} of {sorted.length}
            </span>
            <div className="flex gap-1">
              <button
                disabled={page === 0}
                onClick={() => setPage(p => p - 1)}
                className="px-2 py-1 text-xs rounded bg-gray-800 text-gray-400 disabled:opacity-30 hover:bg-gray-700 transition-colors"
              >← Prev</button>
              {Array.from({ length: totalPages }, (_, i) => (
                <button
                  key={i}
                  onClick={() => setPage(i)}
                  className={cn(
                    "px-2 py-1 text-xs rounded transition-colors",
                    i === page ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                  )}
                >{i + 1}</button>
              ))}
              <button
                disabled={page === totalPages - 1}
                onClick={() => setPage(p => p + 1)}
                className="px-2 py-1 text-xs rounded bg-gray-800 text-gray-400 disabled:opacity-30 hover:bg-gray-700 transition-colors"
              >Next →</button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function BacktestPage() {
  const [mode, setMode] = useState<"single" | "pairs">("single");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [pairsList, setPairsList] = useState<PairResult[]>([]);
  const [wfResult, setWfResult] = useState<WFResult | null>(null);
  const [wfConfig, setWfConfig] = useState({ is_bars: 252, oos_bars: 63, period: "5y" });
  const [sizerType, setSizerType] = useState<"fixed" | "vol_target" | "kelly">("fixed");
  const [regimePreset, setRegimePreset] = useState<"off" | "relaxed" | "standard" | "strict">("off");

  // Single-asset form
  const [single, setSingle] = useState({
    symbol: "BTC/USD",
    template: "scalping",
    period: "2y",
    initial_capital: 100000,
    commission_pct: 0.1,
    position_size_pct: 10,
    parameters: { fast_ema: 9, slow_ema: 21, rsi_period: 14, timeframe: "1Day",
      // adaptive params (optional, only sent when template === "adaptive")
      hurst_lookback: 60, trend_hurst_min: 0.55, sideways_hurst_max: 0.45,
      crash_vol_pct: 85, rsi_oversold: 30, rsi_overbought: 70 } as Record<string, number | string>,
    risk_config: { stop_loss_pct: 1.0, take_profit_pct: 2.0, position_size_pct: 10.0 },
  });

  // Pairs form
  const [pairs, setPairs] = useState({
    symbol1: "BTC/USD",
    symbol2: "ETH/USD",
    lookback: 60,
    entry_zscore: 2.0,
    exit_zscore: 0.5,
    stop_zscore: 3.5,
    period: "2y",
    initial_capital: 100000,
    commission_pct: 0.1,
    position_size_pct: 20,
  });

  const runSingleMut = useMutation({
    mutationFn: () => runBacktest({
      ...(single as unknown as Record<string, unknown>),
      position_sizer: { type: sizerType, fraction: single.position_size_pct / 100, annual_vol_target: 0.15, kelly_fraction: 0.5, max_fraction: 0.25 },
      regime_gate:    { preset: regimePreset },
    }),
    onSuccess: (data) => { setResult(data); toast.success("Backtest complete"); },
    onError:   (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Backtest failed";
      toast.error(msg);
    },
  });

  const runPairsMut = useMutation({
    mutationFn: () => runPairsBacktest(pairs as unknown as Record<string, unknown>),
    onSuccess: (data) => { setResult(data); toast.success("Pairs backtest complete"); },
    onError:   (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Backtest failed";
      toast.error(msg);
    },
  });

  const findPairsMut = useMutation({
    mutationFn: () => findPairs(pairs.period),
    onSuccess: (data) => {
      setPairsList(data.pairs || []);
      toast.success(`Found ${data.cointegrated} cointegrated pairs`);
    },
    onError: () => toast.error("Pair scan failed"),
  });

  const runWFMut = useMutation({
    mutationFn: () => {
      const fe = Number(single.parameters.fast_ema);
      const se = Number(single.parameters.slow_ema);
      return runWalkForward({
        symbol: single.symbol,
        template: single.template,
        param_grid: {
          fast_ema:   [Math.max(3, fe - 4), fe, fe + 4],
          slow_ema:   [Math.max(10, se - 10), se, se + 10],
          rsi_period: [Number(single.parameters.rsi_period)],
        },
        risk_config:       single.risk_config,
        period:            wfConfig.period,
        initial_capital:   single.initial_capital,
        position_size_pct: single.position_size_pct,
        is_bars:           wfConfig.is_bars,
        oos_bars:          wfConfig.oos_bars,
        n_mc:              300,
      });
    },
    onSuccess: (data) => { setWfResult(data); toast.success(`Walk-forward complete — ${data.n_folds} folds`); },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Walk-forward failed";
      toast.error(msg);
    },
  });

  const isRunning = runSingleMut.isPending || runPairsMut.isPending;

  const f = (label: string, val: string | number, onChange: (v: string) => void, type = "text") => (
    <div>
      <Label className="text-xs text-gray-400">{label}</Label>
      <Input
        type={type}
        value={String(val)}
        onChange={e => onChange(e.target.value)}
        step={type === "number" ? 0.1 : undefined}
        className="mt-1 bg-gray-800 border-gray-700 text-white text-sm h-8 focus:border-blue-500"
      />
    </div>
  );

  return (
    <>
    <RefreshProgress
      isActive={runSingleMut.isPending || runPairsMut.isPending}
      config="backtest"
    />
    <RefreshProgress
      isActive={runWFMut.isPending}
      config="walk_forward"
    />
    <div className="p-6 space-y-6 max-w-6xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <FlaskConical className="w-6 h-6 text-blue-400" /> Backtesting Engine
          </h1>
          <p className="text-sm text-gray-400 mt-0.5">
            Validate strategies on 2–5 years of historical data before live deployment
          </p>
        </div>
        <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1">
          {(["single", "pairs"] as const).map(m => (
            <button
              key={m}
              onClick={() => { setMode(m); setResult(null); }}
              className={cn(
                "px-4 py-1.5 rounded-md text-sm font-medium transition-all",
                mode === m ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"
              )}
            >
              {m === "single" ? "Strategy" : "Pairs Trading"}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* ── Config panel ─────────────────────────────────────────────────── */}
        <div className="space-y-4">
          <Card className="bg-gray-900 border-gray-800">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm text-white">
                {mode === "single" ? "Strategy Parameters" : "Pairs Parameters"}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {mode === "single" ? (
                <>
                  <AssetSearch
                    label="Symbol"
                    value={single.symbol}
                    onChange={v => setSingle(s => ({ ...s, symbol: v }))}
                  />
                  <div>
                    <Label className="text-xs text-gray-400">Strategy Template</Label>
                    <select
                      value={single.template}
                      onChange={e => setSingle(s => ({ ...s, template: e.target.value }))}
                      className="mt-1 w-full bg-gray-800 border border-gray-700 text-white text-sm h-8 rounded-md px-2 focus:border-blue-500"
                    >
                      {[
                        { value: "adaptive",    label: "Adaptive (All-Weather) ★" },
                        { value: "scalping",    label: "Auto Scalper" },
                        { value: "rsi",         label: "RSI Mean Reversion" },
                        { value: "macd",        label: "MACD Crossover" },
                        { value: "ma_crossover",label: "MA Crossover" },
                        { value: "bollinger",   label: "Bollinger Bands" },
                        { value: "momentum",    label: "Price Momentum" },
                      ].map(t => (
                        <option key={t.value} value={t.value}>{t.label}</option>
                      ))}
                    </select>
                  </div>
                  {f("Fast EMA", single.parameters.fast_ema, v => setSingle(s => ({ ...s, parameters: { ...s.parameters, fast_ema: +v } })), "number")}
                  {f("Slow EMA", single.parameters.slow_ema, v => setSingle(s => ({ ...s, parameters: { ...s.parameters, slow_ema: +v } })), "number")}
                  {f("Stop Loss %", single.risk_config.stop_loss_pct, v => setSingle(s => ({ ...s, risk_config: { ...s.risk_config, stop_loss_pct: +v } })), "number")}
                  {f("Take Profit %", single.risk_config.take_profit_pct, v => setSingle(s => ({ ...s, risk_config: { ...s.risk_config, take_profit_pct: +v } })), "number")}
                  {/* Adaptive-only parameters */}
                  {single.template === "adaptive" && (
                    <div className="border border-blue-900/40 rounded-lg p-3 bg-blue-950/20 space-y-2 mt-1">
                      <p className="text-xs text-blue-400 font-medium uppercase tracking-wide">Regime Detection</p>
                      {f("Hurst Lookback (bars)", single.parameters.hurst_lookback ?? 60,
                         v => setSingle(s => ({ ...s, parameters: { ...s.parameters, hurst_lookback: +v } })), "number")}
                      {f("Trending if Hurst >", single.parameters.trend_hurst_min ?? 0.55,
                         v => setSingle(s => ({ ...s, parameters: { ...s.parameters, trend_hurst_min: +v } })), "number")}
                      {f("Sideways if Hurst <", single.parameters.sideways_hurst_max ?? 0.45,
                         v => setSingle(s => ({ ...s, parameters: { ...s.parameters, sideways_hurst_max: +v } })), "number")}
                      {f("Crash if Vol% >", single.parameters.crash_vol_pct ?? 85,
                         v => setSingle(s => ({ ...s, parameters: { ...s.parameters, crash_vol_pct: +v } })), "number")}
                      <p className="text-xs text-blue-400 font-medium uppercase tracking-wide mt-2">RSI Thresholds</p>
                      {f("RSI Oversold (sideways buy)", single.parameters.rsi_oversold ?? 30,
                         v => setSingle(s => ({ ...s, parameters: { ...s.parameters, rsi_oversold: +v } })), "number")}
                      {f("RSI Overbought (sideways sell)", single.parameters.rsi_overbought ?? 70,
                         v => setSingle(s => ({ ...s, parameters: { ...s.parameters, rsi_overbought: +v } })), "number")}
                    </div>
                  )}
                </>
              ) : (
                <>
                  <AssetSearch
                    label="Asset 1 (long leg)"
                    value={pairs.symbol1}
                    onChange={v => setPairs(p => ({ ...p, symbol1: v }))}
                  />
                  <AssetSearch
                    label="Asset 2 (short leg)"
                    value={pairs.symbol2}
                    onChange={v => setPairs(p => ({ ...p, symbol2: v }))}
                  />
                  {f("Lookback window (bars)", pairs.lookback, v => setPairs(p => ({ ...p, lookback: +v })), "number")}
                  {f("Entry z-score", pairs.entry_zscore, v => setPairs(p => ({ ...p, entry_zscore: +v })), "number")}
                  {f("Exit z-score", pairs.exit_zscore, v => setPairs(p => ({ ...p, exit_zscore: +v })), "number")}
                  {f("Stop z-score", pairs.stop_zscore, v => setPairs(p => ({ ...p, stop_zscore: +v })), "number")}
                </>
              )}
            </CardContent>
          </Card>

          <Card className="bg-gray-900 border-gray-800">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm text-white">Simulation Settings</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <Label className="text-xs text-gray-400">Period</Label>
                <select
                  value={mode === "single" ? single.period : pairs.period}
                  onChange={e => mode === "single"
                    ? setSingle(s => ({ ...s, period: e.target.value }))
                    : setPairs(p => ({ ...p, period: e.target.value }))}
                  className="mt-1 w-full bg-gray-800 border border-gray-700 text-white text-sm h-8 rounded-md px-2"
                >
                  {[["6mo","6 months"],["1y","1 year"],["2y","2 years"],["5y","5 years"]].map(([v,l]) => (
                    <option key={v} value={v}>{l}</option>
                  ))}
                </select>
              </div>
              {f("Initial Capital ($)", mode === "single" ? single.initial_capital : pairs.initial_capital,
                v => mode === "single" ? setSingle(s => ({ ...s, initial_capital: +v })) : setPairs(p => ({ ...p, initial_capital: +v })), "number")}
              {f("Commission %", mode === "single" ? single.commission_pct : pairs.commission_pct,
                v => mode === "single" ? setSingle(s => ({ ...s, commission_pct: +v })) : setPairs(p => ({ ...p, commission_pct: +v })), "number")}
              {f("Position Size %", mode === "single" ? single.position_size_pct : pairs.position_size_pct,
                v => mode === "single" ? setSingle(s => ({ ...s, position_size_pct: +v })) : setPairs(p => ({ ...p, position_size_pct: +v })), "number")}

              {/* Phase 3 + 4 controls — single mode only */}
              {mode === "single" && (
                <>
                  <div>
                    <Label className="text-xs text-gray-400">Position Sizer</Label>
                    <select value={sizerType} onChange={e => setSizerType(e.target.value as typeof sizerType)}
                      className="mt-1 w-full bg-gray-800 border border-gray-700 text-white text-sm h-8 rounded-md px-2">
                      <option value="fixed">Fixed Fraction</option>
                      <option value="vol_target">Vol Targeting (15% ann)</option>
                      <option value="kelly">Half-Kelly</option>
                    </select>
                  </div>
                  <div>
                    <Label className="text-xs text-gray-400">Regime Gate</Label>
                    <select value={regimePreset} onChange={e => setRegimePreset(e.target.value as typeof regimePreset)}
                      className="mt-1 w-full bg-gray-800 border border-gray-700 text-white text-sm h-8 rounded-md px-2">
                      <option value="off">Off (no gating)</option>
                      <option value="relaxed">Relaxed (H 0.2–0.8, vol &lt;90%)</option>
                      <option value="standard">Standard (H 0.3–0.7, vol &lt;80%)</option>
                      <option value="strict">Strict (H 0.4–0.6, vol &lt;65%)</option>
                    </select>
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          <Button
            onClick={() => mode === "single" ? runSingleMut.mutate() : runPairsMut.mutate()}
            disabled={isRunning || runWFMut.isPending}
            className="w-full bg-blue-600 hover:bg-blue-700"
          >
            {isRunning
              ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Running…</>
              : "Run Backtest"}
          </Button>

          {/* Walk-forward — single mode only */}
          {mode === "single" && (
            <Card className="bg-gray-900 border-gray-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs text-gray-400">Walk-Forward Validation</CardTitle>
                <CardDescription className="text-xs text-gray-600">
                  IS optimisation + OOS test, rolling forward
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  {f("IS window (bars)", wfConfig.is_bars, v => setWfConfig(c => ({ ...c, is_bars: +v })), "number")}
                  {f("OOS window (bars)", wfConfig.oos_bars, v => setWfConfig(c => ({ ...c, oos_bars: +v })), "number")}
                </div>
                <div>
                  <Label className="text-xs text-gray-400">Period</Label>
                  <select
                    value={wfConfig.period}
                    onChange={e => setWfConfig(c => ({ ...c, period: e.target.value }))}
                    className="mt-1 w-full bg-gray-800 border border-gray-700 text-white text-sm h-8 rounded-md px-2"
                  >
                    {[["2y","2 years"],["3y","3 years"],["5y","5 years"]].map(([v,l]) => (
                      <option key={v} value={v}>{l}</option>
                    ))}
                  </select>
                </div>
                <p className="text-xs text-gray-600">
                  Param grid: fast_ema ±4 · slow_ema ±10 (9 combos/fold)
                </p>
                <Button
                  onClick={() => runWFMut.mutate()}
                  disabled={runWFMut.isPending || isRunning}
                  variant="outline"
                  className="w-full border-purple-700 text-purple-300 hover:text-purple-100 text-xs"
                >
                  {runWFMut.isPending
                    ? <><Loader2 className="w-3 h-3 mr-1 animate-spin" />Optimising folds…</>
                    : "Run Walk-Forward"}
                </Button>
              </CardContent>
            </Card>
          )}

          {/* Pair scanner — only in pairs mode */}
          {mode === "pairs" && (
            <div className="space-y-2">
              <Button
                variant="outline"
                onClick={() => findPairsMut.mutate()}
                disabled={findPairsMut.isPending}
                className="w-full border-gray-700 text-gray-300 hover:text-white"
              >
                {findPairsMut.isPending
                  ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Scanning…</>
                  : <><Search className="w-4 h-4 mr-2" />Find Cointegrated Pairs</>}
              </Button>
              {pairsList.length > 0 && (
                <Card className="bg-gray-900 border-gray-800">
                  <CardContent className="p-3 space-y-1.5">
                    <p className="text-xs text-gray-400 font-medium mb-2">
                      Best pairs (sorted by cointegration strength)
                    </p>
                    {pairsList.slice(0, 8).map((pair, i) => (
                      <button
                        key={i}
                        onClick={() => setPairs(p => ({ ...p, symbol1: pair.symbol1, symbol2: pair.symbol2 }))}
                        className={cn(
                          "w-full flex items-center justify-between px-2 py-1.5 rounded text-xs hover:bg-gray-800 transition-colors",
                          pair.cointegrated ? "text-green-300" : "text-gray-400"
                        )}
                      >
                        <span>{pair.symbol1} / {pair.symbol2}</span>
                        <span>p={pair.pvalue} · r={pair.correlation}</span>
                      </button>
                    ))}
                  </CardContent>
                </Card>
              )}
            </div>
          )}
        </div>

        {/* ── Results panel ─────────────────────────────────────────────────── */}
        <div className="col-span-2 space-y-4">
          {!result && !isRunning && (
            <div className="h-96 flex flex-col items-center justify-center text-gray-600 border border-gray-800 rounded-xl">
              <FlaskConical className="w-12 h-12 mb-3 opacity-30" />
              <p className="text-sm">Configure parameters and run a backtest</p>
              <p className="text-xs mt-1 text-gray-700">Results will appear here</p>
            </div>
          )}

          {isRunning && (
            <div className="h-96 flex flex-col items-center justify-center text-gray-500 border border-gray-800 rounded-xl">
              <Loader2 className="w-10 h-10 animate-spin mb-3 text-blue-500" />
              <p className="text-sm text-gray-400">Downloading historical data & running simulation…</p>
              <p className="text-xs mt-2 text-gray-600">Usually completes in 1–3 seconds</p>
              <p className="text-xs mt-1 text-gray-700">Same symbol runs instantly on repeat — data cached for 1 hour</p>
            </div>
          )}

          {result && !isRunning && (
            <>
              {/* Header info */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white font-semibold">
                    {result.strategy_type === "pairs"
                      ? `${result.symbol1} / ${result.symbol2} · Pairs Trading`
                      : `${result.symbol} · ${result.strategy_name}`}
                  </p>
                  <p className="text-xs text-gray-400">{result.start_date} → {result.end_date}</p>
                </div>
                <div className="flex gap-2 flex-wrap justify-end">
                  {result.low_sample_warning && (
                    <Badge className="text-xs bg-yellow-500/20 text-yellow-400 border-yellow-500/30">
                      <AlertTriangle className="w-3 h-3 mr-1 inline" />
                      Low sample (&lt;30 trades)
                    </Badge>
                  )}
                  {result.position_sizer && result.position_sizer.type !== "fixed" && (
                    <Badge className="text-xs bg-purple-900/40 text-purple-300 border-purple-700/50">
                      {result.position_sizer.type === "vol_target" ? "Vol Target" : "Half-Kelly"}
                    </Badge>
                  )}
                  {result.regime_gate && (
                    <Badge className="text-xs bg-indigo-900/40 text-indigo-300 border-indigo-700/50">
                      Regime gate
                    </Badge>
                  )}
                  {result.hedge_ratio_method === "kalman" && result.kalman_beta_final != null && (
                    <Badge className="text-xs bg-teal-900/40 text-teal-300 border-teal-700/50">
                      β={result.kalman_beta_final} Kalman
                    </Badge>
                  )}
                  {result.halflife_bars != null && (
                    <Badge className={`text-xs ${result.halflife_warning ? "bg-yellow-900/40 text-yellow-300 border-yellow-700/50" : "bg-teal-900/40 text-teal-300 border-teal-700/50"}`}>
                      HL={result.halflife_bars}bars
                    </Badge>
                  )}
                  {result.cointegrated === false && (
                    <Badge className="text-xs bg-red-900/40 text-red-300 border-red-700/50">
                      <AlertTriangle className="w-3 h-3 mr-1 inline" />Not cointegrated
                    </Badge>
                  )}
                  {single.template === "adaptive" && (
                    <Badge className="text-xs bg-blue-600/20 text-blue-300 border-blue-500/40">
                      ★ Adaptive
                    </Badge>
                  )}
                  {result.annualization_n && (
                    <Badge className="text-xs bg-gray-700 text-gray-400 border-gray-600">
                      N={result.annualization_n}
                    </Badge>
                  )}
                  {(Object.entries(result.kpi_pass) as [string, boolean][]).map(([k, v]) => (
                    <Badge
                      key={k}
                      className={cn("text-xs", v
                        ? "bg-green-500/20 text-green-400 border-green-500/30"
                        : "bg-red-500/20 text-red-400 border-red-500/30")}
                    >
                      {v ? "✓" : "✗"} {k.replace(/_/g, " ")}
                    </Badge>
                  ))}
                </div>
              </div>

              {/* ── Primary profitability (CLAUDE.md) ── */}
              <div className="space-y-1">
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Primary — Edge exists?</p>
                <div className="grid grid-cols-3 gap-3">
                  <KpiCard
                    label="Expectancy"
                    value={`$${result.expectancy > 0 ? "+" : ""}${result.expectancy.toFixed(2)}`}
                    sub="avg $ earned per trade after costs"
                    pass={result.kpi_pass.expectancy}
                  />
                  <KpiCard
                    label="Profit Factor"
                    value={`${result.profit_factor}×`}
                    sub={`Payoff ratio: ${result.payoff_ratio}× · target ≥ 1.5`}
                    pass={result.kpi_pass.profit_factor}
                  />
                  <KpiCard
                    label="Return (Net / Gross)"
                    value={`${result.total_return_pct > 0 ? "+" : ""}${result.total_return_pct}%`}
                    sub={result.gross_return_pct !== undefined
                      ? `Gross ${result.gross_return_pct > 0 ? "+" : ""}${result.gross_return_pct}% · costs $${result.total_costs_paid?.toLocaleString() ?? 0} · α ${result.alpha_pct! > 0 ? "+" : ""}${result.alpha_pct}%`
                      : `$${result.final_capital.toLocaleString()}`}
                    neutral
                  />
                </div>
              </div>

              {/* ── Secondary risk ── */}
              <div className="space-y-1">
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Secondary — Risk-adjusted quality</p>
                <div className="grid grid-cols-3 gap-3">
                  <KpiCard
                    label="Sharpe Ratio"
                    value={String(result.sharpe_ratio)}
                    sub={`Sortino: ${result.sortino_ratio} · Calmar: ${result.calmar_ratio} · N=${result.annualization_n ?? 252}`}
                    pass={result.kpi_pass.sharpe}
                  />
                  <KpiCard
                    label="Max Drawdown"
                    value={`${result.max_drawdown_pct}%`}
                    sub="target < 10%"
                    pass={result.kpi_pass.max_drawdown}
                  />
                  {/* Win Rate — informational only, no pass/fail */}
                  <Card className="border bg-gray-800">
                    <CardContent className="p-4">
                      <p className="text-xs text-gray-400 uppercase tracking-wide">Win Rate <span className="text-gray-600">(informational)</span></p>
                      <p className="text-2xl font-bold mt-1 text-gray-300">{result.win_rate}%</p>
                      <p className="text-xs text-gray-500 mt-0.5">
                        {result.winning_trades}W / {result.losing_trades}L · {result.total_trades} trades
                        {result.avg_win_pct !== undefined && ` · avg +${result.avg_win_pct}% / ${result.avg_loss_pct}%`}
                      </p>
                    </CardContent>
                  </Card>
                </div>
              </div>

              {/* ── Statistical significance (DSR + Monte Carlo) ── */}
              {(result.deflated_sharpe_ratio != null || result.mc_permutation) && (
                <div className="space-y-1">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Statistical Significance</p>
                  <div className="grid grid-cols-2 gap-3">
                    {result.deflated_sharpe_ratio != null && (
                      <KpiCard
                        label="Deflated Sharpe Ratio (DSR)"
                        value={`${(result.deflated_sharpe_ratio * 100).toFixed(1)}%`}
                        sub={`Prob. Sharpe is real after selection bias · target ≥ 95% · Bailey & López de Prado 2014`}
                        pass={result.kpi_pass.dsr ?? result.deflated_sharpe_ratio >= 0.95}
                      />
                    )}
                    {result.mc_permutation && (
                      <KpiCard
                        label="Monte Carlo Permutation (500 shuffles)"
                        value={`p = ${result.mc_permutation.p_value.toFixed(3)}`}
                        sub={result.mc_permutation.verdict}
                        pass={result.mc_permutation.significant}
                      />
                    )}
                  </div>
                  <p className="text-[10px] text-gray-700 leading-relaxed">
                    DSR adjusts Sharpe for multiple-testing bias — even a single backtest can be
                    inadvertently over-fitted. MC permutation shuffles returns to test whether
                    observed Sharpe could arise by chance. Both must pass before considering
                    walk-forward validation.
                  </p>
                </div>
              )}

              {/* Plain-English analysis */}
              <ResultAnalysis result={result} template={single.template} />

              {/* Equity curve: net / gross / B&H */}
              <Card className="bg-gray-900 border-gray-800">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm text-white">Equity Curve</CardTitle>
                  <CardDescription className="text-xs text-gray-500">
                    Net after costs (blue) · Gross before costs (green dashed) · Buy &amp; Hold (gray)
                    {result.cost_model && (
                      <span className="ml-2 text-gray-600">
                        [{result.cost_model.commission_bps}bps comm + {result.cost_model.half_spread_bps}bps spread + k={result.cost_model.slippage_k}·vol]
                      </span>
                    )}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={result.equity_curve}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#6b7280" }}
                             tickFormatter={v => v.slice(0, 7)} interval="preserveStartEnd" />
                      <YAxis tick={{ fontSize: 10, fill: "#6b7280" }}
                             tickFormatter={v => `$${(v/1000).toFixed(0)}k`} />
                      <Tooltip
                        contentStyle={{ background: "#111827", border: "1px solid #374151", fontSize: 12 }}
                        formatter={(v: unknown, name: unknown) => [
                          `$${Number(v).toLocaleString()}`,
                          name === "value" ? "Net (after costs)" : name === "gross" ? "Gross (no costs)" : "Buy & Hold"
                        ]}
                      />
                      <Legend
                        formatter={(v) => v === "value" ? "Net" : v === "gross" ? "Gross" : "B&H"}
                        wrapperStyle={{ fontSize: 11, color: "#9ca3af" }}
                      />
                      <ReferenceLine y={result.initial_capital} stroke="#374151" strokeDasharray="4 4" />
                      {/* Net equity — primary line */}
                      <Line type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={2} dot={false} name="value" />
                      {/* Gross equity — what you'd get with zero costs */}
                      {result.equity_curve[0]?.gross !== undefined && (
                        <Line type="monotone" dataKey="gross" stroke="#22c55e" strokeWidth={1} dot={false}
                              strokeDasharray="5 3" name="gross" />
                      )}
                      {/* Buy & Hold benchmark */}
                      {result.equity_curve[0]?.bah !== undefined && (
                        <Line type="monotone" dataKey="bah" stroke="#4b5563" strokeWidth={1} dot={false}
                              strokeDasharray="3 2" name="bah" />
                      )}
                    </LineChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>

              {/* Z-score series (pairs only) */}
              {result.zscore_series && result.zscore_series.length > 0 && (
                <Card className="bg-gray-900 border-gray-800">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm text-white">Spread Z-Score</CardTitle>
                    <CardDescription className="text-xs text-gray-500">
                      Trades open when |z| {">"} {pairs.entry_zscore}, close when |z| {"<"} {pairs.exit_zscore}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <ResponsiveContainer width="100%" height={140}>
                      <AreaChart data={result.zscore_series}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                        <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#6b7280" }}
                               tickFormatter={v => v.slice(0, 7)} interval="preserveStartEnd" />
                        <YAxis tick={{ fontSize: 10, fill: "#6b7280" }} />
                        <Tooltip
                          contentStyle={{ background: "#111827", border: "1px solid #374151", fontSize: 12 }}
                          formatter={(v: unknown) => [Number(v).toFixed(3), "Z-Score"]}
                        />
                        <ReferenceLine y={pairs.entry_zscore}  stroke="#f59e0b" strokeDasharray="4 2" label={{ value: `+${pairs.entry_zscore}`, fill: "#f59e0b", fontSize: 10 }} />
                        <ReferenceLine y={-pairs.entry_zscore} stroke="#f59e0b" strokeDasharray="4 2" label={{ value: `-${pairs.entry_zscore}`, fill: "#f59e0b", fontSize: 10 }} />
                        <ReferenceLine y={0} stroke="#374151" />
                        <Area type="monotone" dataKey="zscore" stroke="#8b5cf6" fill="#8b5cf620" strokeWidth={1.5} dot={false} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </CardContent>
                </Card>
              )}

              {/* Monthly returns heatmap */}
              <Card className="bg-gray-900 border-gray-800">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm text-white">Monthly Returns</CardTitle>
                </CardHeader>
                <CardContent>
                  <MonthlyHeatmap data={result.monthly_returns} />
                </CardContent>
              </Card>

              {/* Full trade log */}
              {result.trades && result.trades.length > 0 && (
                <TradeTable
                  trades={result.trades}
                  isPairs={result.strategy_type === "pairs"}
                  initialCapital={result.initial_capital}
                  positionSizePct={
                    result.position_sizer?.fraction != null
                      ? result.position_sizer.fraction * 100
                      : mode === "pairs" ? pairs.position_size_pct : single.position_size_pct
                  }
                />
              )}
            </>
          )}
          {/* ── Walk-forward results ───────────────────────────────────────── */}
          {runWFMut.isPending && (
            <div className="h-48 flex flex-col items-center justify-center text-gray-500 border border-purple-900/30 rounded-xl bg-purple-950/10">
              <Loader2 className="w-8 h-8 animate-spin mb-2 text-purple-500" />
              <p className="text-sm text-gray-400">Running walk-forward — optimising IS, testing OOS…</p>
              <p className="text-xs mt-1 text-gray-600">May take 20–60 seconds with multiple folds</p>
            </div>
          )}

          {wfResult && !runWFMut.isPending && (
            <>
              <div className="border-t border-gray-800 pt-4">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <p className="text-white font-semibold text-sm">Walk-Forward Validation</p>
                    <p className="text-xs text-gray-500">
                      {wfResult.n_folds} folds · {wfResult.n_trials} IS trials · IS={wfResult.is_bars}bars / OOS={wfResult.oos_bars}bars
                    </p>
                  </div>
                  <div className="flex gap-2 flex-wrap justify-end">
                    <Badge className={wfResult.kpi_pass.dsr_significant
                      ? "bg-green-500/20 text-green-400 border-green-500/30 text-xs"
                      : "bg-red-500/20 text-red-400 border-red-500/30 text-xs"}>
                      DSR {wfResult.dsr} {wfResult.kpi_pass.dsr_significant ? "✓" : "✗"}
                    </Badge>
                    <Badge className={wfResult.mc_significant
                      ? "bg-green-500/20 text-green-400 border-green-500/30 text-xs"
                      : "bg-yellow-500/20 text-yellow-400 border-yellow-500/30 text-xs"}>
                      MC p={wfResult.mc_pvalue} {wfResult.mc_significant ? "✓" : "—"}
                    </Badge>
                  </div>
                </div>

                {/* OOS metric KPIs */}
                <div className="space-y-1 mb-3">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">OOS-Only Metrics (never seen during optimisation)</p>
                  <div className="grid grid-cols-4 gap-2">
                    <KpiCard
                      label="OOS Expectancy"
                      value={`$${wfResult.oos_expectancy > 0 ? "+" : ""}${wfResult.oos_expectancy.toFixed(2)}`}
                      pass={wfResult.kpi_pass.oos_expectancy}
                    />
                    <KpiCard
                      label="OOS Profit Factor"
                      value={`${wfResult.oos_profit_factor}×`}
                      pass={wfResult.kpi_pass.oos_profit_factor}
                    />
                    <KpiCard
                      label="OOS Sharpe"
                      value={String(wfResult.oos_sharpe)}
                      sub={`DSR: ${wfResult.dsr} · n_trials=${wfResult.n_trials}`}
                      pass={wfResult.kpi_pass.oos_sharpe}
                    />
                    <KpiCard
                      label="OOS Max DD"
                      value={`${wfResult.oos_max_drawdown_pct}%`}
                      pass={wfResult.kpi_pass.oos_max_drawdown}
                    />
                  </div>
                </div>

                {/* MC verdict */}
                <div className={`text-xs px-3 py-2 rounded border mb-3 ${wfResult.mc_significant
                  ? "bg-green-900/20 border-green-800 text-green-300"
                  : "bg-yellow-900/20 border-yellow-800 text-yellow-300"}`}>
                  <span className="font-semibold">Monte Carlo ({wfResult.mc_pvalue * 100 < 5 ? "p&lt;5%" : `p=${(wfResult.mc_pvalue*100).toFixed(1)}%`}):</span> {wfResult.mc_verdict}
                </div>

                {/* OOS equity curve */}
                <Card className="bg-gray-900 border-gray-800 mb-3">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm text-white">OOS Equity Curve (stitched)</CardTitle>
                    <CardDescription className="text-xs text-gray-500">
                      Compounded returns across all OOS windows — no IS data included
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <ResponsiveContainer width="100%" height={180}>
                      <LineChart data={wfResult.oos_equity_curve}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                        <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#6b7280" }}
                               tickFormatter={v => v.slice(0, 7)} interval="preserveStartEnd" />
                        <YAxis tick={{ fontSize: 10, fill: "#6b7280" }}
                               tickFormatter={v => `$${(v/1000).toFixed(0)}k`} />
                        <Tooltip
                          contentStyle={{ background: "#111827", border: "1px solid #374151", fontSize: 12 }}
                          formatter={(v: unknown) => [`$${Number(v).toLocaleString()}`, "OOS Equity"]}
                        />
                        <ReferenceLine y={wfResult.oos_equity_curve[0]?.value ?? 100000}
                                       stroke="#374151" strokeDasharray="4 4" />
                        <Line type="monotone" dataKey="value" stroke="#a855f7" strokeWidth={2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </CardContent>
                </Card>

                {/* Fold table */}
                <Card className="bg-gray-900 border-gray-800">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm text-white">Per-Fold Summary</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs text-gray-400">
                        <thead>
                          <tr className="border-b border-gray-800">
                            <th className="text-left pb-1 font-medium text-gray-500">OOS Period</th>
                            <th className="text-right pb-1 font-medium text-gray-500">Best Params</th>
                            <th className="text-right pb-1 font-medium text-gray-500">IS Sharpe</th>
                            <th className="text-right pb-1 font-medium text-gray-500">OOS Sharpe</th>
                            <th className="text-right pb-1 font-medium text-gray-500">OOS Return</th>
                            <th className="text-right pb-1 font-medium text-gray-500">Trades</th>
                          </tr>
                        </thead>
                        <tbody>
                          {wfResult.folds.map(fold => (
                            <tr key={fold.fold_index} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                              <td className="py-1">{fold.oos_start} → {fold.oos_end}</td>
                              <td className="py-1 text-right font-mono text-gray-500">
                                {Object.entries(fold.best_params).map(([k,v]) => `${k.replace(/_/g,"")}=${v}`).join(" ")}
                              </td>
                              <td className="py-1 text-right">{fold.is_sharpe.toFixed(2)}</td>
                              <td className={`py-1 text-right font-medium ${fold.oos_sharpe >= 1.5 ? "text-green-400" : fold.oos_sharpe >= 0 ? "text-yellow-400" : "text-red-400"}`}>
                                {fold.oos_sharpe.toFixed(2)}
                              </td>
                              <td className={`py-1 text-right ${fold.oos_return_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                                {fold.oos_return_pct > 0 ? "+" : ""}{fold.oos_return_pct.toFixed(1)}%
                              </td>
                              <td className="py-1 text-right">{fold.oos_trades}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
    </>
  );
}
