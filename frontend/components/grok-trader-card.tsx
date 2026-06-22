"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  Zap, RefreshCw, ExternalLink, TrendingUp, TrendingDown,
  Loader2, AlertTriangle, Target, ShieldAlert, Clock, BarChart2,
} from "lucide-react";
import { toast } from "sonner";
import { RefreshProgress } from "@/components/refresh-progress";

const getSetups    = () => api.get("/grok-trader/setups").then(r => r.data);
const getHealth    = () => api.get("/grok-trader/health").then(r => r.data);
const triggerScan  = (ctx?: object) => api.post("/grok-trader/scan", ctx ?? {}).then(r => r.data);

// ── Style maps ───────────────────────────────────────────────────────────────
const BIAS_STYLE: Record<string, { label: string; color: string; bg: string }> = {
  bullish: { label: "Bullish",  color: "text-green-300",  bg: "bg-green-500/10 border-green-500/30" },
  neutral: { label: "Neutral",  color: "text-yellow-300", bg: "bg-yellow-500/10 border-yellow-500/30" },
  bearish: { label: "Bearish",  color: "text-red-300",    bg: "bg-red-500/10 border-red-500/30" },
};

const CATALYST_BADGE: Record<string, string> = {
  strong:   "bg-green-500/20 text-green-300 border-green-500/30",
  moderate: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  weak:     "bg-gray-500/20 text-gray-400 border-gray-600",
};

const SETUP_ICON: Record<string, string> = {
  gap_and_go:              "⚡",
  orb_breakout:            "🔲",
  vwap_reclaim:            "〰",
  momentum_continuation:   "🚀",
  reversal_at_key_level:   "↩",
  catalyst_breakout:       "💥",
};

