"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Brain, RefreshCw, ExternalLink, AlertTriangle, TrendingUp, TrendingDown, Minus, Play, Loader2 } from "lucide-react";
import { toast } from "sonner";

const getLatestSA = () => api.get("/situational-awareness/latest").then(r => r.data);
const getSAHealth = () => api.get("/situational-awareness/health").then(r => r.data);
const triggerSA   = () => api.post("/situational-awareness", {}).then(r => r.data);

const REGIME_STYLES: Record<string, { label: string; color: string; bg: string }> = {
  strong_uptrend:   { label: "Strong Uptrend",  color: "text-green-300",  bg: "bg-green-500/10 border-green-500/20" },
  uptrend:          { label: "Uptrend",          color: "text-green-400",  bg: "bg-green-500/10 border-green-500/20" },
  range:            { label: "Range",            color: "text-yellow-300", bg: "bg-yellow-500/10 border-yellow-500/20" },
  downtrend:        { label: "Downtrend",        color: "text-red-400",    bg: "bg-red-500/10 border-red-500/20" },
  strong_downtrend: { label: "Strong Downtrend", color: "text-red-300",    bg: "bg-red-500/10 border-red-500/20" },
  high_volatility:  { label: "High Volatility",  color: "text-orange-300", bg: "bg-orange-500/10 border-orange-500/20" },
  unknown:          { label: "Unknown",          color: "text-gray-400",   bg: "bg-gray-800 border-gray-700" },
};

const POSTURE_STYLES: Record<string, { icon: React.ReactNode; color: string }> = {
  risk_on:  { icon: <TrendingUp  className="w-3.5 h-3.5" />, color: "text-green-400" },
  neutral:  { icon: <Minus       className="w-3.5 h-3.5" />, color: "text-gray-400"  },
  risk_off: { icon: <TrendingDown className="w-3.5 h-3.5"/>, color: "text-red-400"   },
};

const BIAS_DOT: Record<string, string> = {
  bullish: "bg-green-500",
  neutral: "bg-gray-500",
  bearish: "bg-red-500",
};

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60)   return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

