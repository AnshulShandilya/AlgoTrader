"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getTrades, getTradeStats } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Activity, TrendingUp, TrendingDown, Clock, ShieldAlert,
  ChevronDown, ChevronUp, Zap, Sun, TrendingUp as Swing,
  ExternalLink, Target, AlertTriangle, Info,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ── Types ──────────────────────────────────────────────────────────────────────

interface Trade {
  id: number;
  symbol: string;
  side: string;
  strategy_name?: string;
  qty: number;
  entry_price?: number;
  exit_price?: number;
  stop_loss_price?: number;
  take_profit_price?: number;
  exit_reason?: string;
  pnl?: number;
  pnl_pct?: number;
  r_multiple?: number;
  initial_risk?: number;
  setup_score?: number;
  expected_profit?: number;
  expected_profit_pct?: number;
  trade_type?: string;
  trade_logic?: string;
  catalyst?: string;
  catalyst_source?: string;
  status: string;
  opened_at: string;
  closed_at?: string;
  notes?: string;
}

// ── Style maps ─────────────────────────────────────────────────────────────────

const SIDE_COLORS: Record<string, string> = {
  buy:  "bg-green-500/20 text-green-400 border-green-500/30",
  sell: "bg-red-500/20 text-red-400 border-red-500/30",
};

const EXIT_COLORS: Record<string, string> = {
  stop_loss:        "text-red-400",
  take_profit:      "text-green-400",
  signal:           "text-blue-400",
  manually_cancelled: "text-yellow-400",
  end_of_data:      "text-gray-500",
};

const EXIT_LABELS: Record<string, string> = {
  stop_loss:           "SL Hit",
  take_profit:         "TP Hit",
  signal:              "Signal",
  manually_cancelled:  "Cancelled",
  end_of_data:         "EOD",
};

// ── Trade type config ──────────────────────────────────────────────────────────

const TRADE_TYPE_CONFIG: Record<string, { label: string; color: string; icon: React.ElementType; description: string }> = {
  scalping: {
    label: "Scalp",
    color: "bg-orange-500/15 text-orange-300 border-orange-500/30",
    icon: Zap,
    description: "Hold < 1 hour · tight stop · target 0.3–0.8%",
  },
  day_trading: {
    label: "Day",
    color: "bg-sky-500/15 text-sky-300 border-sky-500/30",
    icon: Sun,
    description: "Hold 2–6 hours · close same session · target 1–3%",
  },
  swing_trading: {
    label: "Swing",
    color: "bg-violet-500/15 text-violet-300 border-violet-500/30",
    icon: Swing,
    description: "Hold 1–7 days · catalyst-driven · target 3–10%+",
  },
};

// ── Helpers ────────────────────────────────────────────────────────────────────

function TradeTypeBadge({ type }: { type?: string }) {
  if (!type) return null;
  const cfg = TRADE_TYPE_CONFIG[type];
  if (!cfg) return null;
  const Icon = cfg.icon;
  return (
    <Badge className={cn("text-[10px] border gap-1 px-1.5 py-0", cfg.color)}>
      <Icon className="w-2.5 h-2.5" />
      {cfg.label}
    </Badge>
  );
}

function pct(n?: number) {
  if (n == null) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}