const VERDICT_STYLE: Record<string, string> = {
  "STRONG GO":  "text-green-400 font-bold",
  "GO":         "text-green-300",
  "MARGINAL":   "text-yellow-400",
  "NO-GO":      "text-red-400",
};

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60)   return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function RRBar({ ratio }: { ratio: number }) {
  const pct = Math.min(100, (ratio / 5) * 100);
  const color = ratio >= 3 ? "bg-green-500" : ratio >= 2 ? "bg-yellow-500" : "bg-red-400";
  return (
    <div className="flex items-center gap-1.5">
      <div className="flex-1 h-1 bg-gray-800 rounded-full">
        <div className={cn("h-full rounded-full", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className={cn("text-[10px] font-semibold tabular-nums",
        ratio >= 3 ? "text-green-400" : ratio >= 2 ? "text-yellow-400" : "text-gray-400"
      )}>{ratio.toFixed(1)}R</span>
    </div>
  );
}

interface Setup {
  id: string;
  symbol: string;
  direction: "long" | "short";
  setup_type: string;
  catalyst: string;
  catalyst_strength: string;
  catalyst_source: string;
  entry_price: number;
  entry_trigger: string;
  stop_price: number;
  target_price: number;
  r_r_ratio: number;
  time_window_et: string;
  invalidation: string;
  rvol_estimate: number;
  confidence: number;
  sources: string[];
  pre_trade_probability?: number;
  pre_trade_verdict?: string;
  pre_trade_expected_r?: number;
}

function SetupCard({ setup }: { setup: Setup }) {
  const isLong = setup.direction === "long";
  const dirColor = isLong ? "text-green-400" : "text-red-400";
  const dirBg    = isLong ? "bg-green-500/10 border-green-500/20" : "bg-red-500/10 border-red-500/20";
  const icon     = SETUP_ICON[setup.setup_type] ?? "📊";
  const confPct  = Math.round(setup.confidence * 100);
  const verdict  = setup.pre_trade_verdict;

  return (
    <div className={cn("rounded-lg border p-3 space-y-2.5", dirBg)}>
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-base">{icon}</span>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-bold text-white">{setup.symbol}</span>
              <span className={cn("text-xs font-semibold uppercase", dirColor)}>
                {setup.direction}
              </span>
              {setup.time_window_et && (
                <span className="text-[10px] text-gray-500 flex items-center gap-0.5">
                  <Clock className="w-2.5 h-2.5" />{setup.time_window_et} ET
                </span>
              )}
            </div>
            <p className="text-[10px] text-gray-500 truncate">
              {setup.setup_type.replace(/_/g, " ")}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {verdict && (
            <span className={cn("text-[10px]", VERDICT_STYLE[verdict] ?? "text-gray-400")}>
              {verdict}
            </span>
          )}
          <Badge className={cn("text-[10px] border px-1.5 py-0", CATALYST_BADGE[setup.catalyst_strength])}>
            {setup.catalyst_strength}
          </Badge>
        </div>
      </div>

      {/* Catalyst */}
      <div className="text-[11px] text-gray-300 border-l-2 border-gray-600 pl-2 leading-relaxed">
        {setup.catalyst}
        {setup.catalyst_source && (
          <a href={setup.catalyst_source} target="_blank" rel="noreferrer"
            className="ml-1.5 text-blue-500 hover:text-blue-400 inline-flex items-center gap-0.5">
            source <ExternalLink className="w-2.5 h-2.5" />
          </a>
        )}
      </div>

      {/* Price levels */}
      <div className="grid grid-cols-3 gap-1.5 text-center">
        {[
          { label: "Entry",  value: setup.entry_price,  color: "text-gray-200" },
          { label: "Stop",   value: setup.stop_price,   color: "text-red-400" },
          { label: "Target", value: setup.target_price, color: "text-green-400" },
        ].map(({ label, value, color }) => (
          <div key={label} className="bg-gray-900/60 rounded px-2 py-1.5">
            <p className="text-[9px] text-gray-500 uppercase tracking-wide">{label}</p>
            <p className={cn("text-xs font-mono font-semibold", color)}>
              {value > 0 ? value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—"}
            </p>
          </div>
        ))}
      </div>

      {/* R:R bar + confidence + RVOL */}
      <div className="space-y-1">
        <RRBar ratio={setup.r_r_ratio} />
        <div className="flex items-center gap-3 text-[10px]">
          <span className="text-gray-500">Conf
            <span className={cn("ml-1 font-semibold",
              confPct >= 75 ? "text-green-400" : confPct >= 55 ? "text-yellow-400" : "text-gray-400"
            )}>{confPct}%</span>
          </span>
          {setup.rvol_estimate > 0 && (
            <span className="text-gray-500">RVOL
              <span className={cn("ml-1 font-semibold",
                setup.rvol_estimate >= 2 ? "text-green-400" : setup.rvol_estimate >= 1.5 ? "text-yellow-400" : "text-gray-400"
              )}>{setup.rvol_estimate.toFixed(1)}x</span>
            </span>
          )}
          {setup.pre_trade_probability != null && (
            <span className="text-gray-500">Score
              <span className={cn("ml-1 font-semibold",
                setup.pre_trade_probability >= 70 ? "text-green-400" :
                setup.pre_trade_probability >= 55 ? "text-yellow-400" : "text-red-400"
              )}>{setup.pre_trade_probability}/100</span>
            </span>
          )}
          {setup.pre_trade_expected_r != null && (
            <span className="ml-auto text-gray-500">
              E[R] <span className="text-purple-400 font-semibold">{setup.pre_trade_expected_r.toFixed(2)}</span>
            </span>
          )}
        </div>
      </div>

      {/* Entry trigger */}
      {setup.entry_trigger && (
        <p className="text-[10px] text-gray-500">
          <span className="text-gray-400">Trigger:</span> {setup.entry_trigger}
        </p>
      )}

      {/* Invalidation */}
      {setup.invalidation && (
        <p className="text-[10px] text-gray-500 flex items-start gap-1">
          <ShieldAlert className="w-3 h-3 shrink-0 text-orange-500 mt-0.5" />
          {setup.invalidation}
        </p>
      )}
    </div>
  );
}

export default function GrokTraderCard() {
  const qc = useQueryClient();

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["grok-setups"],
    queryFn: getSetups,
    refetchInterval: 5 * 60_000,
  });

  const { data: health } = useQuery({
    queryKey: ["grok-health"],
    queryFn: getHealth,
    refetchInterval: 30_000,
  });

  const scanMut = useMutation({
    mutationFn: () => triggerScan(),
    onMutate: () => toast.info("Grok is scanning markets… 15–40s"),
    onSuccess: (d) => {
      if (d.ok === false) {
        toast.error(`Grok scan failed: ${d.error ?? "unknown"}`);
      } else {
        const n = d.setups?.length ?? 0;
        toast.success(`Grok found ${n} setup${n !== 1 ? "s" : ""} — bias: ${d.market_bias}`);
      }
      qc.invalidateQueries({ queryKey: ["grok-setups"] });
      qc.invalidateQueries({ queryKey: ["grok-health"] });
    },
    onError: (e: any) => toast.error(`Scan failed: ${e?.message ?? e}`),
  });

  const available = data?.available;
  const scan = available ? data : null;
  const bias = BIAS_STYLE[scan?.market_bias ?? "neutral"] ?? BIAS_STYLE.neutral;
  const setups: Setup[] = scan?.setups ?? [];
  const avoid: { symbol: string; reason: string }[] = scan?.avoid_today ?? [];

  return (
    <>
    <RefreshProgress isActive={scanMut.isPending} config="grok_scan" />
    <Card className="bg-gray-900 border-gray-800">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium text-gray-300 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-yellow-400" />
            Grok Trader
            <span className="text-[10px] text-gray-600 font-normal">catalyst · live search</span>
            {health?.last_scan_utc && (
              <span className="text-[10px] text-gray-600 font-normal">
                · {timeAgo(health.last_scan_utc)}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[10px] border-gray-700 bg-gray-800 hover:border-yellow-500 hover:text-yellow-300"
              onClick={() => scanMut.mutate()}
              disabled={scanMut.isPending}
            >
              {scanMut.isPending
                ? <><Loader2 className="w-3 h-3 mr-1 animate-spin" />Scanning…</>
                : <><Zap className="w-3 h-3 mr-1" />Scan Now</>}
            </Button>
            <button onClick={() => refetch()} className="text-gray-600 hover:text-gray-300">
              <RefreshCw className={cn("w-3.5 h-3.5", isFetching && "animate-spin")} />
            </button>
          </div>
        </CardTitle>
      </CardHeader>

      <CardContent className="space-y-3">
        {/* Empty state */}
        {!available && !isLoading && (
          <div className="text-center py-6 space-y-3">
            <Zap className="w-8 h-8 text-gray-700 mx-auto" />
            <div className="space-y-1">
              <p className="text-xs text-gray-400 font-medium">No scan yet this session</p>
              <p className="text-[10px] text-gray-600">
                Grok scans automatically at 9:05 AM and 2:00 PM ET (Mon–Fri).
              </p>
              <p className="text-[10px] text-gray-600">
                Finds catalyst-driven setups with live web + X search.
              </p>
            </div>
            <Button
              size="sm"
              className="bg-yellow-600 hover:bg-yellow-500 text-white text-xs"
              onClick={() => scanMut.mutate()}
              disabled={scanMut.isPending}
            >
              {scanMut.isPending
                ? <><Loader2 className="w-3 h-3 mr-1.5 animate-spin" />Scanning…</>
                : <><Zap className="w-3 h-3 mr-1.5" />Scan Markets Now</>}
            </Button>
            {health?.last_error && (
              <p className="text-[10px] text-red-400 bg-red-500/10 rounded px-2 py-1">
                {health.last_error}
              </p>
            )}
          </div>
        )}

        {/* Market bias + overview */}
        {scan && (
          <>
            <div className="flex items-center gap-2">
              <Badge className={cn("text-xs border font-semibold", bias.bg, bias.color)}>
                {scan.market_bias === "bullish"
                  ? <TrendingUp className="w-3 h-3 mr-1 inline" />
                  : scan.market_bias === "bearish"
                  ? <TrendingDown className="w-3 h-3 mr-1 inline" />
                  : null}
                {bias.label}
              </Badge>
              <span className="text-[10px] text-gray-500">
                {setups.length} setup{setups.length !== 1 ? "s" : ""} found
              </span>
              <span className="ml-auto text-[10px] text-gray-600">
                {health?.last_scan_utc ? timeAgo(health.last_scan_utc) : ""}
              </span>
            </div>

            {scan.session_overview && (
              <p className="text-[11px] text-gray-400 border-l-2 border-gray-700 pl-2.5 leading-relaxed">
                {scan.session_overview}
              </p>
            )}

            {/* Setup cards */}
            {setups.length === 0 ? (
              <div className="text-center py-3">
                <BarChart2 className="w-6 h-6 text-gray-700 mx-auto mb-1" />
                <p className="text-xs text-gray-500">No high-conviction setups found today.</p>
                <p className="text-[10px] text-gray-600">
                  Grok only returns setups with ≥2R, a verified catalyst, and RVOL &gt; 1.5×.
                </p>
              </div>
            ) : (
              <div className="space-y-2.5">
                {setups.map((s) => <SetupCard key={s.id} setup={s} />)}
              </div>
            )}

            {/* Avoid today */}
            {avoid.length > 0 && (
              <div className="border-t border-gray-800 pt-2">
                <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1.5 flex items-center gap-1">
                  <AlertTriangle className="w-3 h-3 text-orange-500" />
                  Avoid today
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {avoid.map((a, i) => (
                    <div key={i} className="bg-gray-800 rounded px-2 py-1" title={a.reason}>
                      <span className="text-[11px] text-gray-300 font-medium">{a.symbol}</span>
                      <span className="text-[10px] text-gray-500 ml-1.5">{a.reason.slice(0, 50)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Sources footer */}
            {scan.sources_searched?.length > 0 && (
              <div className="flex flex-wrap gap-1 pt-1 border-t border-gray-800">
                <span className="text-[10px] text-gray-600">Searched:</span>
                {scan.sources_searched.slice(0, 3).map((url: string, i: number) => {
                  let host = url;
                  try { host = new URL(url).hostname.replace("www.", ""); } catch {}
                  return (
                    <a key={i} href={url} target="_blank" rel="noreferrer"
                      className="text-[10px] text-blue-500 hover:text-blue-400 flex items-center gap-0.5">
                      {host} <ExternalLink className="w-2 h-2" />
                    </a>
                  );
                })}
                {scan.sources_searched.length > 3 && (
                  <span className="text-[10px] text-gray-600">+{scan.sources_searched.length - 3}</span>
                )}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
    </>
  );
}