export default function SituationalAwarenessCard() {
  const qc = useQueryClient();

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["sa-latest"],
    queryFn: getLatestSA,
    refetchInterval: 60_000,
  });

  const { data: health } = useQuery({
    queryKey: ["sa-health"],
    queryFn: getSAHealth,
    refetchInterval: 30_000,
  });

  const runNowMut = useMutation({
    mutationFn: triggerSA,
    onMutate: () => toast.info("Asking Grok… this takes 10–30s"),
    onSuccess: (d) => {
      if (d.ok === false) {
        toast.error(`Grok SA failed: ${d.error ?? "unknown error"}`);
      } else {
        toast.success(`Grok SA done — ${d.market_regime} / ${d.risk_posture}`);
      }
      qc.invalidateQueries({ queryKey: ["sa-latest"] });
      qc.invalidateQueries({ queryKey: ["sa-health"] });
    },
    onError: (e: any) => toast.error(`SA request failed: ${e?.message ?? e}`),
  });

  const available = data?.available;
  const a = available ? data : null;
  const regime = REGIME_STYLES[a?.market_regime ?? "unknown"] ?? REGIME_STYLES.unknown;
  const posture = POSTURE_STYLES[a?.risk_posture ?? "neutral"] ?? POSTURE_STYLES.neutral;
  const perSymbol: Record<string, { bias: string; sentiment: number; note: string; catalysts: string[] }> =
    a?.per_symbol ?? {};
  const eventRisk: { event: string; when_utc: string; impact: string; affected: string[] }[] =
    a?.event_risk ?? [];
  const mult = a?.recommended_risk_multiplier ?? 1.0;
  const enforce = health?.enforce ?? false;

  return (
    <Card className={cn("border", a?.risk_posture === "risk_off"
      ? "bg-red-500/5 border-red-500/20"
      : "bg-gray-900 border-gray-800"
    )}>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium text-gray-300 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-purple-400" />
            Grok Situational Awareness
            {health?.last_success_utc && (
              <span className="text-[10px] text-gray-600 font-normal">
                · {timeAgo(health.last_success_utc)}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[10px] border-gray-700 bg-gray-800 hover:border-purple-500 hover:text-purple-300"
              onClick={() => runNowMut.mutate()}
              disabled={runNowMut.isPending}
              title="Trigger Grok assessment now"
            >
              {runNowMut.isPending
                ? <Loader2 className="w-3 h-3 animate-spin" />
                : <Play className="w-3 h-3" />}
              <span className="ml-1">{runNowMut.isPending ? "Asking Grok…" : "Run Now"}</span>
            </Button>
            <button
              onClick={() => refetch()}
              className="text-gray-600 hover:text-gray-300 transition-colors"
              title="Refresh cached result"
            >
              <RefreshCw className={cn("w-3.5 h-3.5", isFetching && "animate-spin")} />
            </button>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">

        {/* ── Not available yet ───────────────────────────────────────── */}
        {!available && !isLoading && (
          <div className="text-center py-4 space-y-3">
            <Brain className="w-8 h-8 text-gray-700 mx-auto" />
            <div className="space-y-1">
              <p className="text-xs text-gray-400 font-medium">No assessment yet this session</p>
              <p className="text-[10px] text-gray-600">
                Runs automatically every 20 min once{" "}
                <code className="text-purple-400 bg-purple-500/10 px-1 rounded">XAI_API_KEY</code>{" "}
                is set.
              </p>
            </div>
            <Button
              size="sm"
              className="bg-purple-600 hover:bg-purple-500 text-white text-xs"
              onClick={() => runNowMut.mutate()}
              disabled={runNowMut.isPending}
            >
              {runNowMut.isPending
                ? <><Loader2 className="w-3 h-3 mr-1.5 animate-spin" /> Asking Grok…</>
                : <><Play className="w-3 h-3 mr-1.5" /> Ask Grok Now</>}
            </Button>
            {health?.last_error && (
              <p className="text-[10px] text-red-400 bg-red-500/10 rounded px-2 py-1">
                Last error: {health.last_error}
              </p>
            )}
          </div>
        )}

        {/* ── Regime + posture row ─────────────────────────────────────── */}
        {a && (
          <>
            <div className="flex items-center gap-2 flex-wrap">
              <Badge className={cn("text-xs border font-semibold", regime.bg, regime.color)}>
                {regime.label}
              </Badge>
              <div className={cn("flex items-center gap-1 text-xs font-medium", posture.color)}>
                {posture.icon}
                {a.risk_posture.replace("_", " ")}
              </div>
              <div className="ml-auto text-[10px] text-gray-500">
                conf {Math.round((a.confidence ?? 0) * 100)}%
              </div>
            </div>

            {/* Risk multiplier bar */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] text-gray-500 uppercase tracking-wide">
                  Risk multiplier
                  {enforce
                    ? <span className="ml-1 text-orange-400">(enforced)</span>
                    : <span className="ml-1 text-gray-600">(advisory)</span>}
                </span>
                <span className={cn("text-xs font-semibold tabular-nums",
                  mult < 0.6 ? "text-red-400" : mult < 0.85 ? "text-yellow-400" : "text-gray-300"
                )}>
                  ×{mult.toFixed(2)}
                  {!enforce && <span className="text-gray-600 font-normal"> → ×1.00 applied</span>}
                </span>
              </div>
              <div className="w-full bg-gray-800 rounded-full h-1.5">
                <div
                  className={cn("h-full rounded-full transition-all",
                    mult < 0.6 ? "bg-red-500" : mult < 0.85 ? "bg-yellow-500" : "bg-green-500"
                  )}
                  style={{ width: `${mult * 100}%` }}
                />
              </div>
            </div>

            {/* Macro summary */}
            {a.macro_summary && (
              <p className="text-[11px] text-gray-400 leading-relaxed border-l-2 border-gray-700 pl-2.5">
                {a.macro_summary}
              </p>
            )}

            {/* Event risk */}
            {eventRisk.length > 0 && (
              <div className="space-y-1">
                <p className="text-[10px] text-gray-500 uppercase tracking-wide">Event risk</p>
                {eventRisk.slice(0, 4).map((ev, i) => (
                  <div key={i} className="flex items-center gap-2 text-[11px]">
                    <AlertTriangle className={cn("w-3 h-3 shrink-0",
                      ev.impact === "high" ? "text-red-400" :
                      ev.impact === "medium" ? "text-yellow-400" : "text-gray-500"
                    )} />
                    <span className="text-gray-300">{ev.event}</span>
                    {ev.when_utc && ev.when_utc !== "active" && (
                      <span className="text-gray-600 ml-auto shrink-0">
                        {new Date(ev.when_utc).toLocaleDateString([], { month: "short", day: "numeric" })}
                      </span>
                    )}
                    {ev.when_utc === "active" && (
                      <span className="text-red-400 ml-auto text-[10px] font-semibold shrink-0">ACTIVE</span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Per-symbol bias grid */}
            {Object.keys(perSymbol).length > 0 && (
              <div>
                <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1.5">Per-symbol</p>
                <div className="grid grid-cols-2 gap-1.5">
                  {Object.entries(perSymbol).slice(0, 8).map(([sym, info]) => (
                    <div key={sym}
                      className="flex items-center gap-1.5 bg-gray-800 rounded px-2 py-1.5 min-w-0">
                      <span className={cn("w-1.5 h-1.5 rounded-full shrink-0",
                        BIAS_DOT[info.bias] ?? "bg-gray-500")} />
                      <span className="text-[11px] font-medium text-white truncate">{sym}</span>
                      <span className={cn("text-[10px] ml-auto shrink-0",
                        info.bias === "bullish" ? "text-green-400" :
                        info.bias === "bearish" ? "text-red-400" : "text-gray-500"
                      )}>
                        {info.sentiment != null
                          ? `${info.sentiment >= 0 ? "+" : ""}${(info.sentiment * 100).toFixed(0)}%`
                          : info.bias}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Sources */}
            {a.sources?.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-1 border-t border-gray-800">
                <span className="text-[10px] text-gray-600">Sources:</span>
                {a.sources.slice(0, 4).map((url: string, i: number) => {
                  let host = url;
                  try { host = new URL(url).hostname.replace("www.", ""); } catch {}
                  return (
                    <a key={i} href={url} target="_blank" rel="noreferrer"
                      className="flex items-center gap-0.5 text-[10px] text-blue-500 hover:text-blue-400">
                      {host} <ExternalLink className="w-2.5 h-2.5" />
                    </a>
                  );
                })}
                {a.sources.length > 4 && (
                  <span className="text-[10px] text-gray-600">+{a.sources.length - 4} more</span>
                )}
              </div>
            )}

            {/* Caveats */}
            {a.caveats && (
              <p className="text-[10px] text-gray-600 italic">{a.caveats}</p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
