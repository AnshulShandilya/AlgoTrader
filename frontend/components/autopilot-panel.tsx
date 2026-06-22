"use client";
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { runAutoPilot, getLastAutoPilot, getAutoPilotProgress } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import DeployModal from "@/components/deploy-modal";
import {
  Zap, TrendingUp, CheckCircle, Loader2,
  ChevronDown, ChevronUp, ExternalLink, X,
  Download, SlidersHorizontal, BarChart3, Trophy, Rocket,
} from "lucide-react";

// ── Types ──────────────────────────────────────────────────────────────────────
type Progress = {
  phase: "idle" | "downloading" | "scoring" | "backtesting" | "done";
  pct: number;
  message: string;
  current_item: string;
  assets_done: number;
  assets_total: number;
  backtests_done: number;
  backtests_total: number;
  with_edge: number;
  elapsed_seconds: number;
};

type IndicatorSnapshot = {
  bias: string;
  bullish_votes: number;
  ema_trend: string;
  ema_stack: number;
  adx: number;
  rsi: number;
  macd_hist: number;
  stoch_k: number;
  williams_r: number;
  cci: number;
  roc: number;
  obv_trend: string;
  mfi: number;
  volume_ratio: number;
  atr_pct: number;
  bb_width: number;
  donchian_pct: number;
  supertrend_direction: string;
  hurst: number;
  market_regime: string;
  price_vs_vwap: number;
  fib_nearest_level: string;
  fib_distance_pct: number;
  ichimoku_above_cloud: boolean;
  sar_direction: string;
};

type LeaderboardRow = {
  rank: number;
  symbol: string;
  strategy: string;
  strategy_name: string;
  score: number;
  scanner_score: number;
  expectancy: number;
  profit_factor: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  total_return_pct: number;
  benchmark_return_pct: number;
  alpha_pct: number;
  total_trades: number;
  win_rate: number;
  payoff_ratio: number;
  kpi_pass: Record<string, boolean>;
  all_pass: boolean;
  start_date: string;
  end_date: string;
  indicators?: IndicatorSnapshot;
};

type AutoPilotResult = {
  status: string;
  elapsed_seconds: number;
  assets_scanned: number;
  assets_tested: number;
  combinations_run: number;
  combinations_with_edge: number;
  combinations_all_pass: number;
  top_assets: { symbol: string; scanner_score: number }[];
  leaderboard: LeaderboardRow[];
};

// ── Score bar ──────────────────────────────────────────────────────────────────
function ScoreBar({ value, max = 100 }: { value: number; max?: number }) {
  const pct = (value / max) * 100;
  const color = pct >= 60 ? "bg-green-500" : pct >= 35 ? "bg-yellow-500" : "bg-red-600";
  return (
    <div className="flex items-center gap-2 min-w-[80px]">
      <div className="flex-1 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-bold text-white w-7 text-right">{value}</span>
    </div>
  );
}

// ── Strategy pill ──────────────────────────────────────────────────────────────
const STRATEGY_COLORS: Record<string, string> = {
  adaptive:     "bg-blue-600/20 text-blue-300 border-blue-500/30",
  scalping:     "bg-purple-600/20 text-purple-300 border-purple-500/30",
  rsi:          "bg-yellow-600/20 text-yellow-300 border-yellow-500/30",
  ma_crossover: "bg-teal-600/20 text-teal-300 border-teal-500/30",
  bollinger:    "bg-orange-600/20 text-orange-300 border-orange-500/30",
  momentum:     "bg-pink-600/20 text-pink-300 border-pink-500/30",
  macd:         "bg-indigo-600/20 text-indigo-300 border-indigo-500/30",
};

function StrategyPill({ name }: { name: string }) {
  const cls = STRATEGY_COLORS[name] ?? "bg-gray-700 text-gray-300 border-gray-600";
  return (
    <span className={cn("inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium border", cls)}>
      {name === "adaptive" ? "★ " : ""}{name.replace(/_/g, " ")}
    </span>
  );
}

// ── KPI mini-check row ────────────────────────────────────────────────────────
function KpiDots({ kpi_pass }: { kpi_pass: Record<string, boolean> }) {
  return (
    <div className="flex gap-1">
      {Object.entries(kpi_pass).map(([k, v]) => (
        <span key={k} title={k.replace(/_/g, " ")}
          className={cn("w-1.5 h-1.5 rounded-full", v ? "bg-green-500" : "bg-red-600")} />
      ))}
    </div>
  );
}

