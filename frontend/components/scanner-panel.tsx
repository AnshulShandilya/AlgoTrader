"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getLastScan, triggerScan } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { Radar, RefreshCw, TrendingUp, TrendingDown, Minus, Zap, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { RefreshProgress } from "@/components/refresh-progress";

// ── Types ──────────────────────────────────────────────────────────────────────

interface ScanResult {
  rank: number;
  symbol: string;
  score: number;
  asset_class?: string;
  trade_type?: string;
  pct_change?: number;
  vol_ratio?: number;
  rsi?: number;
  direction?: string;
  bias?: string;
  reason?: string;
  // legacy Alpaca-scanner fields (may be absent)
  atr_pct?: number;
  volume_ratio?: number;
  momentum_pct?: number;
  trend?: string;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function safe(n: number | undefined, decimals = 2, suffix = ""): string {
  if (n == null || isNaN(n)) return "—";
  return `${n.toFixed(decimals)}${suffix}`;
}

function ScoreBar({ value }: { value: number }) {
  const color = value >= 70 ? "bg-green-500" : value >= 45 ? "bg-blue-500" : "bg-gray-600";
  return (
    <div className="flex items-center gap-2">
      <div className="w-14 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full", color)} style={{ width: `${Math.min(value, 100)}%` }} />
      </div>
      <span className="text-xs text-white font-medium w-7">{value}</span>
    </div>
  );
}

const TRADE_TYPE_BADGE: Record<string, string> = {
  scalping:    "bg-orange-500/15 text-orange-300 border-orange-500/30",
  day_trading: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  swing_trading: "bg-violet-500/15 text-violet-300 border-violet-500/30",
};
const TRADE_TYPE_LABEL: Record<string, string> = {
  scalping:    "Scalp",
  day_trading: "Day",
  swing_trading: "Swing",
};

const ASSET_CLASS_BADGE: Record<string, string> = {
  us_stock:  "bg-blue-500/15 text-blue-300 border-blue-500/30",
  uk_stock:  "bg-violet-500/15 text-violet-300 border-violet-500/30",
  crypto:    "bg-orange-500/15 text-orange-300 border-orange-500/30",
  commodity: "bg-yellow-500/15 text-yellow-300 border-yellow-500/30",
};
const ASSET_CLASS_LABEL: Record<string, string> = {
  us_stock: "US", uk_stock: "UK", crypto: "Crypto", commodity: "Cmdty",
};

// ── Component ──────────────────────────────────────────────────────────────────

