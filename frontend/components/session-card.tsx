"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getTodaySession, startSession, closeSession, getSessionHistory, getCalibration } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  BookOpen, PlayCircle, StopCircle, TrendingUp, TrendingDown,
  Target, BarChart2, Minus
} from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
  ScatterChart, Scatter, CartesianGrid, Line, LineChart,
} from "recharts";
import { toast } from "sonner";

const BIAS_COLORS: Record<string, string> = {
  bullish: "text-green-400",
  bearish: "text-red-400",
  neutral:  "text-gray-400",
};

function VerdictBadge({ verdict }: { verdict: string }) {
  const cls =
    verdict === "STRONG GO" ? "bg-green-500/20 text-green-300 border-green-500/30" :
    verdict === "GO"         ? "bg-blue-500/20 text-blue-300 border-blue-500/30" :
    verdict === "MARGINAL"   ? "bg-yellow-500/20 text-yellow-300 border-yellow-500/30" :
                               "bg-red-500/20 text-red-300 border-red-500/30";
  return <Badge className={cn("text-[10px] border font-semibold", cls)}>{verdict}</Badge>;
}

export function SessionCard() {
  const qc = useQueryClient();

  const { data: todayData, isLoading } = useQuery({
    queryKey: ["session-today"],
    queryFn: getTodaySession,
    refetchInterval: 15_000,
  });

  const { data: history } = useQuery({
    queryKey: ["session-history"],
    queryFn: () => getSessionHistory(14),
    refetchInterval: 60_000,
  });

  const { data: calibration } = useQuery({
    queryKey: ["calibration"],
    queryFn: getCalibration,
    refetchInterval: 60_000,
  });

  const startMut = useMutation({
    mutationFn: (bias: string) => startSession(bias),
    onSuccess: () => {
      toast.success("Session started");
      qc.invalidateQueries({ queryKey: ["session-today"] });
    },
    onError: () => toast.error("Failed to start session"),
  });

  const closeMut = useMutation({
    mutationFn: () => closeSession(),
    onSuccess: (d) => {
      const outcome = d.outcome;
      if (outcome === "WIN") toast.success("Session closed — WIN day!");
      else if (outcome === "LOSS") toast.error("Session closed — Loss day. Review the journal.");
      else toast.info("Session closed — Flat day.");
      qc.invalidateQueries({ queryKey: ["session-today"] });
      qc.invalidateQueries({ queryKey: ["session-history"] });
      qc.invalidateQueries({ queryKey: ["calibration"] });
    },
    onError: () => toast.error("Failed to close session"),
  });

  const session = todayData?.session;
  const historyList: any[] = history ?? [];

  const pnlColor = (v: number) =>
    v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-gray-400";

  // Build mini history chart (last 14 sessions)
  const histChart = [...historyList]
    .reverse()
    .map((s) => ({ date: s.date.slice(5), pnl: s.session_pnl ?? 0 }));

  // Calibration: predicted mid vs actual win rate
  const calBuckets: any[] = calibration?.buckets ?? [];

  return (
    <Card className="bg-gray-900 border-gray-800">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium text-gray-300 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BookOpen className="w-4 h-4" />
            Daily Session Journal
          </div>
          {session ? (
            <Badge className={cn(
              "text-[10px] border",
              session.status === "open"
                ? "bg-green-500/20 text-green-300 border-green-500/30"
                : "bg-gray-700 text-gray-400 border-gray-600"
            )}>
              {session.status === "open" ? "● LIVE" : "CLOSED"}
            </Badge>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* ── No session yet ──────────────────────────────────────────── */}
        {!session && !isLoading && (
          <div className="space-y-3">
            <p className="text-xs text-gray-500 text-center py-2">
              Start a session to track today's trades and build your learning dataset.
            </p>
            <div className="flex gap-2">
              {["bullish", "neutral", "bearish"].map(bias => (
                <Button
                  key={bias}
                  size="sm"
                  variant="outline"
                  className={cn(
                    "flex-1 text-xs border-gray-700 bg-gray-800 capitalize",
                    bias === "bullish" && "hover:border-green-500 hover:text-green-400",
                    bias === "neutral" && "hover:border-blue-500 hover:text-blue-400",
                    bias === "bearish" && "hover:border-red-500 hover:text-red-400",
                  )}
                  onClick={() => startMut.mutate(bias)}
                  disabled={startMut.isPending}
                >
                  {bias === "bullish" ? "↑" : bias === "bearish" ? "↓" : "–"} {bias}
                </Button>
              ))}
            </div>
          </div>
        )}

        {/* ── Active / closed session ─────────────────────────────────── */}
        {session && (
          <>
            {/* Stats grid */}
            <div className="grid grid-cols-4 gap-2">
              <div className="bg-gray-800 rounded-lg p-2.5 text-center">
                <p className="text-[10px] text-gray-500">P&L</p>
                <p className={cn("text-xs font-bold mt-0.5", pnlColor(session.session_pnl ?? 0))}>
                  {session.session_pnl != null
                    ? `${session.session_pnl >= 0 ? "+" : ""}$${Math.abs(session.session_pnl).toFixed(0)}`
                    : "$0"}
                </p>
              </div>
              <div className="bg-gray-800 rounded-lg p-2.5 text-center">
                <p className="text-[10px] text-gray-500">Trades</p>
                <p className="text-xs font-bold text-white mt-0.5">
                  {session.wins ?? 0}W / {session.losses ?? 0}L
                </p>
              </div>
              <div className="bg-gray-800 rounded-lg p-2.5 text-center">
                <p className="text-[10px] text-gray-500">Avg R</p>
                <p className={cn("text-xs font-bold mt-0.5", pnlColor(session.avg_r ?? 0))}>
                  {session.avg_r != null ? `${session.avg_r >= 0 ? "+" : ""}${session.avg_r}R` : "—"}
                </p>
              </div>
              <div className="bg-gray-800 rounded-lg p-2.5 text-center">
                <p className="text-[10px] text-gray-500">Bias</p>
                <p className={cn("text-xs font-bold mt-0.5 capitalize", BIAS_COLORS[session.pre_session_bias ?? "neutral"])}>
                  {session.pre_session_bias ?? "—"}
                </p>
              </div>
            </div>

            {/* High-probability accuracy */}
            {session.predicted_wins > 0 && (
              <div className="bg-gray-800/60 rounded-lg p-2.5">
                <div className="flex items-center justify-between">
                  <p className="text-[10px] text-gray-500">High-prob trades (≥60 score)</p>
                  <p className="text-xs font-semibold text-white">
                    {session.actual_wins_high_prob}/{session.predicted_wins} wins
                    {session.high_prob_accuracy != null && (
                      <span className={cn("ml-1.5", pnlColor(session.high_prob_accuracy - 60))}>
                        ({session.high_prob_accuracy}%)
                      </span>
                    )}
                  </p>
                </div>
              </div>
            )}

            {/* Close session button */}
            {session.status === "open" && (
              <Button
                size="sm"
                variant="outline"
                className="w-full text-xs border-gray-700 bg-gray-800 hover:border-red-500 hover:text-red-400"
                onClick={() => closeMut.mutate()}
                disabled={closeMut.isPending}
              >
                <StopCircle className="w-3 h-3 mr-1.5" />
                Close Today's Session
              </Button>
            )}
          </>
        )}

        {/* ── Session history mini-chart ──────────────────────────────── */}
        {histChart.length >= 3 && (
          <div>
            <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1.5">
              Last {histChart.length} sessions
            </p>
            <ResponsiveContainer width="100%" height={60}>
              <BarChart2 />
              {/* We use AreaChart for session P&L trend */}
            </ResponsiveContainer>
            <ResponsiveContainer width="100%" height={60}>
              <AreaChart data={histChart} margin={{ top: 2, bottom: 2, left: 0, right: 0 }}>
                <defs>
                  <linearGradient id="sessGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#22c55e" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <ReferenceLine y={0} stroke="#374151" />
                <XAxis dataKey="date" hide />
                <YAxis hide />
                <Tooltip
                  contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 6, fontSize: 11 }}
                  formatter={(v) => [`${Number(v) >= 0 ? "+" : ""}$${Number(v).toFixed(0)}`, "P&L"]}
                />
                <Area type="monotone" dataKey="pnl" stroke="#22c55e" strokeWidth={1.5}
                  fill="url(#sessGrad)" dot={{ r: 2, fill: "#22c55e" }} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* ── Probability calibration ─────────────────────────────────── */}
        {calBuckets.length >= 2 && (
          <div>
            <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1.5">
              Probability calibration
              <span className="ml-1 text-gray-600 normal-case">(predicted vs actual win %)</span>
            </p>
            <ResponsiveContainer width="100%" height={80}>
              <LineChart data={calBuckets} margin={{ top: 4, bottom: 4, left: 0, right: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis dataKey="predicted_mid" tick={{ fontSize: 9, fill: "#6b7280" }}
                  tickFormatter={(v) => `${v}%`} />
                <YAxis hide domain={[0, 100]} />
                <Tooltip
                  contentStyle={{ background: "#111827", border: "1px solid #374151", fontSize: 11 }}
                  formatter={(v, name) => [`${Number(v)}%`, name === "predicted_mid" ? "Predicted" : "Actual"]}
                />
                {/* Perfect calibration line */}
                <Line type="monotone" dataKey="predicted_mid" stroke="#374151"
                  strokeDasharray="4 2" dot={false} name="Predicted" />
                <Line type="monotone" dataKey="actual_win_rate" stroke="#3b82f6"
                  strokeWidth={2} dot={{ r: 3, fill: "#3b82f6" }} name="Actual" />
              </LineChart>
            </ResponsiveContainer>
            <p className="text-[9px] text-gray-600 text-center mt-1">
              Blue line → actual. Dashed → perfect calibration. Closer = better model.
            </p>
          </div>
        )}

        {/* ── Gap readiness ───────────────────────────────────────────── */}
        <div className="border-t border-gray-800 pt-3">
          <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-2">Day-trading readiness</p>
          <div className="flex flex-wrap gap-1.5">
            {[
              { label: "Sizing", done: true },
              { label: "SL Required", done: true },
              { label: "Loss Limit", done: true },
              { label: "R-multiples", done: true },
              { label: "Pre-trade %", done: true },
              { label: "Session Log", done: true },
              { label: "ATR Stops", done: false },
              { label: "EOD Flatten", done: false },
              { label: "Heat Cap", done: false },
              { label: "Intraday Data", done: false },
            ].map(({ label, done }) => (
              <span key={label} className={cn(
                "text-[10px] px-1.5 py-0.5 rounded border font-medium",
                done
                  ? "bg-green-500/10 text-green-400 border-green-500/20"
                  : "bg-gray-800 text-gray-500 border-gray-700"
              )}>
                {done ? "✓" : "○"} {label}
              </span>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