// ── 20-Indicator Panel ────────────────────────────────────────────────────────
const BIAS_STYLE: Record<string, string> = {
  strong_bull: "text-green-400 bg-green-500/10 border-green-500/30",
  bull:        "text-green-300 bg-green-500/10 border-green-500/20",
  neutral:     "text-gray-400 bg-gray-700/30 border-gray-600/30",
  bear:        "text-red-400  bg-red-500/10  border-red-500/20",
  strong_bear: "text-red-500  bg-red-500/10  border-red-500/30",
};
const BIAS_LABEL: Record<string, string> = {
  strong_bull: "Strong Bull", bull: "Bull",
  neutral: "Neutral", bear: "Bear", strong_bear: "Strong Bear",
};

function IndCell({ label, value, good, warn }: {
  label: string; value: string | number; good?: boolean; warn?: boolean;
}) {
  return (
    <div className="bg-gray-800/60 rounded-lg p-2 min-w-0">
      <p className="text-[9px] text-gray-600 uppercase tracking-wide mb-0.5 truncate">{label}</p>
      <p className={cn(
        "text-xs font-bold truncate",
        good ? "text-green-400" : warn ? "text-red-400" : "text-gray-200"
      )}>{value}</p>
    </div>
  );
}

function IndicatorPanel({ ind }: { ind: IndicatorSnapshot }) {
  const biasStyle = BIAS_STYLE[ind.bias] ?? BIAS_STYLE.neutral;
  return (
    <div className="px-3 pb-3 pt-2 bg-gray-950/60 border-t border-gray-800/80 space-y-2">

      {/* Bias header */}
      <div className="flex items-center gap-2 mb-1">
        <span className={cn("inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold border", biasStyle)}>
          {BIAS_LABEL[ind.bias] ?? ind.bias}
        </span>
        <span className="text-[10px] text-gray-600">
          {ind.bullish_votes}/10 bullish signals · {ind.market_regime} market
        </span>
        <span className={cn("ml-auto text-[10px] font-medium",
          ind.ichimoku_above_cloud ? "text-green-400" : "text-red-400"
        )}>
          Ichimoku: {ind.ichimoku_above_cloud ? "above cloud ↑" : "below cloud ↓"}
        </span>
      </div>

      {/* Trend group */}
      <div>
        <p className="text-[9px] text-gray-600 uppercase tracking-widest mb-1.5">— Trend</p>
        <div className="grid grid-cols-5 gap-1.5">
          <IndCell label="EMA Stack" value={`${ind.ema_stack}/4`} good={ind.ema_stack >= 3} warn={ind.ema_stack <= 1} />
          <IndCell label="EMA Trend" value={ind.ema_trend.replace("_", " ")} good={ind.ema_trend.includes("bull")} warn={ind.ema_trend.includes("bear")} />
          <IndCell label="ADX" value={ind.adx} good={ind.adx > 25} warn={ind.adx < 15} />
          <IndCell label="Supertrend" value={ind.supertrend_direction} good={ind.supertrend_direction === "bullish"} warn={ind.supertrend_direction === "bearish"} />
          <IndCell label="SAR" value={ind.sar_direction} good={ind.sar_direction === "bullish"} warn={ind.sar_direction === "bearish"} />
        </div>
      </div>

      {/* Momentum group */}
      <div>
        <p className="text-[9px] text-gray-600 uppercase tracking-widest mb-1.5">— Momentum</p>
        <div className="grid grid-cols-6 gap-1.5">
          <IndCell label="RSI" value={ind.rsi} good={ind.rsi >= 40 && ind.rsi <= 65} warn={ind.rsi > 80 || ind.rsi < 25} />
          <IndCell label="MACD Hist" value={ind.macd_hist > 0 ? `+${ind.macd_hist}` : String(ind.macd_hist)} good={ind.macd_hist > 0} warn={ind.macd_hist < 0} />
          <IndCell label="Stoch %K" value={ind.stoch_k} good={ind.stoch_k >= 20 && ind.stoch_k <= 80} warn={ind.stoch_k > 85 || ind.stoch_k < 15} />
          <IndCell label="Williams %R" value={ind.williams_r} good={ind.williams_r > -40} warn={ind.williams_r < -75} />
          <IndCell label="CCI" value={ind.cci} good={ind.cci > 0 && ind.cci < 100} warn={Math.abs(ind.cci) > 150} />
          <IndCell label="ROC (10)" value={`${ind.roc > 0 ? "+" : ""}${ind.roc}%`} good={ind.roc > 2} warn={ind.roc < -2} />
        </div>
      </div>

      {/* Volume / Flow group */}
      <div>
        <p className="text-[9px] text-gray-600 uppercase tracking-widest mb-1.5">— Volume & Flow</p>
        <div className="grid grid-cols-3 gap-1.5">
          <IndCell label="OBV Trend" value={ind.obv_trend} good={ind.obv_trend === "rising"} warn={ind.obv_trend === "falling"} />
          <IndCell label="MFI" value={ind.mfi} good={ind.mfi >= 40 && ind.mfi <= 70} warn={ind.mfi > 80 || ind.mfi < 20} />
          <IndCell label="Vol Ratio" value={`${ind.volume_ratio}×`} good={ind.volume_ratio >= 1.2} warn={ind.volume_ratio < 0.7} />
        </div>
      </div>

      {/* Volatility / Structure */}
      <div>
        <p className="text-[9px] text-gray-600 uppercase tracking-widest mb-1.5">— Volatility & Structure</p>
        <div className="grid grid-cols-6 gap-1.5">
          <IndCell label="ATR%" value={`${ind.atr_pct}%`} good={ind.atr_pct >= 0.5 && ind.atr_pct <= 4} warn={ind.atr_pct > 6} />
          <IndCell label="BB Width" value={`${ind.bb_width}%`} good={ind.bb_width < 2.5} warn={ind.bb_width > 10} />
          <IndCell label="Donchian" value={`${(ind.donchian_pct * 100).toFixed(0)}%`} good={ind.donchian_pct > 0.8} warn={ind.donchian_pct < 0.2} />
          <IndCell label="Hurst" value={ind.hurst} good={ind.hurst > 0.55} warn={ind.hurst < 0.45} />
          <IndCell label="vs VWAP" value={`${ind.price_vs_vwap > 0 ? "+" : ""}${ind.price_vs_vwap}%`} good={ind.price_vs_vwap > 0} warn={ind.price_vs_vwap < -2} />
          <IndCell label={`Fib ${ind.fib_nearest_level}`} value={`${ind.fib_distance_pct}%`} good={ind.fib_distance_pct < 1} warn={ind.fib_distance_pct > 5} />
        </div>
      </div>
    </div>
  );
}