function dollar(n?: number, prefix = "$") {
  if (n == null) return "—";
  return `${n >= 0 ? "+" : ""}${prefix}${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const h = Math.floor(diff / 3_600_000);
  const d = Math.floor(h / 24);
  if (d > 0) return `${d}d ago`;
  if (h > 0) return `${h}h ago`;
  return `${Math.floor(diff / 60_000)}m ago`;
}

// ── Logic detail panel ─────────────────────────────────────────────────────────

function TradeLogicPanel({ trade }: { trade: Trade }) {
  const cfg = trade.trade_type ? TRADE_TYPE_CONFIG[trade.trade_type] : null;

  // Parse trade_logic string into sections ("Key: value | Key: value | …")
  const logicSections: { label: string; text: string }[] = [];
  if (trade.trade_logic) {
    trade.trade_logic.split(" | ").forEach(part => {
      const colon = part.indexOf(": ");
      if (colon > 0) {
        logicSections.push({ label: part.slice(0, colon), text: part.slice(colon + 2) });
      } else if (part.trim()) {
        logicSections.push({ label: "Logic", text: part.trim() });
      }
    });
  }

  const rr = trade.stop_loss_price && trade.entry_price && trade.take_profit_price
    ? Math.abs(trade.take_profit_price - trade.entry_price) / Math.abs(trade.entry_price - trade.stop_loss_price)
    : null;

  return (
    <div className="bg-gray-950 border border-gray-800/60 rounded-lg p-4 mt-1 space-y-4">

      {/* Trade type + description */}
      {cfg && (
        <div className="flex items-start gap-3">
          <div className={cn("flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-full border", cfg.color)}>
            <cfg.icon className="w-3 h-3" />
            {cfg.label} Trade
          </div>
          <p className="text-xs text-gray-500 mt-0.5">{cfg.description}</p>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">

        {/* Price levels + expected profit */}
        <div>
          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest mb-2 flex items-center gap-1.5">
            <Target className="w-3 h-3" /> Price Levels
          </p>
          <div className="space-y-1.5">
            {[
              ["Entry",         trade.entry_price ? `$${Number(trade.entry_price).toFixed(4)}` : "—", "text-gray-300"],
              ["Stop Loss",     trade.stop_loss_price ? `$${Number(trade.stop_loss_price).toFixed(4)}` : "—", "text-red-400"],
              ["Take Profit",   trade.take_profit_price ? `$${Number(trade.take_profit_price).toFixed(4)}` : "—", "text-green-400"],
              ["R:R Ratio",     rr ? `${rr.toFixed(1)}:1` : "—", rr && rr >= 2 ? "text-green-400" : "text-yellow-400"],
              ["Initial Risk",  trade.initial_risk ? `$${Number(trade.initial_risk).toFixed(2)}` : "—", "text-gray-400"],
            ].map(([k, v, c]) => (
              <div key={k as string} className="flex justify-between">
                <span className="text-[11px] text-gray-500">{k}</span>
                <span className={cn("text-[11px] font-mono font-medium", c as string)}>{v}</span>
              </div>
            ))}
          </div>

          {/* Expected profit callout */}
          {trade.expected_profit != null && (
            <div className="mt-3 bg-green-500/8 border border-green-500/20 rounded-lg px-3 py-2">
              <p className="text-[10px] text-gray-500 uppercase tracking-wide">Expected at Target</p>
              <p className="text-base font-bold text-green-400">
                +${Number(trade.expected_profit).toFixed(2)}
                <span className="text-xs font-normal text-green-500/70 ml-1">
                  ({trade.expected_profit_pct != null ? `+${Number(trade.expected_profit_pct).toFixed(2)}%` : ""})
                </span>
              </p>
            </div>
          )}
        </div>

        {/* Trade logic reasoning */}
        <div>
          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest mb-2 flex items-center gap-1.5">
            <Info className="w-3 h-3" /> Trade Logic
          </p>
          {logicSections.length > 0 ? (
            <div className="space-y-2">
              {logicSections.map((s, i) => (
                <div key={i}>
                  <p className="text-[10px] font-semibold text-gray-600 uppercase">{s.label}</p>
                  <p className="text-[11px] text-gray-300 leading-relaxed">{s.text}</p>
                </div>
              ))}
            </div>
          ) : trade.notes ? (
            <p className="text-[11px] text-gray-400 leading-relaxed">{trade.notes}</p>
          ) : (
            <p className="text-[11px] text-gray-600">No logic recorded for this trade.</p>
          )}
        </div>

        {/* Catalyst + source */}
        <div>
          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest mb-2 flex items-center gap-1.5">
            <AlertTriangle className="w-3 h-3" /> Catalyst
          </p>
          {trade.catalyst ? (
            <div className="space-y-2">
              <p className="text-[11px] text-gray-300 leading-relaxed">{trade.catalyst}</p>
              {trade.catalyst_source && (
                <a
                  href={trade.catalyst_source}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-[10px] text-blue-400 hover:text-blue-300 transition-colors truncate"
                >
                  <ExternalLink className="w-2.5 h-2.5 shrink-0" />
                  {trade.catalyst_source}
                </a>
              )}
            </div>
          ) : (
            <p className="text-[11px] text-gray-600">
              {trade.strategy_name?.startsWith("Grok")
                ? "Catalyst detail not recorded."
                : "Strategy-driven trade (no catalyst)."}
            </p>
          )}

          {/* Setup score */}
          {trade.setup_score != null && (
            <div className="mt-3">
              <p className="text-[10px] text-gray-500 uppercase tracking-wide">Setup Score</p>
              <div className="flex items-center gap-2 mt-1">
                <div className="flex-1 bg-gray-800 rounded-full h-1.5">
                  <div
                    className={cn("h-1.5 rounded-full", trade.setup_score >= 70 ? "bg-green-500" : trade.setup_score >= 50 ? "bg-yellow-500" : "bg-red-500")}
                    style={{ width: `${trade.setup_score}%` }}
                  />
                </div>
                <span className="text-xs font-bold text-gray-300">{trade.setup_score}</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Open position row ──────────────────────────────────────────────────────────

function OpenTradeRow({ trade, expanded, onToggle }: { trade: Trade; expanded: boolean; onToggle: () => void }) {
  const isUp = trade.side === "buy";
  return (
    <>
      <tr
        onClick={onToggle}
        className="border-b border-gray-800/50 hover:bg-gray-800/30 cursor-pointer transition-colors"
      >
        <td className="py-3 px-4">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-white">{trade.symbol}</span>
            <TradeTypeBadge type={trade.trade_type} />
          </div>
          <p className="text-[10px] text-gray-600 mt-0.5">{trade.strategy_name ?? "—"}</p>
        </td>
        <td className="py-3 px-4">
          <Badge className={cn("text-[10px] border", SIDE_COLORS[trade.side])}>{trade.side.toUpperCase()}</Badge>
        </td>
        <td className="py-3 px-4 font-mono text-xs text-gray-300">{Number(trade.qty).toFixed(4)}</td>
        <td className="py-3 px-4 text-gray-300 font-mono text-sm">
          {trade.entry_price ? `$${Number(trade.entry_price).toFixed(4)}` : "—"}
        </td>
        <td className="py-3 px-4 text-red-400 font-mono text-sm font-medium">
          {trade.stop_loss_price ? `$${Number(trade.stop_loss_price).toFixed(4)}` : <span className="text-gray-700">—</span>}
        </td>
        <td className="py-3 px-4 text-green-400 font-mono text-sm font-medium">
          {trade.take_profit_price ? `$${Number(trade.take_profit_price).toFixed(4)}` : <span className="text-gray-700">—</span>}
        </td>
        <td className="py-3 px-4">
          {trade.expected_profit != null ? (
            <div>
              <span className="text-green-400 text-sm font-semibold">+${Number(trade.expected_profit).toFixed(2)}</span>
              {trade.expected_profit_pct != null && (
                <span className="text-green-500/60 text-xs ml-1">({Number(trade.expected_profit_pct).toFixed(1)}%)</span>
              )}
            </div>
          ) : <span className="text-gray-700">—</span>}
        </td>
        <td className="py-3 px-4 text-gray-500 text-xs">{timeAgo(trade.opened_at)}</td>
        <td className="py-3 px-4">
          {expanded ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={9} className="px-4 pb-4 pt-0">
            <TradeLogicPanel trade={trade} />
          </td>
        </tr>
      )}
    </>
  );
}

// ── Closed trade row ───────────────────────────────────────────────────────────

function ClosedTradeRow({ trade, expanded, onToggle }: { trade: Trade; expanded: boolean; onToggle: () => void }) {
  const pnlPos = (trade.pnl ?? 0) >= 0;
  return (
    <>
      <tr
        onClick={onToggle}
        className="border-b border-gray-800/50 hover:bg-gray-800/30 cursor-pointer transition-colors"
      >
        <td className="py-3 px-4">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-white">{trade.symbol}</span>
            <TradeTypeBadge type={trade.trade_type} />
            {trade.side === "buy"
              ? <TrendingUp className="w-3 h-3 text-green-500" />
              : <TrendingDown className="w-3 h-3 text-red-500" />}
          </div>
          <p className="text-[10px] text-gray-600 mt-0.5">{trade.strategy_name ?? "—"}</p>
        </td>
        <td className="py-3 px-4 text-gray-400 font-mono text-xs">
          {trade.entry_price ? `$${Number(trade.entry_price).toFixed(4)}` : "—"}
          <span className="text-gray-700 mx-1">→</span>
          {trade.exit_price ? `$${Number(trade.exit_price).toFixed(4)}` : "—"}
        </td>
        <td className="py-3 px-4">
          {trade.exit_reason ? (
            <span className={cn("text-xs font-semibold", EXIT_COLORS[trade.exit_reason] ?? "text-gray-400")}>
              {EXIT_LABELS[trade.exit_reason] ?? trade.exit_reason}
            </span>
          ) : <span className="text-gray-700">—</span>}
        </td>
        <td className="py-3 px-4">
          <span className={cn("text-sm font-bold", pnlPos ? "text-green-400" : "text-red-400")}>
            {dollar(trade.pnl)}
          </span>
          <span className={cn("text-xs ml-1", pnlPos ? "text-green-500/60" : "text-red-500/60")}>
            {pct(trade.pnl_pct)}
          </span>
        </td>
        <td className="py-3 px-4">
          {trade.r_multiple != null ? (
            <span className={cn("text-xs font-semibold font-mono",
              trade.r_multiple >= 1 ? "text-green-400" : "text-red-400"
            )}>
              {trade.r_multiple >= 0 ? "+" : ""}{Number(trade.r_multiple).toFixed(2)}R
            </span>
          ) : <span className="text-gray-700">—</span>}
        </td>
        <td className="py-3 px-4">
          {trade.expected_profit != null ? (
            <span className="text-gray-500 text-xs">was +${Number(trade.expected_profit).toFixed(2)}</span>
          ) : <span className="text-gray-700">—</span>}
        </td>
        <td className="py-3 px-4 text-gray-600 text-xs">
          {trade.closed_at ? timeAgo(trade.closed_at) : timeAgo(trade.opened_at)}
        </td>
        <td className="py-3 px-4">
          {expanded ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={8} className="px-4 pb-4 pt-0">
            <TradeLogicPanel trade={trade} />
          </td>
        </tr>
      )}
    </>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function TradesPage() {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const { data: trades = [] } = useQuery({
    queryKey: ["trades"],
    queryFn: () => getTrades({ limit: 200 }),
    refetchInterval: 30_000,
  });
  const { data: stats } = useQuery({
    queryKey: ["trade-stats"],
    queryFn: getTradeStats,
    refetchInterval: 30_000,
  });

  const allTrades    = trades as Trade[];
  const openTrades   = allTrades.filter(t => t.status === "open");
  const closedTrades = allTrades.filter(t => t.status === "closed");

  const toggle = (id: number) =>
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  // Trade type breakdown for header
  const byType = allTrades.reduce<Record<string, number>>((acc, t) => {
    const k = t.trade_type ?? "unknown";
    acc[k] = (acc[k] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Trade Log</h1>
          <div className="flex items-center gap-2 mt-1.5 flex-wrap">
            <span className="text-sm text-gray-400">{openTrades.length} open · {closedTrades.length} closed</span>
            {Object.entries(byType).map(([type, count]) => {
              const cfg = TRADE_TYPE_CONFIG[type];
              if (!cfg) return null;
              return (
                <Badge key={type} className={cn("text-[10px] border gap-1", cfg.color)}>
                  <cfg.icon className="w-2.5 h-2.5" />
                  {count} {cfg.label}
                </Badge>
              );
            })}
          </div>
        </div>
        {/* Trade type legend */}
        <div className="flex flex-col gap-1">
          {Object.entries(TRADE_TYPE_CONFIG).map(([k, v]) => (
            <div key={k} className="flex items-center gap-2">
              <Badge className={cn("text-[10px] border gap-1 w-16 justify-center", v.color)}>
                <v.icon className="w-2.5 h-2.5" />{v.label}
              </Badge>
              <span className="text-[10px] text-gray-600">{v.description}</span>
            </div>
          ))}
        </div>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {[
          {
            label: "Expectancy",
            value: stats ? `${(stats.expectancy ?? 0) >= 0 ? "+" : ""}$${(stats.expectancy ?? 0).toFixed(2)}` : "—",
            color: (stats?.expectancy ?? 0) >= 0 ? "text-green-400" : "text-red-400",
            primary: true,
          },
          {
            label: "Profit Factor",
            value: stats ? String(stats.profit_factor ?? "—") : "—",
            color: (stats?.profit_factor ?? 0) >= 1.5 ? "text-green-400" : (stats?.profit_factor ?? 0) >= 1 ? "text-yellow-400" : "text-red-400",
            primary: true,
          },
          {
            label: "Total P&L",
            value: `${(stats?.total_pnl ?? 0) >= 0 ? "+" : ""}$${(stats?.total_pnl ?? 0).toFixed(2)}`,
            color: (stats?.total_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400",
          },
          {
            label: "Win Rate *",
            value: `${stats?.win_rate ?? 0}%`,
            color: "text-gray-300",
          },
          {
            label: "Avg Win / Loss",
            value: stats ? `+$${stats.avg_win?.toFixed(0) ?? "—"} / -$${Math.abs(stats.avg_loss ?? 0).toFixed(0)}` : "—",
            color: "text-gray-300",
          },
        ].map(m => (
          <Card key={m.label} className={cn("border", m.primary ? "bg-gray-800 border-blue-500/30" : "bg-gray-900 border-gray-800")}>
            <CardContent className="px-4 py-3">
              <p className="text-[10px] text-gray-500 uppercase font-medium tracking-wide">{m.label}</p>
              <p className={cn("text-lg font-bold mt-0.5", m.color)}>{m.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>
      <p className="text-[10px] text-gray-600">* Win Rate is informational only — Expectancy and Profit Factor are primary KPIs</p>

      {/* Open positions */}
      {openTrades.length > 0 && (
        <Card className="bg-gray-900 border-blue-500/20">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium text-blue-300 flex items-center gap-2">
              <ShieldAlert className="w-4 h-4" />
              Open Positions ({openTrades.length})
              <span className="text-[10px] text-gray-600 font-normal ml-1">· tap any row to see trade logic</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[11px] text-gray-500 uppercase tracking-wide border-b border-gray-800">
                    {["Symbol & Type", "Side", "Qty", "Entry", "Stop", "Target", "Exp. Profit", "Opened", ""].map(h => (
                      <th key={h} className="text-left py-2.5 px-4 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {openTrades.map(t => (
                    <OpenTradeRow
                      key={t.id}
                      trade={t}
                      expanded={expanded.has(t.id)}
                      onToggle={() => toggle(t.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Closed trades */}
      <Card className="bg-gray-900 border-gray-800">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <Activity className="w-4 h-4" />
            Closed Trades ({closedTrades.length})
            <span className="text-[10px] text-gray-600 font-normal ml-1">· tap any row to see trade logic</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {closedTrades.length === 0 ? (
            <div className="py-16 text-center">
              <Clock className="w-8 h-8 text-gray-700 mx-auto mb-3" />
              <p className="text-gray-400 text-sm font-medium">No closed trades yet</p>
              <p className="text-gray-600 text-xs mt-1">Trades appear here when SL, TP, or signal exits trigger</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[11px] text-gray-500 uppercase tracking-wide border-b border-gray-800">
                    {["Symbol & Type", "Entry → Exit", "Closed Via", "P&L", "R-Multiple", "Was Targeting", "Closed", ""].map(h => (
                      <th key={h} className="text-left py-2.5 px-4 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {closedTrades.map(t => (
                    <ClosedTradeRow
                      key={t.id}
                      trade={t}
                      expanded={expanded.has(t.id)}
                      onToggle={() => toggle(t.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