export default function ScannerPanel() {
  const qc = useQueryClient();

  const { data: scan, isLoading } = useQuery({
    queryKey: ["last-scan"],
    queryFn: getLastScan,
    refetchInterval: 30_000,
  });

  const scanMut = useMutation({
    mutationFn: triggerScan,
    onSuccess: () => {
      toast.info("Auto-setup started", { description: "Strategies will update in ~35 seconds" });
      setTimeout(() => qc.invalidateQueries({ queryKey: ["last-scan", "strategies", "automation-status"] }), 35_000);
    },
  });

  const results: ScanResult[] = scan?.results ?? [];
  const scannedAt = scan?.scanned_at ? new Date(scan.scanned_at) : null;

  // Detect whether results are from new (universe) or old (Alpaca) scanner
  const isUniverseData = results.length > 0 && results[0].pct_change != null;

  return (
    <>
    <RefreshProgress isActive={scanMut.isPending} config="scanner_run" />
    <Card className="bg-gray-900 border-gray-800">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <Radar className="w-4 h-4 text-blue-400" />
            Auto-Setup Candidates
            {results.length > 0 && (
              <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/30 border text-[10px] px-2 py-0 ml-1">
                {results.length} assets
              </Badge>
            )}
          </CardTitle>
          <div className="flex items-center gap-3">
            {scannedAt && (
              <span className="text-[10px] text-gray-500">
                {scannedAt.toLocaleTimeString()}
              </span>
            )}
            <Button size="sm" variant="outline"
              onClick={() => scanMut.mutate()}
              disabled={scanMut.isPending}
              className="border-gray-700 text-gray-300 hover:text-white h-7 px-2 text-xs gap-1.5">
              <RefreshCw className={cn("w-3 h-3", scanMut.isPending && "animate-spin")} />
              {scanMut.isPending ? "Running…" : "Rescan Now"}
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent>
        {isLoading ? (
          <p className="text-xs text-gray-500 py-3">Loading…</p>
        ) : results.length === 0 ? (
          <div className="py-6 text-center space-y-2">
            <p className="text-sm text-gray-400">No scan results yet</p>
            <p className="text-xs text-gray-600">
              Runs automatically on startup · add API keys in Settings first
            </p>
            <Button size="sm" onClick={() => scanMut.mutate()} disabled={scanMut.isPending}
              className="mt-2 bg-blue-600 hover:bg-blue-700 text-xs gap-1.5">
              <Radar className="w-3.5 h-3.5" />
              {scanMut.isPending ? "Scanning…" : "Scan Now"}
            </Button>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] text-gray-500 uppercase tracking-wide border-b border-gray-800">
                  <th className="text-left py-2 pr-3 font-medium">#</th>
                  <th className="text-left py-2 pr-3 font-medium">Symbol</th>
                  <th className="text-left py-2 pr-3 font-medium">Type</th>
                  <th className="text-left py-2 pr-3 font-medium">Score</th>
                  <th className="text-right py-2 pr-3 font-medium">% Move</th>
                  <th className="text-right py-2 pr-3 font-medium">Vol/Avg</th>
                  <th className="text-right py-2 pr-3 font-medium">RSI</th>
                  <th className="text-left py-2 font-medium">Direction</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {results.map((r) => {
                  // Normalise across both data shapes
                  const pctChange  = r.pct_change   ?? r.momentum_pct ?? 0;
                  const volRatio   = r.vol_ratio     ?? r.volume_ratio ?? 0;
                  const rsi        = r.rsi           ?? 50;
                  const direction  = r.direction     ?? (r.bias?.includes("bull") ? "up" : "down");
                  const isUp       = direction === "up";

                  return (
                    <tr key={r.symbol} className="hover:bg-gray-800/40 transition-colors">
                      <td className="py-2.5 pr-3">
                        <span className={cn("font-bold text-[11px]",
                          r.rank <= 3 ? "text-yellow-400" : "text-gray-500"
                        )}>
                          {r.rank <= 3 ? ["🥇","🥈","🥉"][r.rank - 1] : `#${r.rank}`}
                        </span>
                      </td>

                      <td className="py-2.5 pr-3">
                        <div className="font-semibold text-white">{r.symbol}</div>
                        {r.asset_class && (
                          <Badge className={cn(
                            "text-[9px] border px-1 py-0 mt-0.5",
                            ASSET_CLASS_BADGE[r.asset_class] ?? "bg-gray-700 text-gray-400 border-gray-600"
                          )}>
                            {ASSET_CLASS_LABEL[r.asset_class] ?? r.asset_class}
                          </Badge>
                        )}
                      </td>

                      <td className="py-2.5 pr-3">
                        {r.trade_type ? (
                          <Badge className={cn(
                            "text-[9px] border px-1.5 py-0",
                            TRADE_TYPE_BADGE[r.trade_type] ?? "bg-gray-700 text-gray-400 border-gray-600"
                          )}>
                            {TRADE_TYPE_LABEL[r.trade_type] ?? r.trade_type}
                          </Badge>
                        ) : (
                          <span className="text-gray-600">—</span>
                        )}
                      </td>

                      <td className="py-2.5 pr-3">
                        <ScoreBar value={r.score} />
                      </td>

                      <td className="py-2.5 pr-3 text-right">
                        <span className={cn("font-medium", isUp ? "text-green-400" : "text-red-400")}>
                          {pctChange > 0 ? "+" : ""}{safe(pctChange, 2, "%")}
                        </span>
                      </td>

                      <td className="py-2.5 pr-3 text-right">
                        <span className={cn(
                          volRatio >= 2 ? "text-orange-400 font-semibold"
                          : volRatio >= 1.3 ? "text-yellow-400"
                          : "text-gray-400"
                        )}>
                          {safe(volRatio, 2, "×")}
                        </span>
                      </td>

                      <td className="py-2.5 pr-3 text-right">
                        <span className={cn(
                          rsi > 70 ? "text-red-400" : rsi < 30 ? "text-green-400" : "text-gray-300"
                        )}>
                          {safe(rsi, 0)}
                        </span>
                      </td>

                      <td className="py-2.5">
                        <div className="flex items-center gap-1">
                          {isUp
                            ? <TrendingUp className="w-3.5 h-3.5 text-green-400" />
                            : <TrendingDown className="w-3.5 h-3.5 text-red-400" />}
                          <span className={cn("text-[10px]", isUp ? "text-green-400" : "text-red-400")}>
                            {isUp ? "Up" : "Down"}
                          </span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="text-[10px] text-gray-600 mt-3">
              {isUniverseData
                ? "Source: universe screener · multi-asset · US + UK stocks, crypto, commodities"
                : "Source: Alpaca intraday scanner · crypto + US stocks"}
              {" · "}rescans every 4 hours
            </p>
          </div>
        )}
      </CardContent>
    </Card>
    </>
  );
}