// ── Phase icon ─────────────────────────────────────────────────────────────────
function PhaseIcon({ phase }: { phase: Progress["phase"] }) {
  if (phase === "downloading") return <Download className="w-5 h-5 text-blue-400 animate-pulse" />;
  if (phase === "scoring")    return <SlidersHorizontal className="w-5 h-5 text-yellow-400 animate-pulse" />;
  if (phase === "backtesting") return <BarChart3 className="w-5 h-5 text-purple-400 animate-pulse" />;
  if (phase === "done")       return <Trophy className="w-5 h-5 text-green-400" />;
  return <Loader2 className="w-5 h-5 text-gray-400 animate-spin" />;
}

// ── Progress Popup ─────────────────────────────────────────────────────────────
function ProgressPopup({ onClose }: { onClose: () => void }) {
  const { data: prog } = useQuery<Progress>({
    queryKey: ["autopilot-progress"],
    queryFn: getAutoPilotProgress,
    refetchInterval: 800,
  });

  const p = prog ?? {
    phase: "idle", pct: 0, message: "Starting…",
    current_item: "", assets_done: 0, assets_total: 0,
    backtests_done: 0, backtests_total: 0, with_edge: 0, elapsed_seconds: 0,
  };

  const phaseLabel: Record<string, string> = {
    idle:         "Initialising",
    downloading:  "Downloading market data",
    scoring:      "Scoring assets",
    backtesting:  "Running backtests",
    done:         "Complete",
  };

  const barColor =
    p.phase === "done"        ? "bg-green-500" :
    p.phase === "backtesting" ? "bg-purple-500" :
    p.phase === "scoring"     ? "bg-yellow-500" :
                                "bg-blue-500";

  return (
    /* Backdrop */
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="relative w-full max-w-lg mx-4 bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl overflow-hidden">

        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-gray-800">
          <div className="flex items-center gap-3">
            <PhaseIcon phase={p.phase} />
            <div>
              <p className="text-sm font-semibold text-white">{phaseLabel[p.phase] ?? p.phase}</p>
              <p className="text-xs text-gray-500">{p.elapsed_seconds}s elapsed</p>
            </div>
          </div>
          {p.phase === "done" && (
            <button onClick={onClose}
              className="text-gray-500 hover:text-white transition-colors rounded-full p-1 hover:bg-gray-800">
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Progress bar */}
        <div className="px-6 pt-4">
          <div className="flex items-end justify-between mb-1.5">
            <span className="text-xs text-gray-400 font-medium">Progress</span>
            <span className="text-sm font-bold text-white tabular-nums">{p.pct}%</span>
          </div>
          <div className="h-3 bg-gray-800 rounded-full overflow-hidden">
            <div
              className={cn("h-full rounded-full transition-all duration-500", barColor)}
              style={{ width: `${p.pct}%` }}
            />
          </div>
        </div>

        {/* Current message */}
        <div className="px-6 py-3">
          <p className="text-xs text-gray-400 font-mono truncate">{p.message}</p>
          {p.current_item && (
            <p className="text-xs text-gray-600 truncate mt-0.5">↳ {p.current_item}</p>
          )}
        </div>

        {/* Stats grid */}
        <div className="grid grid-cols-3 gap-3 px-6 pb-4">
          <div className="bg-gray-800/60 rounded-xl p-3 text-center">
            <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">Assets</p>
            <p className="text-xl font-black text-white tabular-nums">
              {p.assets_done}
              <span className="text-xs font-normal text-gray-600">/{p.assets_total}</span>
            </p>
            <p className="text-[10px] text-gray-600 mt-0.5">downloaded</p>
          </div>
          <div className="bg-gray-800/60 rounded-xl p-3 text-center">
            <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">Backtests</p>
            <p className="text-xl font-black text-white tabular-nums">
              {p.backtests_done}
              <span className="text-xs font-normal text-gray-600">/{p.backtests_total || "…"}</span>
            </p>
            <p className="text-[10px] text-gray-600 mt-0.5">completed</p>
          </div>
          <div className="bg-gray-800/60 rounded-xl p-3 text-center">
            <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">With Edge</p>
            <p className="text-xl font-black text-green-400 tabular-nums">{p.with_edge}</p>
            <p className="text-[10px] text-gray-600 mt-0.5">found so far</p>
          </div>
        </div>

        {/* Phase steps */}
        <div className="px-6 pb-5">
          <div className="flex items-center gap-0">
            {(["downloading", "scoring", "backtesting", "done"] as const).map((ph, i, arr) => {
              const phases = ["downloading", "scoring", "backtesting", "done"];
              const currentIdx = phases.indexOf(p.phase);
              const stepIdx = phases.indexOf(ph);
              const isDone = currentIdx > stepIdx || p.phase === "done";
              const isActive = p.phase === ph;
              const labels = ["Download", "Score", "Backtest", "Done"];
              return (
                <div key={ph} className="flex items-center flex-1 last:flex-none">
                  <div className="flex flex-col items-center gap-1">
                    <div className={cn(
                      "w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold border transition-all",
                      isDone   ? "bg-green-500 border-green-400 text-white" :
                      isActive ? "bg-blue-500 border-blue-400 text-white animate-pulse" :
                                 "bg-gray-800 border-gray-700 text-gray-600"
                    )}>
                      {isDone ? "✓" : i + 1}
                    </div>
                    <span className={cn("text-[9px] whitespace-nowrap",
                      isActive ? "text-white font-medium" : isDone ? "text-green-500" : "text-gray-700"
                    )}>{labels[i]}</span>
                  </div>
                  {i < arr.length - 1 && (
                    <div className={cn("flex-1 h-px mx-1 mb-3 transition-all",
                      isDone ? "bg-green-600" : "bg-gray-800"
                    )} />
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {p.phase === "done" && (
          <div className="px-6 pb-5">
            <button
              onClick={onClose}
              className="w-full py-2.5 rounded-xl bg-green-500 hover:bg-green-400 text-black font-bold text-sm transition-colors"
            >
              View Results
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function AutoPilotPanel() {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [filterEdge, setFilterEdge] = useState(false);
  const [filterAllPass, setFilterAllPass] = useState(false);
  const [showProgress, setShowProgress] = useState(false);
  const [deployRow, setDeployRow] = useState<LeaderboardRow | null>(null);
  const [expandedIndicators, setExpandedIndicators] = useState<string | null>(null);
  const [topN, setTopN] = useState(20);
  const [inclStocks, setInclStocks] = useState(true);
  const [inclCrypto, setInclCrypto] = useState(true);
  const [inclCommodities, setInclCommodities] = useState(true);

  const totalUniverse = (inclStocks ? 150 : 0) + (inclCrypto ? 13 : 0) + (inclCommodities ? 20 : 0);

  const { data: lastResult } = useQuery<AutoPilotResult>({
    queryKey: ["autopilot-last"],
    queryFn: getLastAutoPilot,
    retry: false,
  });

  const mut = useMutation({
    mutationFn: () => runAutoPilot({
      period: "2y",
      top_n_assets: topN,
      min_trades: 8,
      include_stocks: inclStocks,
      include_crypto: inclCrypto,
      include_commodities: inclCommodities,
    }),
    onMutate: () => {
      setShowProgress(true);
      toast.info("Auto-Pilot started", {
        description: `Downloading ${totalUniverse} assets → testing top ${topN} × 7 strategies`,
      });
    },
    onSuccess: (data: AutoPilotResult) => {
      qc.setQueryData(["autopilot-last"], data);
      toast.success("Auto-Pilot complete", {
        description: `${data.combinations_with_edge} combos with edge · ${data.combinations_all_pass} all-KPI-pass`,
      });
      setExpanded(true);
    },
    onError: (e: Error) => {
      setShowProgress(false);
      toast.error("Auto-Pilot failed", { description: e.message });
    },
  });

  const result: AutoPilotResult | null = mut.data ?? lastResult ?? null;
  const isRunning = mut.isPending;

  let rows = result?.leaderboard ?? [];
  if (filterEdge)    rows = rows.filter(r => r.score > 0);
  if (filterAllPass) rows = rows.filter(r => r.all_pass);
  const displayRows = showAll ? rows : rows.slice(0, 10);
  const winner = rows[0] ?? null;

  return (
    <>
      {/* Progress popup overlay */}
      {showProgress && (
        <ProgressPopup onClose={() => setShowProgress(false)} />
      )}

      {/* Deploy confirmation modal */}
      {deployRow && (
        <DeployModal row={deployRow} onClose={() => setDeployRow(null)} />
      )}

      <Card className="bg-gray-900 border-gray-700">
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <CardTitle className="text-sm font-semibold text-white flex items-center gap-2">
                <Zap className="w-4 h-4 text-yellow-400" />
                Auto-Pilot — Find the Best Strategy
              </CardTitle>
              <CardDescription className="text-xs text-gray-500 mt-0.5">
                Scans 180+ assets (US stocks · UK FTSE · Crypto · Commodities) · 7 strategy templates · ranked by PF, DD &amp; Sharpe
              </CardDescription>
            </div>
            <div className="flex items-center gap-2 shrink-0 flex-wrap">
              {/* Config controls */}
              {!isRunning && (
                <div className="flex items-center gap-2 border border-gray-700 rounded-lg px-2.5 py-1.5 bg-gray-800/60">
                  <span className="text-[10px] text-gray-500 uppercase tracking-wide">Top</span>
                  <select
                    value={topN}
                    onChange={e => setTopN(Number(e.target.value))}
                    className="bg-transparent text-xs text-white border-none outline-none cursor-pointer"
                  >
                    {[10, 15, 20, 25, 30].map(n => (
                      <option key={n} value={n} className="bg-gray-900">{n}</option>
                    ))}
                  </select>
                  <span className="text-gray-700">|</span>
                  {[
                    { label: "Stocks", val: inclStocks, set: setInclStocks },
                    { label: "Crypto", val: inclCrypto, set: setInclCrypto },
                    { label: "Cmdty",  val: inclCommodities, set: setInclCommodities },
                  ].map(({ label, val, set }) => (
                    <button
                      key={label}
                      onClick={() => set(v => !v)}
                      className={cn(
                        "text-[10px] px-1.5 py-0.5 rounded border transition-colors",
                        val
                          ? "bg-yellow-500/15 text-yellow-300 border-yellow-500/30"
                          : "bg-gray-700/50 text-gray-600 border-gray-700 line-through"
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}

              {/* Progress tracker button */}
              {isRunning && (
                <button
                  onClick={() => setShowProgress(true)}
                  className="flex items-center gap-2 px-3 h-9 rounded-lg bg-gray-800 border border-gray-700 hover:border-gray-500 text-xs text-gray-300 transition-colors"
                >
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-yellow-400" />
                  View Progress
                </button>
              )}
              <Button
                onClick={() => mut.mutate()}
                disabled={isRunning || totalUniverse === 0}
                className="bg-yellow-500 hover:bg-yellow-400 text-black font-bold text-sm h-9 px-5 gap-2"
              >
                {isRunning
                  ? <><Loader2 className="w-4 h-4 animate-spin" />Running…</>
                  : <><Zap className="w-4 h-4" />Run Auto-Pilot</>}
              </Button>
            </div>
          </div>

          {/* Summary bar after completion */}
          {result && (
            <div className="mt-3 flex items-center gap-3 flex-wrap">
              <span className="text-xs text-gray-500">{result.elapsed_seconds}s</span>
              <span className="text-xs text-gray-600">·</span>
              <span className="text-xs text-gray-400">{result.assets_tested} assets tested</span>
              <span className="text-xs text-gray-600">·</span>
              <span className="text-xs text-gray-400">{result.combinations_run} backtests</span>
              <span className="text-xs text-gray-600">·</span>
              <span className="text-xs text-green-400 font-medium">{result.combinations_with_edge} with edge</span>
              <span className="text-xs text-gray-600">·</span>
              <span className="text-xs text-emerald-400 font-semibold">{result.combinations_all_pass} all-KPI-pass</span>
            </div>
          )}
        </CardHeader>

        {/* ── No result yet ── */}
        {!isRunning && !result && (
          <CardContent>
            <div className="py-8 text-center space-y-2">
              <Zap className="w-8 h-8 text-gray-700 mx-auto" />
              <p className="text-sm text-gray-400">Click Run Auto-Pilot to find today&apos;s best strategy</p>
              <p className="text-xs text-gray-600">
                Downloads {totalUniverse} assets (US + UK stocks · {inclCrypto ? "13 crypto · " : ""}{inclCommodities ? "20 commodities · " : ""}FTSE 100) · scores all · backtests top {topN} × 7 strategies
              </p>
            </div>
          </CardContent>
        )}

        {/* ── Running — compact status inside card ── */}
        {isRunning && (
          <CardContent>
            <div className="py-6 flex flex-col items-center gap-3 text-gray-500">
              <Loader2 className="w-7 h-7 animate-spin text-yellow-400" />
              <p className="text-sm text-gray-400">Auto-Pilot is running in the background…</p>
              <button
                onClick={() => setShowProgress(true)}
                className="mt-1 flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-800 border border-gray-700 hover:border-yellow-500/40 text-xs text-yellow-400 transition-colors"
              >
                <BarChart3 className="w-3.5 h-3.5" />
                Open live progress tracker
              </button>
            </div>
          </CardContent>
        )}

        {/* ── Results ── */}
        {!isRunning && result && result.leaderboard.length > 0 && (
          <CardContent className="pt-0 space-y-4">

            {/* Winner card */}
            {winner && (
              <div className={cn(
                "rounded-xl border p-4 flex items-start justify-between gap-4 flex-wrap",
                winner.all_pass
                  ? "bg-green-950/30 border-green-700/40"
                  : winner.score > 0
                  ? "bg-yellow-950/20 border-yellow-700/30"
                  : "bg-gray-800/50 border-gray-700"
              )}>
                <div className="space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-lg">🥇</span>
                    <span className="text-white font-bold text-lg">{winner.symbol}</span>
                    <StrategyPill name={winner.strategy} />
                    {winner.all_pass && (
                      <Badge className="bg-green-500/20 text-green-400 border-green-500/30 text-xs">
                        All KPIs Pass
                      </Badge>
                    )}
                  </div>
                  <div className="flex gap-4 text-xs flex-wrap mt-1">
                    <span className={cn("font-semibold", winner.expectancy > 0 ? "text-green-400" : "text-red-400")}>
                      Exp ${winner.expectancy > 0 ? "+" : ""}{winner.expectancy.toFixed(0)}
                    </span>
                    <span className="text-blue-300">PF {winner.profit_factor}×</span>
                    <span className="text-gray-300">DD {winner.max_drawdown_pct}%</span>
                    <span className="text-gray-300">SR {winner.sharpe_ratio}</span>
                    <span className="text-gray-400">{winner.total_trades} trades</span>
                    <span className={cn(winner.alpha_pct > 0 ? "text-green-400" : "text-red-400")}>
                      α {winner.alpha_pct > 0 ? "+" : ""}{winner.alpha_pct.toFixed(1)}%
                    </span>
                  </div>
                  <p className="text-xs text-gray-500">{winner.start_date.slice(0,10)} → {winner.end_date.slice(0,10)}</p>
                </div>
                <div className="text-right space-y-1.5">
                  <p className="text-xs text-gray-500 uppercase tracking-wide">Score</p>
                  <p className="text-3xl font-black text-white">{winner.score}</p>
                  <ScoreBar value={winner.score} />
                  <a
                    href={`/backtest?symbol=${winner.symbol}&template=${winner.strategy}`}
                    className="inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 mt-1"
                  >
                    Full backtest <ExternalLink className="w-3 h-3" />
                  </a>
                  <button
                    onClick={() => setDeployRow(winner)}
                    className="mt-2 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-500/20 hover:bg-green-500/30 border border-green-500/40 text-xs font-semibold text-green-400 transition-colors"
                  >
                    <Rocket className="w-3.5 h-3.5" /> Deploy
                  </button>
                </div>
              </div>
            )}

            {/* Filter / expand toggle */}
            <div className="flex items-center justify-between flex-wrap gap-2">
              <div className="flex gap-1.5">
                <button
                  onClick={() => setFilterEdge(v => !v)}
                  className={cn(
                    "px-2.5 py-1 rounded-full text-xs border transition-colors",
                    filterEdge
                      ? "bg-green-500/20 text-green-400 border-green-500/40"
                      : "bg-gray-800 text-gray-500 border-gray-700 hover:text-gray-300"
                  )}
                >
                  With edge only
                </button>
                <button
                  onClick={() => setFilterAllPass(v => !v)}
                  className={cn(
                    "px-2.5 py-1 rounded-full text-xs border transition-colors",
                    filterAllPass
                      ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/40"
                      : "bg-gray-800 text-gray-500 border-gray-700 hover:text-gray-300"
                  )}
                >
                  All KPIs pass
                </button>
              </div>
              <button
                onClick={() => setExpanded(v => !v)}
                className="flex items-center gap-1 text-xs text-gray-400 hover:text-white"
              >
                {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                {expanded ? "Hide" : "Show"} leaderboard
              </button>
            </div>

            {/* Full leaderboard table */}
            {expanded && (
              <div className="overflow-x-auto rounded-lg border border-gray-800">
                <table className="w-full text-xs">
                  <thead className="bg-gray-950 border-b border-gray-800">
                    <tr>
                      {["#", "Asset", "Strategy", "Score", "Expectancy", "PF", "DD%", "Sharpe", "Return%", "α%", "Trades", "KPIs", "20 Ind.", ""].map(h => (
                        <th key={h} className="px-3 py-2 text-left text-[10px] font-medium text-gray-500 uppercase tracking-wide whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-800/60">
                    {displayRows.map((row) => (
                      <React.Fragment key={`${row.symbol}-${row.strategy}`}>
                      <tr
                        className={cn(
                          "transition-colors hover:bg-gray-800/40",
                          row.all_pass ? "border-l-2 border-l-green-600/50" :
                          row.score > 0 ? "border-l-2 border-l-yellow-600/40" :
                          "border-l-2 border-l-gray-800"
                        )}
                      >
                        <td className="px-3 py-2.5">
                          <span className={cn("font-bold text-[11px]",
                            row.rank <= 3 ? "text-yellow-400" : "text-gray-600"
                          )}>
                            {row.rank <= 3 ? ["🥇","🥈","🥉"][row.rank-1] : `#${row.rank}`}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 font-semibold text-white whitespace-nowrap">{row.symbol}</td>
                        <td className="px-3 py-2.5"><StrategyPill name={row.strategy} /></td>
                        <td className="px-3 py-2.5"><ScoreBar value={row.score} /></td>
                        <td className={cn("px-3 py-2.5 font-medium tabular-nums",
                          row.expectancy > 0 ? "text-green-400" : "text-red-400")}>
                          {row.expectancy > 0 ? "+" : ""}${row.expectancy.toFixed(0)}
                        </td>
                        <td className={cn("px-3 py-2.5 tabular-nums",
                          row.profit_factor >= 1.5 ? "text-green-400" : row.profit_factor >= 1.0 ? "text-yellow-400" : "text-red-400")}>
                          {row.profit_factor}×
                        </td>
                        <td className={cn("px-3 py-2.5 tabular-nums",
                          row.max_drawdown_pct < 5 ? "text-green-400" : row.max_drawdown_pct < 10 ? "text-yellow-400" : "text-red-400")}>
                          {row.max_drawdown_pct}%
                        </td>
                        <td className={cn("px-3 py-2.5 tabular-nums",
                          row.sharpe_ratio >= 1.5 ? "text-green-400" : row.sharpe_ratio >= 0 ? "text-gray-300" : "text-red-400")}>
                          {row.sharpe_ratio}
                        </td>
                        <td className={cn("px-3 py-2.5 tabular-nums",
                          row.total_return_pct > 0 ? "text-green-300" : "text-red-400")}>
                          {row.total_return_pct > 0 ? "+" : ""}{row.total_return_pct}%
                        </td>
                        <td className={cn("px-3 py-2.5 tabular-nums",
                          row.alpha_pct > 0 ? "text-green-300" : "text-gray-500")}>
                          {row.alpha_pct > 0 ? "+" : ""}{row.alpha_pct.toFixed(1)}%
                        </td>
                        <td className="px-3 py-2.5 text-gray-400 tabular-nums">{row.total_trades}</td>
                        <td className="px-3 py-2.5"><KpiDots kpi_pass={row.kpi_pass} /></td>
                        <td className="px-3 py-2.5">
                          {row.indicators ? (
                            <button
                              onClick={() => {
                                const key = `${row.symbol}-${row.strategy}`;
                                setExpandedIndicators(v => v === key ? null : key);
                              }}
                              className={cn(
                                "flex items-center gap-1 px-2 py-0.5 rounded text-[10px] border transition-colors",
                                expandedIndicators === `${row.symbol}-${row.strategy}`
                                  ? "bg-blue-500/20 text-blue-300 border-blue-500/40"
                                  : "bg-gray-800 text-gray-500 border-gray-700 hover:text-gray-300"
                              )}
                            >
                              {row.indicators.bias.replace("_", " ")}
                              {expandedIndicators === `${row.symbol}-${row.strategy}`
                                ? <ChevronUp className="w-2.5 h-2.5" />
                                : <ChevronDown className="w-2.5 h-2.5" />}
                            </button>
                          ) : <span className="text-gray-700">—</span>}
                        </td>
                        <td className="px-3 py-2.5">
                          <div className="flex items-center gap-2">
                            <a
                              href={`/backtest?symbol=${row.symbol}&template=${row.strategy}`}
                              className="text-blue-500 hover:text-blue-400"
                              title="Open in Backtesting"
                            >
                              <ExternalLink className="w-3 h-3" />
                            </a>
                            <button
                              onClick={() => setDeployRow(row)}
                              title="Deploy this strategy"
                              className="text-green-600 hover:text-green-400 transition-colors"
                            >
                              <Rocket className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </td>
                      </tr>
                      {expandedIndicators === `${row.symbol}-${row.strategy}` && row.indicators && (
                        <tr>
                          <td colSpan={15} className="p-0">
                            <IndicatorPanel ind={row.indicators} />
                          </td>
                        </tr>
                      )}
                      </React.Fragment>
                    ))}
                  </tbody>
                </table>

                {rows.length > 10 && (
                  <div className="px-4 py-3 border-t border-gray-800 flex items-center justify-between">
                    <span className="text-xs text-gray-500">
                      Showing {displayRows.length} of {rows.length} results
                    </span>
                    <button
                      onClick={() => setShowAll(v => !v)}
                      className="text-xs text-blue-400 hover:text-blue-300"
                    >
                      {showAll ? "Show top 10" : `Show all ${rows.length}`}
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Scanned assets mini-strip */}
            <div className="flex items-center gap-2 flex-wrap pt-1">
              <span className="text-[10px] text-gray-600 uppercase tracking-wide">Assets tested:</span>
              {result.top_assets.map(a => (
                <span key={a.symbol} className="text-[10px] text-gray-500 bg-gray-800 rounded px-1.5 py-0.5">
                  {a.symbol} <span className="text-gray-700">{a.scanner_score}</span>
                </span>
              ))}
            </div>
          </CardContent>
        )}
      </Card>
    </>
  );
}
