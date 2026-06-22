"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getEvents } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  TrendingUp, TrendingDown, AlertTriangle, ScanSearch,
  Bot, Cpu, Info, Filter, RefreshCw
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";

// ── Types ──────────────────────────────────────────────────────────────────────

interface Event {
  id: number;
  type: string;
  severity: string;
  title: string;
  body?: string;
  symbol?: string;
  pnl?: number;
  meta?: Record<string, unknown>;
  created_at: string;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const TYPE_META: Record<string, { icon: React.ElementType; label: string; dot: string }> = {
  trade_open:  { icon: TrendingUp,    label: "Trade Open",    dot: "bg-blue-400" },
  trade_close: { icon: TrendingDown,  label: "Trade Close",   dot: "bg-green-400" },
  risk_alert:  { icon: AlertTriangle, label: "Risk Alert",    dot: "bg-yellow-400" },
  scan:        { icon: ScanSearch,    label: "Scan",          dot: "bg-purple-400" },
  autopilot:   { icon: Bot,           label: "AutoPilot",     dot: "bg-indigo-400" },
  system:      { icon: Cpu,           label: "System",        dot: "bg-gray-500" },
  signal:      { icon: Info,          label: "Signal",        dot: "bg-sky-400" },
};

const SEV_COLORS: Record<string, string> = {
  success: "border-l-green-500  bg-green-500/5",
  error:   "border-l-red-500    bg-red-500/5",
  warning: "border-l-yellow-500 bg-yellow-500/5",
  info:    "border-l-blue-500   bg-blue-500/5",
};

const SEV_BADGE: Record<string, string> = {
  success: "bg-green-500/20 text-green-300 border-green-500/30",
  error:   "bg-red-500/20   text-red-300   border-red-500/30",
  warning: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  info:    "bg-blue-500/20  text-blue-300  border-blue-500/30",
};

const FILTER_TYPES = ["all", "trade_open", "trade_close", "risk_alert", "scan", "autopilot", "system"];

// ── Component ──────────────────────────────────────────────────────────────────

export default function EventsFeed() {
  const [filter, setFilter] = useState<string>("all");
  const qc = useQueryClient();

  const { data: events = [], isLoading, dataUpdatedAt } = useQuery<Event[]>({
    queryKey: ["events", filter],
    queryFn: () => getEvents({ limit: 50, ...(filter !== "all" ? { type: filter } : {}) }),
    refetchInterval: 20_000,
  });

  const lastUpdated = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : null;

  return (
    <Card className="bg-gray-900 border-gray-800 flex flex-col h-full overflow-hidden">
      <CardHeader className="pb-3 flex-shrink-0">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <Cpu className="w-4 h-4" /> Activity Feed
            {lastUpdated && (
              <span className="text-[10px] text-gray-600 font-normal">· updated {lastUpdated}</span>
            )}
          </CardTitle>
          <button
            onClick={() => qc.invalidateQueries({ queryKey: ["events"] })}
            className="text-gray-600 hover:text-gray-400 transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Filter chips */}
        <div className="flex items-center gap-1.5 flex-wrap mt-2">
          <Filter className="w-3 h-3 text-gray-600 shrink-0" />
          {FILTER_TYPES.map(t => (
            <button
              key={t}
              onClick={() => setFilter(t)}
              className={cn(
                "text-[10px] px-2 py-0.5 rounded-full border font-medium transition-all capitalize",
                filter === t
                  ? "bg-blue-600 text-white border-blue-600"
                  : "bg-gray-800 text-gray-400 border-gray-700 hover:border-gray-600"
              )}
            >
              {t === "all" ? "All" : TYPE_META[t]?.label ?? t}
            </button>
          ))}
        </div>
      </CardHeader>

      <CardContent className="p-0 flex-1 overflow-y-auto min-h-0">
        {isLoading ? (
          <div className="py-10 text-center text-xs text-gray-600">Loading events…</div>
        ) : events.length === 0 ? (
          <div className="py-10 text-center space-y-2">
            <Cpu className="w-8 h-8 text-gray-700 mx-auto" />
            <p className="text-sm text-gray-600">No events yet</p>
            <p className="text-xs text-gray-700">Events appear here as trades open, close, and strategies fire</p>
          </div>
        ) : (
          <div className="divide-y divide-gray-800/50">
            {events.map(ev => {
              const meta = TYPE_META[ev.type] ?? TYPE_META.system;
              const Icon = meta.icon;
              const pnlPos = ev.pnl != null && ev.pnl >= 0;

              return (
                <div
                  key={ev.id}
                  className={cn(
                    "flex gap-3 px-4 py-3 border-l-2 transition-colors hover:bg-gray-800/20",
                    SEV_COLORS[ev.severity] ?? SEV_COLORS.info
                  )}
                >
                  {/* Icon */}
                  <div className="shrink-0 mt-0.5">
                    <div className={cn("w-6 h-6 rounded-full flex items-center justify-center", `${meta.dot}/20`)}>
                      <Icon className={cn("w-3.5 h-3.5", `text-${meta.dot.replace("bg-", "").replace("-400", "")}-400`)} />
                    </div>
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2 flex-wrap">
                      <p className="text-xs font-semibold text-white leading-snug">{ev.title}</p>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {ev.pnl != null && (
                          <span className={cn("text-xs font-semibold tabular-nums",
                            pnlPos ? "text-green-400" : "text-red-400"
                          )}>
                            {pnlPos ? "+" : ""}${ev.pnl.toFixed(2)}
                          </span>
                        )}
                        <Badge className={cn("text-[9px] px-1.5 py-0 border", SEV_BADGE[ev.severity] ?? SEV_BADGE.info)}>
                          {ev.severity}
                        </Badge>
                      </div>
                    </div>

                    {ev.body && (
                      <p className="text-[11px] text-gray-500 mt-0.5 leading-relaxed">{ev.body}</p>
                    )}

                    <div className="flex items-center gap-2 mt-1">
                      {ev.symbol && (
                        <span className="text-[10px] font-mono bg-gray-800 text-gray-400 px-1.5 py-0.5 rounded">
                          {ev.symbol}
                        </span>
                      )}
                      <span className="text-[10px] text-gray-700">{timeAgo(ev.created_at)}</span>
                      <span className="text-[10px] text-gray-800 capitalize">{meta.label}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
