"use client";
import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getUniverseCandidates, triggerUniverseRefresh } from "@/lib/api";
import { RefreshProgress } from "@/components/refresh-progress";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  Radar, RefreshCw, TrendingUp, TrendingDown, Minus,
  ChevronDown, ChevronUp, ArrowUpRight, ArrowDownRight,
  Activity, BarChart2, Globe, DollarSign, Bitcoin,
} from "lucide-react";
import { toast } from "sonner";

// ── Types ──────────────────────────────────────────────────────────────────────

interface ScanItem {
  symbol: string;
  asset_class: string;
  price: number;
  price_pence?: number;
  pct_change: number;
  vol_ratio: number;
  rsi: number;
  rsi_rising: boolean;
  above_ma50: boolean;
  at_20d_extreme: boolean;
  gap_pct: number;
  direction: string;
  is_penny: boolean;
  is_boom: boolean;
  quick_score: number;
  scanned_at: string;
  source?: string;
}

interface UniverseResponse {
  updated_at: string | null;
  total_screened: number;
  total_candidates: number;
  up_count: number;
  down_count: number;
  items: ScanItem[];
  error?: string;
}

// ── Constants ──────────────────────────────────────────────────────────────────

const ASSET_CLASS_TABS = [
  { key: "all",       label: "All",         icon: Globe },
  { key: "us_stock",  label: "US Stocks",   icon: DollarSign },
  { key: "uk_stock",  label: "UK Stocks",   icon: Activity },
  { key: "crypto",    label: "Crypto",      icon: Bitcoin },
  { key: "commodity", label: "Commodities", icon: BarChart2 },
] as const;

const SORT_OPTIONS = [
  { key: "pct_change", label: "% Change" },
  { key: "vol_ratio",  label: "Volume" },
  { key: "score",      label: "Score" },
  { key: "rsi",        label: "RSI" },
] as const;

const ASSET_CLASS_BADGE: Record<string, string> = {
  us_stock:  "bg-blue-500/15 text-blue-300 border-blue-500/30",
  uk_stock:  "bg-violet-500/15 text-violet-300 border-violet-500/30",
  crypto:    "bg-orange-500/15 text-orange-300 border-orange-500/30",
  commodity: "bg-yellow-500/15 text-yellow-300 border-yellow-500/30",
};

const ASSET_CLASS_LABEL: Record<string, string> = {
  us_stock:  "US",
  uk_stock:  "UK",
  crypto:    "Crypto",
  commodity: "Cmdty",
};

// ── Helpers ────────────────────────────────────────────────────────────────────

function scoreColor(s: number) {
  if (s >= 65) return "text-green-400";
  if (s >= 40) return "text-yellow-400";
  return "text-gray-500";
}

function scoreBg(s: number) {
  if (s >= 65) return "bg-green-500";
  if (s >= 40) return "bg-yellow-500";
  return "bg-gray-600";
}

