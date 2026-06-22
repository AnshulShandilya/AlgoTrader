"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getAutomationStatus, reloadJobs, pauseAll } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import {
  Bot, RefreshCw, StopCircle, Clock, Zap,
  ShieldCheck, BarChart2, Activity, Cpu,
  TrendingUp, Sun,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface Job {
  id: string;
  name: string;
  next_run: string | null;
  next_run_in_seconds: number | null;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function countdown(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

// Classify each scheduler job into a category
type JobCategory = "risk" | "grok" | "strategy" | "universe" | "system";

function classifyJob(job: Job): JobCategory {
  const id = job.id;
  if (id === "sl_tp_monitor" || id === "drawdown_monitor")       return "risk";
  if (id.startsWith("grok") || id === "situational_awareness")  return "grok";
  if (id.startsWith("strategy_"))                                return "strategy";
  if (id.startsWith("universe"))                                  return "universe";
  return "system";
}

// Parse trade type from strategy job name (e.g. "[AUTO] #1 AAPL · day_trading")
function parseJobMeta(job: Job): { symbol?: string; tradeType?: string; timeframe?: string } {
  const name = job.name;
  // Pattern: "[AUTO] #N SYMBOL (SYMBOL) · TIMEFRAME"
  const symMatch = name.match(/\(([^)]+)\)/);
  const tfMatch  = name.match(/·\s*(\S+)$/);
  return {
    symbol:    symMatch?.[1],
    timeframe: tfMatch?.[1],
  };
}

// ── Category config ────────────────────────────────────────────────────────────

const CAT_CONFIG: Record<JobCategory, {
  label: string;
  color: string;
  dot: string;
  icon: React.ElementType;
  description: string;
}> = {
  risk: {
    label: "Risk Guard",
    color: "bg-red-500/15 text-red-300 border-red-500/30",
    dot:   "bg-red-400",
    icon:  ShieldCheck,
    description: "SL/TP monitor (30s) + drawdown kill-switch (5m) · always on",
  },
  grok: {
    label: "Grok Intel",
    color: "bg-yellow-500/15 text-yellow-300 border-yellow-500/30",
    dot:   "bg-yellow-400",
    icon:  Zap,
    description: "Grok market scans + situational awareness · catalyst-driven",
  },
  universe: {
    label: "Screener",
    color: "bg-teal-500/15 text-teal-300 border-teal-500/30",
    dot:   "bg-teal-400",
    icon:  BarChart2,
    description: "Universe screener · 770+ tickers scored every 60 min",
  },
  strategy: {
    label: "Strategy",
    color: "bg-blue-500/15 text-blue-300 border-blue-500/30",
    dot:   "bg-blue-400",
    icon:  Activity,
    description: "Technical strategy · confidence gate ≥ 50% · stop loss enforced",
  },
  system: {
    label: "System",
    color: "bg-gray-600/30 text-gray-400 border-gray-600",
    dot:   "bg-gray-500",
    icon:  Cpu,
    description: "Internal system job",
  },
};

// ── Individual job row ─────────────────────────────────────────────────────────

function JobRow({ job }: { job: Job }) {
  const cat  = classifyJob(job);
  const cfg  = CAT_CONFIG[cat];
  const Icon = cfg.icon;

  // For strategy jobs: clean up the display name
  let displayName = job.name;
  if (cat === "strategy") {
    // "[AUTO] #1 BTC/USD (BTC/USD) · 5Min" → "#1 BTC/USD · Scalp · 5Min"
    displayName = job.name
      .replace(/\[AUTO\]\s*/g, "")
      .replace(/\(([^)]+)\)\s*·?\s*/g, "")  // remove redundant (SYMBOL)
      .trim();
  }

  return (
    <div className={cn(
      "flex items-center justify-between rounded-lg px-3 py-2.5 border",
      cat === "risk" ? "bg-red-500/5 border-red-500/15"
        : cat === "grok" ? "bg-yellow-500/5 border-yellow-500/15"
        : cat === "universe" ? "bg-teal-500/5 border-teal-500/15"
        : cat === "strategy" ? "bg-blue-500/5 border-blue-500/15"
        : "bg-gray-800 border-gray-700/50"
    )}>
      <div className="flex items-center gap-2.5 min-w-0">
        {/* Live dot */}
        <div className={cn("w-1.5 h-1.5 rounded-full shrink-0 animate-pulse", cfg.dot)} />

        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-xs font-medium text-white truncate">{displayName}</p>

            {/* Category badge */}
            <Badge className={cn("text-[9px] border px-1.5 py-0 gap-0.5 shrink-0", cfg.color)}>
              <Icon className="w-2 h-2" />
              {cfg.label}
            </Badge>

            {/* Strategy-specific badges */}
            {cat === "strategy" && (
              <>
                <Badge className="text-[9px] bg-red-500/10 text-red-300 border-red-500/25 border px-1.5 py-0 gap-0.5 shrink-0">
                  <ShieldCheck className="w-2 h-2" />
                  SL on
                </Badge>
                <Badge className="text-[9px] bg-green-500/10 text-green-300 border-green-500/25 border px-1.5 py-0 gap-0.5 shrink-0">
                  conf ≥ 50%
                </Badge>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1.5 text-[10px] text-gray-500 shrink-0 ml-2">
        <Clock className="w-3 h-3" />
        <span>
          next in{" "}
          <span className="text-blue-400 font-medium tabular-nums">
            {countdown(job.next_run_in_seconds)}
          </span>
        </span>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function AutomationPanel() {
  const qc = useQueryClient();

  const { data: status, isLoading } = useQuery({
    queryKey: ["automation-status"],
    queryFn: getAutomationStatus,
    refetchInterval: 5_000,
  });

  const reloadMut = useMutation({
    mutationFn: reloadJobs,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["automation-status"] });
      toast.success("Scheduler reloaded");
    },
  });

  const pauseMut = useMutation({
    mutationFn: pauseAll,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["automation-status", "strategies"] });
      toast.warning(`Emergency stop — ${data.paused} strategies paused`);
    },
  });

  const isRunning  = status?.running;
  const jobs: Job[] = status?.jobs ?? [];

  // Separate system jobs from strategy jobs
  const systemJobs   = jobs.filter(j => classifyJob(j) !== "strategy");
  const strategyJobs = jobs.filter(j => classifyJob(j) === "strategy");

  const assetClasses = new Set(
    strategyJobs.map(j => {
      const name = j.name.toLowerCase();
      if (name.includes("/usd") || name.includes("btc") || name.includes("eth")) return "Crypto";
      if (name.includes(".l")) return "UK Stocks";
      if (name.includes("=f")) return "Commodities";
      return "US Stocks";
    })
  );

  return (
    <Card className="bg-gray-900 border-gray-800">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <Bot className="w-4 h-4" />
            Automation Engine
            <Badge className={cn(
              "text-[10px] px-2 py-0 border ml-1",
              isRunning
                ? "bg-green-500/20 text-green-400 border-green-500/30"
                : "bg-gray-700 text-gray-400 border-gray-600"
            )}>
              {isRunning ? "● LIVE" : "○ STOPPED"}
            </Badge>
          </CardTitle>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline"
              onClick={() => reloadMut.mutate()}
              disabled={reloadMut.isPending}
              className="border-gray-700 text-gray-400 hover:text-white h-7 px-2 text-xs gap-1">
              <RefreshCw className="w-3 h-3" /> Sync
            </Button>
            <Button size="sm" variant="outline"
              onClick={() => pauseMut.mutate()}
              disabled={pauseMut.isPending || jobs.length === 0}
              className="border-red-800 text-red-400 hover:text-red-300 hover:border-red-600 h-7 px-2 text-xs gap-1">
              <StopCircle className="w-3 h-3" /> Stop All
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent>
        {isLoading ? (
          <p className="text-xs text-gray-500 py-2">Loading…</p>
        ) : jobs.length === 0 ? (
          <div className="text-center py-4">
            <p className="text-xs text-gray-500">No active strategies scheduled</p>
            <p className="text-[10px] text-gray-600 mt-1">Activate a strategy to start automated trading</p>
          </div>
        ) : (
          <div className="space-y-3">

            {/* Strategy safety summary */}
            {strategyJobs.length > 0 && (
              <div className="bg-blue-500/8 border border-blue-500/20 rounded-lg px-3 py-2.5 space-y-1.5">
                <p className="text-[11px] font-semibold text-blue-300 flex items-center gap-1.5">
                  <ShieldCheck className="w-3.5 h-3.5" />
                  {strategyJobs.length} active trading {strategyJobs.length === 1 ? "strategy" : "strategies"}
                  {assetClasses.size > 0 && (
                    <span className="text-blue-400/60 font-normal">
                      · {Array.from(assetClasses).join(", ")}
                    </span>
                  )}
                </p>
                <div className="flex flex-wrap gap-x-4 gap-y-0.5">
                  {[
                    "✓ Stop loss enforced on every order",
                    "✓ Min 50% signal confidence gate",
                    "✓ 2:1 R:R minimum (SL/TP configured per type)",
                    "✓ Daily trade limit + loss limit active",
                    "✓ SL/TP checked every 30 seconds",
                  ].map(text => (
                    <span key={text} className="text-[10px] text-gray-500">{text}</span>
                  ))}
                </div>
              </div>
            )}

            {/* Strategy jobs */}
            {strategyJobs.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-widest px-1">
                  Trading Strategies ({strategyJobs.length})
                </p>
                {strategyJobs.map(job => <JobRow key={job.id} job={job} />)}
              </div>
            )}

            {/* System / infrastructure jobs */}
            {systemJobs.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-widest px-1">
                  Infrastructure ({systemJobs.length})
                </p>
                {systemJobs.map(job => <JobRow key={job.id} job={job} />)}
              </div>
            )}

            <div className="flex items-center gap-1.5 pt-1">
              <Zap className="w-3 h-3 text-yellow-400 shrink-0" />
              <p className="text-[10px] text-gray-500">
                {jobs.length} total jobs ·{" "}
                <span className="text-gray-400">
                  stocks respect market hours · crypto runs 24/7 (Mon–Fri)
                </span>
              </p>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