function timeAgo(iso: string | null) {
  if (!iso) return "never";
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

function formatPrice(item: ScanItem) {
  if (item.asset_class === "uk_stock" && item.price_pence != null) {
    return `${item.price_pence.toFixed(0)}p`;
  }
  if (item.price < 1) return `$${item.price.toFixed(4)}`;
  if (item.price < 100) return `$${item.price.toFixed(2)}`;
  return `$${item.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

// ── Expanded detail row ─────────────────────────────────────────────────────────

function ExpandedDetail({ item }: { item: ScanItem }) {
  const flags = [
    { label: "RSI Trend",    value: item.rsi_rising ? "Rising ↑" : "Falling ↓", ok: item.rsi_rising },
    { label: "Above MA50",   value: item.above_ma50 ? "Yes" : "No",               ok: item.above_ma50 },
    { label: "20d Extreme",  value: item.at_20d_extreme ? "Yes" : "No",           ok: item.at_20d_extreme },
    { label: "Boom Signal",  value: item.is_boom ? "Yes" : "No",                  ok: item.is_boom },
    { label: "Penny Stock",  value: item.is_penny ? "Yes" : "No",                 ok: false },
  ];

  return (
    <tr>
      <td colSpan={9} className="px-4 pb-4 pt-0">
        <div className="bg-gray-950 border border-gray-800/60 rounded-lg p-4">
          <div className="grid grid-cols-3 gap-6">
            {/* Signal flags */}
            <div>
              <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest mb-2">Signal Flags</p>
              <div className="space-y-1.5">
                {flags.map(f => (
                  <div key={f.label} className="flex justify-between items-center">
                    <span className="text-[11px] text-gray-500">{f.label}</span>
                    <span className={cn("text-[11px] font-medium", f.ok ? "text-green-400" : "text-gray-500")}>
                      {f.value}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Price context */}
            <div>
              <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest mb-2">Price Context</p>
              <div className="space-y-1.5">
                {[
                  ["Price",       formatPrice(item)],
                  ["Gap Today",   `${item.gap_pct > 0 ? "+" : ""}${item.gap_pct.toFixed(2)}%`],
                  ["RSI",         `${item.rsi.toFixed(1)}`],
                  ["Vol / Avg",   `${item.vol_ratio.toFixed(2)}×`],
                  ["Direction",   item.direction],
                ].map(([k, v]) => (
                  <div key={k} className="flex justify-between items-center">
                    <span className="text-[11px] text-gray-500">{k}</span>
                    <span className="text-[11px] text-gray-300 font-mono">{v}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Score breakdown */}
            <div>
              <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest mb-2">Score Components</p>
              {[
                { label: "Move",    val: Math.min(Math.abs(item.pct_change) * 5, 30), max: 30 },
                { label: "Volume",  val: item.vol_ratio > 1 ? Math.min((item.vol_ratio - 1) * 15, 25) : 0, max: 25 },
                { label: "RSI",     val: item.rsi_rising ? 10 : 0, max: 10 },
                { label: "MA50",    val: item.above_ma50 ? 15 : 0, max: 15 },
                { label: "Extreme", val: item.at_20d_extreme ? 10 : 0, max: 10 },
                { label: "Gap",     val: Math.abs(item.gap_pct) > 1 ? 5 : 0, max: 5 },
              ].map(s => (
                <div key={s.label} className="flex items-center gap-2 mb-1">
                  <span className="text-[10px] text-gray-500 w-14 shrink-0">{s.label}</span>
                  <div className="flex-1 bg-gray-800 rounded-full h-1">
                    <div
                      className={cn("h-1 rounded-full", s.val > 0 ? "bg-blue-500" : "bg-gray-700")}
                      style={{ width: `${(s.val / s.max) * 100}%` }}
                    />
                  </div>
                  <span className="text-[10px] text-gray-400 font-mono w-6 text-right">{s.val.toFixed(0)}</span>
                  <span className="text-[10px] text-gray-700">/{s.max}</span>
                </div>
              ))}
              {item.source === "curated_fallback" && (
                <p className="text-[10px] text-gray-600 mt-2">source: curated watchlist</p>
              )}
            </div>
          </div>
        </div>
      </td>
    </tr>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function ScanPage() {
  const qc = useQueryClient();
  const [assetClass, setAssetClass] = useState<string>("all");
  const [sortBy, setSortBy] = useState<string>("pct_change");
  const [direction, setDirection] = useState<string>("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const { data, isLoading } = useQuery<UniverseResponse>({
    queryKey: ["universe-candidates", assetClass, sortBy, direction],
    queryFn: () => getUniverseCandidates({ asset_class: assetClass, sort_by: sortBy, direction, limit: 100 }),
    refetchInterval: 5 * 60_000,
  });

  const refreshMut = useMutation({
    mutationFn: triggerUniverseRefresh,
    onSuccess: (res) => {
      toast.success(`Scan complete — ${res.screened} tickers screened`);
      qc.invalidateQueries({ queryKey: ["universe-candidates"] });
    },
    onError: () => toast.error("Scan failed"),
  });

  const items = data?.items ?? [];
  const toggle = (sym: string) =>
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(sym) ? next.delete(sym) : next.add(sym);
      return next;
    });

  const topMover = items.length ? items.reduce((a, b) =>
    Math.abs(b.pct_change) > Math.abs(a.pct_change) ? b : a
  ) : null;

  return (
    <>
    <RefreshProgress isActive={refreshMut.isPending} config="universe_scan" />
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Radar className="w-6 h-6 text-blue-400" />
            Market Scanner
          </h1>
          <p className="text-sm text-gray-400 mt-0.5">
            {data?.total_screened
              ? `${data.total_screened.toLocaleString()} tickers screened · ${data.total_candidates} active setups`
              : "Multi-asset universe screener"}
            {data?.updated_at && (
              <span className="ml-2 text-gray-600">· updated {timeAgo(data.updated_at)}</span>
            )}
          </p>
        </div>
        <Button
          onClick={() => refreshMut.mutate()}
          disabled={refreshMut.isPending}
          className="bg-blue-600 hover:bg-blue-700 h-8 text-sm"
        >
          {refreshMut.isPending
            ? <><RefreshCw className="w-3.5 h-3.5 mr-1.5 animate-spin" />Scanning…</>
            : <><RefreshCw className="w-3.5 h-3.5 mr-1.5" />Refresh Universe</>}
        </Button>
      </div>

      {/* Summary strip */}
      {data && data.total_candidates > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            {
              label: "Screened",
              value: data.total_screened.toLocaleString(),
              color: "text-white",
            },
            {
              label: "Moving Up",
              value: `${data.up_count} / ${data.total_candidates}`,
              color: "text-green-400",
            },
            {
              label: "Moving Down",
              value: `${data.down_count} / ${data.total_candidates}`,
              color: "text-red-400",
            },
            {
              label: "Top Mover",
              value: topMover
                ? `${topMover.symbol} ${topMover.pct_change > 0 ? "+" : ""}${topMover.pct_change.toFixed(1)}%`
                : "—",
              color: topMover
                ? topMover.pct_change > 0 ? "text-green-400" : "text-red-400"
                : "text-gray-500",
            },
          ].map(c => (
            <Card key={c.label} className="bg-gray-900 border-gray-800">
              <CardContent className="px-4 py-3">
                <p className="text-[10px] text-gray-500 uppercase tracking-wide">{c.label}</p>
                <p className={cn("text-base font-bold mt-0.5 truncate", c.color)}>{c.value}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Asset class tabs */}
        <div className="flex items-center bg-gray-900 border border-gray-800 rounded-lg p-0.5 gap-0.5">
          {ASSET_CLASS_TABS.map(tab => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                onClick={() => setAssetClass(tab.key)}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all",
                  assetClass === tab.key
                    ? "bg-blue-600 text-white"
                    : "text-gray-400 hover:text-gray-200"
                )}
              >
                <Icon className="w-3 h-3" />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Direction filter */}
        <div className="flex items-center bg-gray-900 border border-gray-800 rounded-lg p-0.5 gap-0.5">
          {(["all", "up", "down"] as const).map(d => (
            <button
              key={d}
              onClick={() => setDirection(d)}
              className={cn(
                "px-3 py-1.5 rounded-md text-xs font-medium transition-all capitalize",
                direction === d
                  ? d === "up" ? "bg-green-600/30 text-green-300"
                    : d === "down" ? "bg-red-600/30 text-red-300"
                    : "bg-gray-700 text-white"
                  : "text-gray-400 hover:text-gray-200"
              )}
            >
              {d === "up" ? "↑ Up" : d === "down" ? "↓ Down" : "All"}
            </button>
          ))}
        </div>

        {/* Sort */}
        <div className="flex items-center gap-1.5 ml-auto">
          <span className="text-[11px] text-gray-600">Sort:</span>
          {SORT_OPTIONS.map(s => (
            <button
              key={s.key}
              onClick={() => setSortBy(s.key)}
              className={cn(
                "text-[11px] px-2.5 py-1 rounded-md border font-medium transition-all",
                sortBy === s.key
                  ? "bg-blue-600 text-white border-blue-600"
                  : "bg-gray-800 text-gray-400 border-gray-700 hover:border-gray-600"
              )}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {/* Results table */}
      <Card className="bg-gray-900 border-gray-800">
        <CardContent className="p-0">
          {isLoading ? (
            <div className="py-16 text-center text-gray-500 text-sm">
              <RefreshCw className="w-6 h-6 mx-auto mb-3 animate-spin text-gray-600" />
              Loading…
            </div>
          ) : items.length === 0 ? (
            <div className="py-16 text-center space-y-3">
              <Radar className="w-10 h-10 text-gray-700 mx-auto" />
              <p className="text-gray-400 text-sm font-medium">No candidates yet</p>
              <p className="text-gray-600 text-xs max-w-sm mx-auto">
                Click "Refresh Universe" to scan the full multi-asset universe.
                Results are cached — a scan runs automatically every 4 hours.
              </p>
              <Button
                onClick={() => refreshMut.mutate()}
                disabled={refreshMut.isPending}
                size="sm"
                className="bg-blue-600 hover:bg-blue-700 mt-2"
              >
                {refreshMut.isPending
                  ? <><RefreshCw className="w-3 h-3 mr-1.5 animate-spin" />Scanning…</>
                  : <><RefreshCw className="w-3 h-3 mr-1.5" />Scan Now</>}
              </Button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[11px] text-gray-500 uppercase tracking-wide border-b border-gray-800">
                    <th className="text-left px-4 py-2.5 font-medium w-8">#</th>
                    <th className="text-left px-4 py-2.5 font-medium">Symbol</th>
                    <th className="text-left px-4 py-2.5 font-medium">Class</th>
                    <th className="text-right px-4 py-2.5 font-medium">Price</th>
                    <th className="text-right px-4 py-2.5 font-medium">% Change</th>
                    <th className="text-right px-4 py-2.5 font-medium">Vol/Avg</th>
                    <th className="text-right px-4 py-2.5 font-medium">RSI</th>
                    <th className="text-left px-4 py-2.5 font-medium">Flags</th>
                    <th className="text-left px-4 py-2.5 font-medium">Score</th>
                    <th className="px-4 py-2.5 w-6" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((item, idx) => {
                    const isOpen = expanded.has(item.symbol);
                    const isUp = item.direction === "up";
                    return (
                      <React.Fragment key={item.symbol}>
                        <tr
                          onClick={() => toggle(item.symbol)}
                          className="border-b border-gray-800/50 hover:bg-gray-800/30 cursor-pointer transition-colors"
                        >
                          <td className="px-4 py-2.5 text-gray-600 text-xs font-mono">{idx + 1}</td>

                          {/* Symbol */}
                          <td className="px-4 py-2.5">
                            <div className="flex items-center gap-2">
                              <span className="font-semibold text-white text-sm">{item.symbol}</span>
                              {item.is_boom && (
                                <span className="text-[9px] bg-orange-500/20 text-orange-300 border border-orange-500/30 px-1 rounded font-bold">
                                  BOOM
                                </span>
                              )}
                            </div>
                          </td>

                          {/* Asset class */}
                          <td className="px-4 py-2.5">
                            <Badge className={cn(
                              "text-[10px] border px-1.5 py-0",
                              ASSET_CLASS_BADGE[item.asset_class] ?? "bg-gray-700 text-gray-400 border-gray-600"
                            )}>
                              {ASSET_CLASS_LABEL[item.asset_class] ?? item.asset_class}
                            </Badge>
                          </td>

                          {/* Price */}
                          <td className="px-4 py-2.5 text-right font-mono text-sm text-gray-300">
                            {formatPrice(item)}
                          </td>

                          {/* % Change */}
                          <td className="px-4 py-2.5 text-right">
                            <div className={cn(
                              "flex items-center justify-end gap-0.5 font-semibold text-sm",
                              isUp ? "text-green-400" : "text-red-400"
                            )}>
                              {isUp
                                ? <ArrowUpRight className="w-3.5 h-3.5" />
                                : <ArrowDownRight className="w-3.5 h-3.5" />}
                              {isUp ? "+" : ""}{item.pct_change.toFixed(2)}%
                            </div>
                          </td>

                          {/* Vol ratio */}
                          <td className="px-4 py-2.5 text-right">
                            <span className={cn(
                              "font-mono text-xs",
                              item.vol_ratio >= 2 ? "text-orange-400 font-semibold"
                                : item.vol_ratio >= 1.3 ? "text-yellow-400"
                                : "text-gray-500"
                            )}>
                              {item.vol_ratio.toFixed(2)}×
                            </span>
                          </td>

                          {/* RSI */}
                          <td className="px-4 py-2.5 text-right">
                            <span className={cn(
                              "font-mono text-xs",
                              item.rsi >= 70 ? "text-red-400"
                                : item.rsi <= 30 ? "text-green-400"
                                : "text-gray-400"
                            )}>
                              {item.rsi.toFixed(0)}
                            </span>
                          </td>

                          {/* Flags */}
                          <td className="px-4 py-2.5">
                            <div className="flex gap-1">
                              {item.above_ma50 && (
                                <span title="Above MA50" className="text-[9px] bg-blue-500/15 text-blue-300 border border-blue-500/25 px-1 rounded">
                                  MA50
                                </span>
                              )}
                              {item.rsi_rising && (
                                <span title="RSI Rising" className="text-[9px] bg-purple-500/15 text-purple-300 border border-purple-500/25 px-1 rounded">
                                  RSI↑
                                </span>
                              )}
                              {item.at_20d_extreme && (
                                <span title="At 20-day extreme" className="text-[9px] bg-yellow-500/15 text-yellow-300 border border-yellow-500/25 px-1 rounded">
                                  EXT
                                </span>
                              )}
                            </div>
                          </td>

                          {/* Score */}
                          <td className="px-4 py-2.5">
                            <div className="flex items-center gap-2">
                              <div className="w-14 bg-gray-800 rounded-full h-1">
                                <div
                                  className={cn("h-1 rounded-full", scoreBg(item.quick_score))}
                                  style={{ width: `${item.quick_score}%` }}
                                />
                              </div>
                              <span className={cn("text-xs font-bold font-mono", scoreColor(item.quick_score))}>
                                {item.quick_score.toFixed(0)}
                              </span>
                            </div>
                          </td>

                          {/* Expand toggle */}
                          <td className="px-4 py-2.5 text-right">
                            {isOpen
                              ? <ChevronUp className="w-3.5 h-3.5 text-gray-600" />
                              : <ChevronDown className="w-3.5 h-3.5 text-gray-600" />}
                          </td>
                        </tr>
                        {isOpen && <ExpandedDetail key={`${item.symbol}-exp`} item={item} />}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Footer legend */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[10px] text-gray-600">
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 bg-green-500 rounded-full inline-block"/>Score ≥65
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 bg-yellow-500 rounded-full inline-block"/>Score 40–65
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 bg-gray-600 rounded-full inline-block"/>Score &lt;40
        </span>
        <span className="ml-2 text-gray-700">
          Score = pct move (30) + vol spike (25) + RSI rising (10) + above MA50 (15) + 20d extreme (10) + gap (5) — capped at 100
        </span>
      </div>
    </div>
    </>
  );
}
