"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getPortfolioSnapshot, getTradeStats, getStrategies, getWatchlist,
  getActiveBroker, getEquityCurve, getIntradayCurve, getSettings,
  getAutomationStatus, getLastScan, getLastAutoPilot, closeAllPositions,
} from "@/lib/api";
import AutomationPanel    from "@/components/automation-panel";
import ScannerPanel       from "@/components/scanner-panel";
import AutoPilotPanel     from "@/components/autopilot-panel";
import EventsFeed         from "@/components/events-feed";
import SituationalAwarenessCard from "@/components/situational-awareness-card";
import { SessionCard }    from "@/components/session-card";
import GrokTraderCard     from "@/components/grok-trader-card";
import GrokChatWindow     from "@/components/grok-chat-window";
import AutoTradePanel     from "@/components/auto-trade-panel";
import NewsIntelPanel     from "@/components/news-intel-panel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge }   from "@/components/ui/badge";
import { Button }  from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  TrendingUp, TrendingDown, DollarSign, Activity, BarChart2, Zap,
  AlertCircle, ShieldAlert, CalendarClock, Clock, Radio, ScanSearch,
  Bot, CircleCheck, CircleX, Siren, ChevronDown, ChevronUp,
  Wallet, Target, Cpu,
} from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, ReferenceLine,
} from "recharts";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6"];

// ─── Section header ───────────────────────────────────────────────────────────
function SectionLabel({ label, accent = "blue" }: { label: string; accent?: "blue" | "green" | "purple" | "yellow" | "gray" }) {
  const border = {
    blue:   "border-blue-500",
    green:  "border-green-500",
    purple: "border-purple-500",
    yellow: "border-yellow-500",
    gray:   "border-gray-600",
  }[accent];
  return (
    <div className={cn("flex items-center gap-3 border-l-2 pl-3", border)}>
      <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-500">{label}</span>
    </div>
  );
}

// ─── Stat card ────────────────────────────────────────────────────────────────
function StatCard({
  title, value, sub, icon: Icon, positive, neutral, warn,
}: {
  title: string; value: string; sub?: string;
  icon: React.ElementType; positive?: boolean; neutral?: boolean; warn?: boolean;
}) {
  const color = neutral ? "text-gray-400" : warn ? "text-yellow-400" : positive ? "text-green-400" : "text-red-400";
  const bg    = neutral ? "bg-gray-800/60" : warn ? "bg-yellow-500/10" : positive ? "bg-green-500/10" : "bg-red-500/10";
  const glow  = neutral ? "" : warn ? "shadow-yellow-500/5" : positive ? "shadow-green-500/5" : "shadow-red-500/5";
  return (
    <Card className={cn("bg-gray-900 border-gray-800 hover:border-gray-700 transition-colors shadow-lg", glow)}>
      <CardContent className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] text-gray-500 font-semibold uppercase tracking-widest truncate">{title}</p>
            <p className="text-[1.6rem] font-bold text-white mt-1.5 leading-none">{value}</p>
            {sub && <p className={cn("text-xs mt-1.5 leading-snug", neutral ? "text-gray-500" : color)}>{sub}</p>}
          </div>
          <div className={cn("p-2.5 rounded-xl shrink-0 mt-0.5", bg)}>
            <Icon className={cn("w-5 h-5", color)} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Collapsible engine panel ─────────────────────────────────────────────────
function EngineSection({ tab, setTab, children }: {
  tab: "autopilot" | "scanner" | "automation";
  setTab: (t: "autopilot" | "scanner" | "automation") => void;
  children: React.ReactNode;
}) {
  const tabs = [
    { id: "autopilot",  label: "AutoPilot",  icon: Bot },
    { id: "scanner",    label: "Scanner",    icon: ScanSearch },
    { id: "automation", label: "Automation", icon: Cpu },
  ] as const;
  return (
    <Card className="bg-gray-900 border-gray-800">
      <div className="flex items-center gap-1 px-4 pt-3 border-b border-gray-800 pb-0">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-t-lg transition-colors -mb-px border border-transparent",
              tab === t.id
                ? "bg-gray-950 border-gray-700 border-b-gray-950 text-white"
                : "text-gray-500 hover:text-gray-300 hover:bg-gray-800/50"
            )}
          >
            <t.icon className="w-3.5 h-3.5" /> {t.label}
          </button>
        ))}
      </div>
      <div className="p-0">{children}</div>
    </Card>
  );
}

const DEFAULT_WATCHLIST = "AAPL,TSLA,NVDA,SPY,QQQ";

export default function Dashboard() {
  const [chartView,     setChartView]     = useState<"intraday" | "30d">("intraday");
  const [flattenConfirm, setFlattenConfirm] = useState(false);
  const [engineTab,     setEngineTab]     = useState<"autopilot" | "scanner" | "automation">("autopilot");
  const qc = useQueryClient();

  // ── Queries ─────────────────────────────────────────────────────────────────
  const { data: portfolio }       = useQuery({ queryKey: ["portfolio"],        queryFn: getPortfolioSnapshot,                  retry: false,  refetchInterval: 10_000   });
  const { data: stats }           = useQuery({ queryKey: ["trade-stats"],      queryFn: getTradeStats,                         refetchInterval: 15_000   });
  const { data: strategies }      = useQuery({ queryKey: ["strategies"],       queryFn: getStrategies,                         refetchInterval: 30_000   });
  const { data: settings }        = useQuery({ queryKey: ["settings"],         queryFn: getSettings,                           refetchInterval: 300_000  });
  const watchlistSymbols = (settings as { watchlist_symbols?: string } | undefined)?.watchlist_symbols || DEFAULT_WATCHLIST;
  const { data: watchlist }       = useQuery({ queryKey: ["watchlist", watchlistSymbols], queryFn: () => getWatchlist(watchlistSymbols), refetchInterval: 5_000, enabled: !!watchlistSymbols });
  const { data: brokerInfo }      = useQuery({ queryKey: ["broker"],           queryFn: getActiveBroker,                       refetchInterval: 30_000   });
  const { data: equityCurveRaw }  = useQuery({ queryKey: ["equity-curve"],     queryFn: () => getEquityCurve(30),              refetchInterval: 120_000  });
  const { data: intradayRaw }     = useQuery({ queryKey: ["intraday-curve"],   queryFn: getIntradayCurve,                      refetchInterval: 15_000   });
  const { data: automationStatus }= useQuery({ queryKey: ["automation-status"],queryFn: getAutomationStatus, retry: false,     refetchInterval: 15_000   });
  const { data: lastScan }        = useQuery({ queryKey: ["last-scan"],        queryFn: getLastScan,         retry: false,     refetchInterval: 60_000   });
  const { data: lastAutoPilot }   = useQuery({ queryKey: ["last-autopilot"],   queryFn: getLastAutoPilot,    retry: false,     refetchInterval: 120_000  });

  // ── Flatten mutation ─────────────────────────────────────────────────────────
  const flattenMut = useMutation({
    mutationFn: closeAllPositions,
    onSuccess: (data) => {
      toast.success(`Flattened ${data.positions_closed} position(s) — ${data.strategies_paused} strategies paused`);
      setFlattenConfirm(false);
      qc.invalidateQueries();
    },
    onError: () => { toast.error("Flatten failed — check broker connection"); setFlattenConfirm(false); },
  });

  // ── Derived values ────────────────────────────────────────────────────────────
  const activeStrategies = (strategies || []).filter((s: { status: string }) => s.status === "active");
  const keysConfigured   = brokerInfo?.broker && brokerInfo.broker !== "none";
  const connectionError  = portfolio?.connection_error === true;
  const hasPortfolio     = !!portfolio && portfolio.portfolio_value != null;
  const activeBroker     = portfolio?.active_broker ?? brokerInfo?.broker;

  const alpacaSummary  = portfolio?.brokers?.alpaca;
  const binanceSummary = portfolio?.brokers?.binance;
  const bothBrokers    = !!(alpacaSummary && binanceSummary);
  const displayValue   = hasPortfolio ? portfolio.portfolio_value : null;
  const displayCash    = hasPortfolio ? portfolio.cash : null;

  const unrealizedPnl  = hasPortfolio ? (portfolio.total_unrealized_pnl ?? 0) : 0;
  const realizedPnl    = hasPortfolio ? (portfolio.total_realized_pnl ?? 0) : 0;
  const totalPnl       = unrealizedPnl + realizedPnl;
  const knownStartEquity = bothBrokers
    ? 100_000 + Number(binanceSummary!.portfolio_value ?? 309_000)
    : alpacaSummary ? 100_000 : null;
  const returnPct = hasPortfolio && knownStartEquity
    ? ((Number(displayValue) - knownStartEquity) / knownStartEquity) * 100
    : null;

  const alpacaStartEquity = 100_000;
  const hasCurveData      = equityCurveRaw && (equityCurveRaw as { day: string; pnl: number }[]).length > 0;
  const equityCurveData   = hasCurveData
    ? (equityCurveRaw as { day: string; pnl: number }[]).map((p, i) => ({
        day: `Day ${i + 1}`,
        value: alpacaStartEquity + p.pnl,
      }))
    : null;

  type IntradayPoint = { time: string; pnl: number; label?: string };
  const intradayPoints: IntradayPoint[] = (intradayRaw as { points?: IntradayPoint[] } | undefined)?.points ?? [];
  const todayRealized: number  = (intradayRaw as { today_realized?: number }  | undefined)?.today_realized  ?? 0;
  const todayTrades:   number  = (intradayRaw as { today_total_trades?: number } | undefined)?.today_total_trades ?? 0;
  const hasIntradayData = intradayPoints.length > 1;

  const positionPie: { name: string; value: number }[] = (portfolio?.positions || []).map(
    (p: { symbol: string; market_value: number }) => ({ name: p.symbol, value: Math.abs(p.market_value) })
  );

  const openPositions: {
    symbol: string; qty: number; side: string;
    market_value: number; unrealized_pl: number; avg_entry_price: number; current_price: number;
  }[] = portfolio?.positions || [];

  const dailyToday = stats?.daily_trades_today ?? 0;
  const dailyLimit = stats?.daily_trades_limit  ?? 20;
  const dailyPct   = Math.round((dailyToday / dailyLimit) * 100);
  const dailyWarn  = dailyPct >= 80;

  const dailyPnl          = stats?.daily_pnl_today    ?? 0;
  const dailyLossLimitPct = stats?.daily_loss_limit_pct ?? 3.0;
  const dailyLossLimitAmt = 100_000 * (dailyLossLimitPct / 100);
  const dailyLossPct      = dailyPnl < 0 ? Math.min(Math.abs(dailyPnl) / dailyLossLimitAmt * 100, 100) : 0;
  const dailyLossWarn     = dailyLossPct >= 80;
  const dailyLossHit      = dailyPnl <= -dailyLossLimitAmt;

  const marketStatus = (() => {
    const now = new Date();
    const inMins = (tz: string) => {
      const s = now.toLocaleString("en-US", { timeZone: tz });
      const d = new Date(s);
      return { mins: d.getHours() * 60 + d.getMinutes(), day: d.getDay() };
    };
    const weekday = (day: number) => day >= 1 && day <= 5;
    const lse  = inMins("Europe/London");
    const nyse = inMins("America/New_York");
    const lseOpen   = weekday(lse.day)  && lse.mins  >= 480 && lse.mins  < 1010;
    const nyseOpen  = weekday(nyse.day) && nyse.mins >= 570 && nyse.mins < 960;
    const forexOpen = weekday(now.getDay() === 0 ? 0 : now.getDay()) &&
                      !(now.getDay() === 6 || (now.getDay() === 0 && now.getUTCHours() < 22));
    const cryptoOpen = !(now.getDay() === 6 && now.getUTCHours() >= 22) &&
                       !(now.getDay() === 0 && now.getUTCHours() < 21);
    return { lseOpen, nyseOpen, forexOpen, cryptoOpen, anyOpen: lseOpen || nyseOpen || forexOpen || cryptoOpen };
  })();

  const autopilotLeaderboard: { dsr?: number; deflated_sharpe_ratio?: number }[] =
    (lastAutoPilot as { leaderboard?: { dsr?: number; deflated_sharpe_ratio?: number }[] } | undefined)?.leaderboard ?? [];
  const bestDsr = autopilotLeaderboard.length > 0
    ? Math.max(...autopilotLeaderboard.map(e => (e.dsr ?? e.deflated_sharpe_ratio ?? 0)))
    : null;

  const lastScanTime: string | null = (lastScan as { scanned_at?: string } | undefined)?.scanned_at ?? null;
  const lastScanMinsAgo = lastScanTime
    ? Math.round((Date.now() - new Date(lastScanTime).getTime()) / 60_000)
    : null;

  const expectancy     = stats?.expectancy     ?? null;
  const profitFactor   = stats?.profit_factor  ?? null;
  const maxDrawdownPct = stats?.max_drawdown_pct ?? null;
  const expectancyPass = expectancy != null && expectancy > 0;
  const pfPass         = profitFactor != null && profitFactor >= 1.5;
  const ddPass         = maxDrawdownPct != null && maxDrawdownPct < 10;

  return (
    <div className="p-5 space-y-6 min-h-screen">

      {/* ── HEADER ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">Dashboard</h1>
          <p className="text-xs text-gray-500 mt-0.5">
            {new Date().toLocaleDateString("en-GB", { weekday: "long", year: "numeric", month: "long", day: "numeric" })}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap justify-end">
          {/* Market session badges */}
          <div className="flex items-center gap-1.5 bg-gray-900 border border-gray-800 rounded-lg px-3 py-1.5">
            {([
              { label: "LSE",    open: marketStatus.lseOpen   },
              { label: "NYSE",   open: marketStatus.nyseOpen  },
              { label: "Forex",  open: marketStatus.forexOpen },
              { label: "Crypto", open: marketStatus.cryptoOpen },
            ] as { label: string; open: boolean }[]).map(({ label, open }, i) => (
              <div key={label} className="flex items-center gap-1">
                {i > 0 && <span className="text-gray-700 mx-0.5">·</span>}
                <span className={cn("inline-block w-1.5 h-1.5 rounded-full", open ? "bg-green-400 animate-pulse" : "bg-gray-600")} />
                <span className={cn("text-[11px] font-medium", open ? "text-gray-300" : "text-gray-600")}>{label}</span>
              </div>
            ))}
          </div>

          {/* Broker badge */}
          {activeBroker === "binance_testnet" && (
            <Badge className="text-xs px-2.5 py-1 border bg-yellow-500/10 text-yellow-300 border-yellow-500/20">
              Binance Testnet
            </Badge>
          )}
          {activeBroker?.startsWith("alpaca") && (
            <Badge className="text-xs px-2.5 py-1 border bg-blue-500/10 text-blue-300 border-blue-500/20">
              Alpaca Paper
            </Badge>
          )}

          {/* Connection pill */}
          <div className={cn(
            "flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border font-medium",
            hasPortfolio      ? "bg-green-500/10 text-green-400 border-green-500/20"
            : connectionError ? "bg-red-500/10 text-red-400 border-red-500/20"
            : keysConfigured  ? "bg-blue-500/10 text-blue-400 border-blue-500/20"
            : "bg-yellow-500/10 text-yellow-400 border-yellow-500/20"
          )}>
            <span className={cn("w-1.5 h-1.5 rounded-full",
              hasPortfolio ? "bg-green-400 animate-pulse" : connectionError ? "bg-red-400" : "bg-yellow-400"
            )} />
            {hasPortfolio ? "Connected" : connectionError ? "Error" : keysConfigured ? "Connecting…" : "No API Keys"}
          </div>

          {/* Scheduler pulse */}
          {automationStatus?.running && (
            <div className="flex items-center gap-1.5 text-xs text-gray-400 bg-gray-900 border border-gray-800 rounded-lg px-3 py-1.5">
              <Radio className="w-3 h-3 text-blue-400 animate-pulse" />
              <span>{(automationStatus.jobs as unknown[])?.length ?? 0} jobs running</span>
            </div>
          )}
        </div>
      </div>

      {/* ── ALERTS ──────────────────────────────────────────────────────────── */}
      {!keysConfigured && (
        <div className="flex items-center gap-3 bg-yellow-500/5 border border-yellow-500/20 rounded-xl px-4 py-3">
          <AlertCircle className="w-4 h-4 text-yellow-400 shrink-0" />
          <p className="text-sm text-yellow-300">
            Add Binance Testnet or Alpaca paper trading keys in{" "}
            <a href="/settings" className="underline font-medium hover:text-yellow-200">Settings</a> to see live portfolio data.
          </p>
        </div>
      )}
      {keysConfigured && connectionError && (
        <div className="flex items-center gap-3 bg-red-500/5 border border-red-500/20 rounded-xl px-4 py-3">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
          <p className="text-sm text-red-300">
            Could not connect to {brokerInfo?.broker === "binance_testnet" ? "Binance Testnet" : "Alpaca"}.{" "}
            {portfolio?.error && <span className="text-red-400 font-mono text-xs">{portfolio.error}</span>}
          </p>
        </div>
      )}
      {dailyLossHit && (
        <div className="flex items-center gap-3 bg-red-500/10 border border-red-500/30 rounded-xl px-4 py-3">
          <Siren className="w-4 h-4 text-red-400 shrink-0 animate-pulse" />
          <p className="text-sm text-red-300 font-medium">
            Daily loss limit hit — all strategies paused. Resets UTC midnight.
          </p>
        </div>
      )}

      {/* ── PORTFOLIO OVERVIEW ──────────────────────────────────────────────── */}
      <div className="space-y-3">
        <SectionLabel label="Portfolio Overview" accent="blue" />

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatCard
            title={bothBrokers ? "Combined Portfolio" : "Portfolio Value"}
            value={hasPortfolio ? `$${Number(displayValue).toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "$—"}
            sub={hasPortfolio
              ? bothBrokers
                ? `Alpaca $${Number(alpacaSummary!.portfolio_value).toLocaleString("en-US", { maximumFractionDigits: 0 })} · Binance $${Number(binanceSummary!.portfolio_value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`
                : `Cash: $${Number(displayCash).toLocaleString("en-US", { minimumFractionDigits: 2 })}`
              : "Connect a broker to see live data"}
            icon={Wallet} positive neutral={!hasPortfolio}
          />
          <StatCard
            title="Total P&L"
            value={hasPortfolio ? `${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}` : "$—"}
            sub={hasPortfolio
              ? `${returnPct != null ? `${returnPct >= 0 ? "+" : ""}${returnPct.toFixed(2)}% return · ` : ""}Unrlzd: ${unrealizedPnl >= 0 ? "+" : ""}$${unrealizedPnl.toFixed(2)}`
              : undefined}
            icon={hasPortfolio && totalPnl >= 0 ? TrendingUp : TrendingDown}
            positive={hasPortfolio && totalPnl >= 0}
            neutral={!hasPortfolio}
          />
          <StatCard
            title="Expectancy"
            value={expectancy != null ? `${expectancy >= 0 ? "+" : ""}$${expectancy.toFixed(2)}` : "$—"}
            sub={expectancy != null
              ? expectancyPass ? "Positive — strategy viable" : "Negative — do not go live"
              : "No closed trades yet"}
            icon={expectancyPass ? Target : TrendingDown}
            positive={expectancyPass}
            neutral={expectancy == null}
          />
          <StatCard
            title="Active Strategies"
            value={String(activeStrategies.length)}
            sub={bestDsr != null
              ? `Best DSR: ${(bestDsr * 100).toFixed(1)}% · ${(strategies || []).length} configured`
              : `${(strategies || []).length} total configured`}
            icon={Zap}
            positive={bestDsr != null ? bestDsr >= 0.95 : activeStrategies.length > 0}
            neutral={bestDsr == null && activeStrategies.length === 0}
            warn={bestDsr != null && bestDsr < 0.95}
          />
        </div>

        {/* Daily budget strip */}
        {stats && (
          <div className="grid grid-cols-2 gap-3">
            <div className={cn(
              "flex items-center gap-4 px-4 py-3 rounded-xl border",
              dailyWarn ? "bg-yellow-500/5 border-yellow-500/20" : "bg-gray-900 border-gray-800"
            )}>
              <CalendarClock className={cn("w-4 h-4 shrink-0", dailyWarn ? "text-yellow-400" : "text-gray-500")} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between mb-1.5">
                  <span className={cn("text-[11px] font-semibold uppercase tracking-wider", dailyWarn ? "text-yellow-300" : "text-gray-500")}>
                    Daily Trade Budget
                  </span>
                  <span className={cn("text-xs font-bold tabular-nums", dailyWarn ? "text-yellow-300" : "text-white")}>
                    {dailyToday} / {dailyLimit}
                    {dailyToday >= dailyLimit && <span className="ml-1.5 text-[10px] text-red-400 font-normal">limit reached</span>}
                  </span>
                </div>
                <Progress value={dailyPct} className={cn("h-1.5", dailyWarn ? "bg-yellow-900/20" : "bg-gray-800")} />
              </div>
            </div>
            <div className={cn(
              "flex items-center gap-4 px-4 py-3 rounded-xl border",
              dailyLossHit ? "bg-red-500/10 border-red-500/30" : dailyLossWarn ? "bg-orange-500/5 border-orange-500/20" : "bg-gray-900 border-gray-800"
            )}>
              <ShieldAlert className={cn("w-4 h-4 shrink-0", dailyLossHit ? "text-red-400" : dailyLossWarn ? "text-orange-400" : "text-gray-500")} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between mb-1.5">
                  <span className={cn("text-[11px] font-semibold uppercase tracking-wider", dailyLossHit ? "text-red-300" : dailyLossWarn ? "text-orange-300" : "text-gray-500")}>
                    Daily Loss Limit
                  </span>
                  <span className={cn("text-xs font-bold tabular-nums", dailyLossHit ? "text-red-400" : dailyLossWarn ? "text-orange-400" : "text-white")}>
                    {dailyPnl < 0 ? `-$${Math.abs(dailyPnl).toFixed(0)}` : "$0"} / -${dailyLossLimitAmt.toFixed(0)}
                    {dailyLossHit && <span className="ml-1.5 text-[10px] font-normal">paused</span>}
                  </span>
                </div>
                <Progress value={dailyLossPct} className={cn("h-1.5", dailyLossHit ? "bg-red-900/30" : "bg-gray-800")} />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── CHART + RIGHT COLUMN ────────────────────────────────────────────── */}
      <div className="grid grid-cols-12 gap-4">
        {/* Chart — spans 8 of 12 */}
        <Card className="col-span-12 lg:col-span-8 bg-gray-900 border-gray-800">
          <CardHeader className="pb-2 pt-4">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold text-gray-300 flex items-center gap-2">
                <BarChart2 className="w-4 h-4 text-blue-400" />
                {chartView === "intraday"
                  ? `Today's P&L${todayTrades > 0 ? ` — ${todayTrades} trades` : ""}`
                  : "30-Day Equity Curve"}
              </CardTitle>
              <div className="flex gap-1 bg-gray-800 rounded-lg p-0.5">
                {(["intraday", "30d"] as const).map(v => (
                  <button key={v} onClick={() => setChartView(v)}
                    className={cn("text-[11px] px-3 py-1 rounded-md font-medium transition-all",
                      chartView === v ? "bg-blue-600 text-white shadow-sm" : "text-gray-400 hover:text-gray-200"
                    )}>
                    {v === "intraday" ? "Today" : "30 Days"}
                  </button>
                ))}
              </div>
            </div>
          </CardHeader>
          <CardContent className="pt-1">
            {chartView === "intraday" ? (
              hasIntradayData ? (
                <>
                  <div className="flex items-baseline gap-3 mb-3">
                    <span className={cn("text-2xl font-bold", todayRealized >= 0 ? "text-green-400" : "text-red-400")}>
                      {todayRealized >= 0 ? "+" : ""}${todayRealized.toFixed(2)}
                    </span>
                    <span className="text-xs text-gray-500">realized P&L today</span>
                  </div>
                  <ResponsiveContainer width="100%" height={210}>
                    <AreaChart data={intradayPoints}>
                      <defs>
                        <linearGradient id="intradayGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%"  stopColor={todayRealized >= 0 ? "#10b981" : "#ef4444"} stopOpacity={0.25} />
                          <stop offset="95%" stopColor={todayRealized >= 0 ? "#10b981" : "#ef4444"} stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                      <XAxis dataKey="time" tick={{ fontSize: 10, fill: "#6b7280" }} tickLine={false} axisLine={false} />
                      <YAxis tick={{ fontSize: 10, fill: "#6b7280" }} tickLine={false} axisLine={false}
                        tickFormatter={(v: number) => `$${v >= 0 ? "+" : ""}${v.toFixed(0)}`} />
                      <ReferenceLine y={0} stroke="#374151" strokeDasharray="4 4" />
                      <Tooltip
                        contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #1e293b", borderRadius: 10, fontSize: 12 }}
                        formatter={(v: unknown, _: unknown, p: { payload?: { label?: string } }) => [
                          `${Number(v) >= 0 ? "+" : ""}$${Number(v).toFixed(2)}`,
                          p?.payload?.label ?? "P&L",
                        ]}
                      />
                      <Area type="monotone" dataKey="pnl"
                        stroke={todayRealized >= 0 ? "#10b981" : "#ef4444"}
                        fill="url(#intradayGrad)" strokeWidth={2} dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </>
              ) : (
                <div className="h-[240px] flex flex-col items-center justify-center gap-2 text-gray-700">
                  <BarChart2 className="w-10 h-10" />
                  <p className="text-sm font-medium text-gray-500">No trades closed today yet</p>
                  <p className="text-xs text-gray-600">Chart populates as trades close during the session</p>
                </div>
              )
            ) : equityCurveData ? (
              <ResponsiveContainer width="100%" height={240}>
                <AreaChart data={equityCurveData}>
                  <defs>
                    <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="day" tick={{ fontSize: 10, fill: "#6b7280" }} tickLine={false} axisLine={false} interval={9} />
                  <YAxis tick={{ fontSize: 10, fill: "#6b7280" }} tickLine={false} axisLine={false}
                    tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`} />
                  <Tooltip
                    contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #1e293b", borderRadius: 10, fontSize: 12 }}
                    formatter={(v: unknown) => [`$${Number(v).toLocaleString("en-US", { minimumFractionDigits: 2 })}`, "Value"]}
                  />
                  <Area type="monotone" dataKey="value" stroke="#3b82f6" fill="url(#pnlGrad)" strokeWidth={2} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-[240px] flex flex-col items-center justify-center gap-2 text-gray-700">
                <BarChart2 className="w-10 h-10" />
                <p className="text-sm font-medium text-gray-500">No closed trades in the last 30 days</p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Right column: Position pie + Watchlist */}
        <div className="col-span-12 lg:col-span-4 flex flex-col gap-4">
          {/* Position allocation */}
          <Card className="bg-gray-900 border-gray-800 flex-1">
            <CardHeader className="pb-2 pt-4">
              <CardTitle className="text-sm font-semibold text-gray-300">Position Allocation</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              {positionPie.length > 0 ? (
                <>
                  <ResponsiveContainer width="100%" height={120}>
                    <PieChart>
                      <Pie data={positionPie} cx="50%" cy="50%" innerRadius={35} outerRadius={55} dataKey="value" paddingAngle={3}>
                        {positionPie.map((_: unknown, i: number) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                      </Pie>
                      <Tooltip contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #1e293b", borderRadius: 10, fontSize: 12 }}
                        formatter={(v: unknown) => [`$${Number(v).toFixed(2)}`, ""]} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="space-y-1.5 mt-1">
                    {positionPie.slice(0, 4).map((p: { name: string; value: number }, i: number) => (
                      <div key={p.name} className="flex items-center justify-between text-xs">
                        <div className="flex items-center gap-1.5">
                          <div className="w-2 h-2 rounded-full shrink-0" style={{ background: COLORS[i % COLORS.length] }} />
                          <span className="text-gray-400">{p.name}</span>
                        </div>
                        <span className="text-gray-300 font-medium">${p.value.toFixed(0)}</span>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="h-36 flex flex-col items-center justify-center gap-1.5">
                  <DollarSign className="w-7 h-7 text-gray-700" />
                  <p className="text-xs text-gray-600">No open positions</p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Watchlist */}
          <Card className="bg-gray-900 border-gray-800">
            <CardHeader className="pb-2 pt-4">
              <CardTitle className="text-sm font-semibold text-gray-300 flex items-center gap-2">
                <TrendingUp className="w-3.5 h-3.5 text-blue-400" /> Watchlist
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-0 px-4 pb-3">
              {watchlist ? watchlist.map((q: { symbol: string; price: number; change_pct: number; volume: number }) => (
                <div key={q.symbol} className="flex items-center justify-between py-2 border-b border-gray-800/70 last:border-0">
                  <div>
                    <p className="text-sm font-semibold text-white leading-none">{q.symbol}</p>
                    <p className="text-[10px] text-gray-600 mt-0.5">{(q.volume / 1_000_000).toFixed(1)}M vol</p>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-medium text-gray-200">${q.price.toFixed(2)}</p>
                    <p className={cn("text-[11px] font-semibold", q.change_pct >= 0 ? "text-green-400" : "text-red-400")}>
                      {q.change_pct >= 0 ? "+" : ""}{q.change_pct.toFixed(2)}%
                    </p>
                  </div>
                </div>
              )) : (
                <div className="py-6 text-center text-xs text-gray-600">Loading market data…</div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* ── OPEN POSITIONS ──────────────────────────────────────────────────── */}
      {openPositions.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <SectionLabel label={`Open Positions — ${openPositions.length}`} accent="yellow" />
            {flattenConfirm ? (
              <div className="flex items-center gap-2">
                <span className="text-xs text-red-400">Close ALL + pause strategies?</span>
                <Button size="sm" variant="destructive" onClick={() => flattenMut.mutate()}
                  disabled={flattenMut.isPending}
                  className="h-7 px-3 text-xs bg-red-600 hover:bg-red-700">
                  {flattenMut.isPending ? "Closing…" : "Confirm Flatten"}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setFlattenConfirm(false)}
                  className="h-7 px-3 text-xs text-gray-400 hover:text-white">
                  Cancel
                </Button>
              </div>
            ) : (
              <Button size="sm" onClick={() => setFlattenConfirm(true)}
                className="h-7 px-3 text-xs bg-red-900/50 hover:bg-red-700/80 border border-red-700/40 text-red-300 hover:text-white gap-1.5">
                <Siren className="w-3.5 h-3.5" /> Flatten All
              </Button>
            )}
          </div>
          <Card className="bg-gray-900 border-gray-800">
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-gray-800 text-gray-600 uppercase tracking-widest text-[10px]">
                      <th className="text-left px-5 py-3 font-semibold">Symbol</th>
                      <th className="text-left px-4 py-3 font-semibold">Side</th>
                      <th className="text-right px-4 py-3 font-semibold">Qty</th>
                      <th className="text-right px-4 py-3 font-semibold">Avg Entry</th>
                      <th className="text-right px-4 py-3 font-semibold">Current</th>
                      <th className="text-right px-4 py-3 font-semibold">Mkt Value</th>
                      <th className="text-right px-5 py-3 font-semibold">Unrlzd P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {openPositions.map((pos) => {
                      const pnlPos = pos.unrealized_pl >= 0;
                      const pnlPct = pos.avg_entry_price > 0
                        ? ((pos.current_price - pos.avg_entry_price) / pos.avg_entry_price) * 100
                        : 0;
                      return (
                        <tr key={pos.symbol} className="border-b border-gray-800/40 last:border-0 hover:bg-gray-800/20 transition-colors">
                          <td className="px-5 py-3 font-bold text-white">{pos.symbol}</td>
                          <td className="px-4 py-3">
                            <span className={cn("px-2 py-0.5 rounded text-[10px] font-semibold uppercase",
                              pos.side === "long" ? "bg-green-500/15 text-green-400" : "bg-red-500/15 text-red-400"
                            )}>
                              {pos.side}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-right text-gray-300 tabular-nums">{pos.qty}</td>
                          <td className="px-4 py-3 text-right text-gray-300 tabular-nums">${Number(pos.avg_entry_price).toFixed(2)}</td>
                          <td className="px-4 py-3 text-right text-gray-300 tabular-nums">${Number(pos.current_price).toFixed(2)}</td>
                          <td className="px-4 py-3 text-right text-gray-300 tabular-nums">${Number(pos.market_value).toLocaleString("en-US", { maximumFractionDigits: 0 })}</td>
                          <td className="px-5 py-3 text-right tabular-nums">
                            <div className={cn("font-bold", pnlPos ? "text-green-400" : "text-red-400")}>
                              {pnlPos ? "+" : ""}${Number(pos.unrealized_pl).toFixed(2)}
                            </div>
                            <div className={cn("text-[10px] font-medium", pnlPos ? "text-green-600" : "text-red-600")}>
                              {pnlPos ? "+" : ""}{pnlPct.toFixed(2)}%
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ── PERFORMANCE METRICS ─────────────────────────────────────────────── */}
      <div className="space-y-3">
        <SectionLabel label="Performance Metrics" accent="green" />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {/* Expectancy */}
          <Card className="bg-gray-900 border-gray-800 p-4">
            <p className={cn("text-[11px] font-semibold uppercase tracking-widest mb-2",
              expectancyPass ? "text-green-500" : expectancy != null ? "text-red-500" : "text-gray-500")}>
              Expectancy {expectancyPass ? "✓" : expectancy != null ? "✗" : ""}
            </p>
            <p className="text-xl font-bold text-white">
              {expectancy != null ? `${expectancy >= 0 ? "+" : ""}$${expectancy.toFixed(2)}` : "—"}
            </p>
            <div className={cn("mt-2 h-1 rounded-full", expectancyPass ? "bg-green-900/40" : "bg-gray-800")}>
              <div className={cn("h-full rounded-full", expectancyPass ? "bg-green-500" : "bg-red-600")}
                style={{ width: expectancy != null ? "100%" : "0%" }} />
            </div>
          </Card>
          {/* Profit Factor */}
          <Card className="bg-gray-900 border-gray-800 p-4">
            <p className={cn("text-[11px] font-semibold uppercase tracking-widest mb-2",
              pfPass ? "text-green-500" : profitFactor != null ? "text-red-500" : "text-gray-500")}>
              Profit Factor {pfPass ? "✓" : profitFactor != null ? "✗" : ""}
              <span className="text-gray-700 font-normal"> (≥1.5)</span>
            </p>
            <p className="text-xl font-bold text-white">{profitFactor ?? "—"}</p>
            <Progress value={Math.min((profitFactor ?? 0) * 33, 100)} className="mt-2 h-1 bg-gray-800" />
          </Card>
          {/* Max Drawdown */}
          <Card className="bg-gray-900 border-gray-800 p-4">
            <p className={cn("text-[11px] font-semibold uppercase tracking-widest mb-2",
              ddPass ? "text-green-500" : maxDrawdownPct != null ? "text-yellow-500" : "text-gray-500")}>
              Max Drawdown {ddPass ? "✓" : maxDrawdownPct != null ? "⚠" : ""}
              <span className="text-gray-700 font-normal"> (&lt;10%)</span>
            </p>
            <p className="text-xl font-bold text-white">{maxDrawdownPct != null ? `${maxDrawdownPct.toFixed(1)}%` : "—"}</p>
            <Progress value={maxDrawdownPct != null ? Math.min(maxDrawdownPct * 10, 100) : 0} className="mt-2 h-1 bg-gray-800" />
          </Card>
          {/* Mini stats */}
          <Card className="bg-gray-900 border-gray-800 p-4">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-gray-500 mb-2">Trade Stats</p>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <p className="text-[10px] text-gray-600">Avg Win</p>
                <p className="text-xs font-bold text-green-400 mt-0.5">+${stats?.avg_win?.toFixed(2) ?? "0.00"}</p>
              </div>
              <div>
                <p className="text-[10px] text-gray-600">Avg Loss</p>
                <p className="text-xs font-bold text-red-400 mt-0.5">-${Math.abs(stats?.avg_loss ?? 0).toFixed(2)}</p>
              </div>
              <div>
                <p className="text-[10px] text-gray-600">Avg R</p>
                <p className={cn("text-xs font-bold mt-0.5",
                  stats?.r_multiples?.avg_r != null
                    ? stats.r_multiples.avg_r >= 1 ? "text-green-400" : stats.r_multiples.avg_r > 0 ? "text-yellow-400" : "text-red-400"
                    : "text-gray-400")}>
                  {stats?.r_multiples?.avg_r != null ? `${stats.r_multiples.avg_r >= 0 ? "+" : ""}${stats.r_multiples.avg_r}R` : "—"}
                </p>
              </div>
              <div>
                <p className="text-[10px] text-gray-600">Win Rate <span className="text-gray-700">(info)</span></p>
                <p className="text-xs font-bold text-gray-400 mt-0.5">{stats?.win_rate != null ? `${stats.win_rate}%` : "—"}</p>
              </div>
            </div>
          </Card>
        </div>
      </div>

      {/* ── MARKET INTELLIGENCE ─────────────────────────────────────────────── */}
      <div className="space-y-3">
        <SectionLabel label="Market Intelligence" accent="purple" />
        <div className="grid grid-cols-12 gap-4 items-stretch" style={{ height: 680 }}>
          <div className="col-span-12 lg:col-span-5 h-full overflow-hidden"><GrokChatWindow /></div>
          <div className="col-span-12 lg:col-span-4 h-full overflow-hidden"><NewsIntelPanel /></div>
          <div className="col-span-12 lg:col-span-3 flex flex-col gap-3 h-full overflow-hidden">
            <div className="flex-shrink-0"><SituationalAwarenessCard /></div>
            <div className="flex-1 min-h-0 overflow-hidden"><EventsFeed /></div>
          </div>
        </div>
      </div>

      {/* ── TOOLS ROW ───────────────────────────────────────────────────────── */}
      <div className="space-y-3">
        <SectionLabel label="Trading Tools" accent="blue" />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <GrokTraderCard />
          <AutoTradePanel />
          <SessionCard />
          {/* System status mini card */}
          <Card className="bg-gray-900 border-gray-800">
            <CardHeader className="pb-2 pt-4">
              <CardTitle className="text-sm font-semibold text-gray-300 flex items-center gap-2">
                <Activity className="w-3.5 h-3.5 text-blue-400" /> System Status
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 pt-0">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Radio className={cn("w-3.5 h-3.5", automationStatus?.running ? "text-green-400" : "text-gray-600")} />
                  <span className="text-xs text-gray-400">Scheduler</span>
                </div>
                <span className={cn("text-xs font-semibold", automationStatus?.running ? "text-green-400" : "text-gray-600")}>
                  {automationStatus ? (automationStatus.running ? `Running · ${(automationStatus.jobs as unknown[])?.length ?? 0} jobs` : "Stopped") : "—"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <ScanSearch className="w-3.5 h-3.5 text-gray-600" />
                  <span className="text-xs text-gray-400">Last Scan</span>
                </div>
                <span className="text-xs font-semibold text-gray-300">
                  {lastScanMinsAgo != null
                    ? lastScanMinsAgo < 1 ? "just now" : lastScanMinsAgo < 60 ? `${lastScanMinsAgo}m ago` : `${Math.round(lastScanMinsAgo / 60)}h ago`
                    : "never"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Bot className="w-3.5 h-3.5 text-gray-600" />
                  <span className="text-xs text-gray-400">AutoPilot</span>
                </div>
                <span className="text-xs font-semibold text-gray-300">
                  {(lastAutoPilot as { status?: string } | undefined)?.status === "no_results"
                    ? "never run"
                    : autopilotLeaderboard.length > 0
                    ? `${autopilotLeaderboard.length} results`
                    : "—"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Clock className="w-3.5 h-3.5 text-gray-600" />
                  <span className="text-xs text-gray-400">Trades today</span>
                </div>
                <div className="flex items-center gap-1.5">
                  {dailyToday >= dailyLimit
                    ? <CircleX className="w-3.5 h-3.5 text-red-400" />
                    : <CircleCheck className="w-3.5 h-3.5 text-green-400" />}
                  <span className={cn("text-xs font-semibold", dailyToday >= dailyLimit ? "text-red-400" : "text-gray-300")}>
                    {dailyToday} / {dailyLimit}
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* ── ENGINE CONTROL (tabbed) ─────────────────────────────────────────── */}
      <div className="space-y-3">
        <SectionLabel label="Engine Control" accent="gray" />
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="flex items-center gap-1 px-4 pt-3 border-b border-gray-800">
            {([
              { id: "autopilot",  label: "AutoPilot",  icon: Bot },
              { id: "scanner",    label: "Scanner",    icon: ScanSearch },
              { id: "automation", label: "Automation", icon: Cpu },
            ] as const).map(t => (
              <button
                key={t.id}
                onClick={() => setEngineTab(t.id)}
                className={cn(
                  "flex items-center gap-1.5 px-4 py-2 text-xs font-medium rounded-t-lg transition-all -mb-px border",
                  engineTab === t.id
                    ? "bg-gray-950 border-gray-700 border-b-gray-950 text-white"
                    : "border-transparent text-gray-500 hover:text-gray-300 hover:bg-gray-800/40"
                )}
              >
                <t.icon className="w-3.5 h-3.5" /> {t.label}
              </button>
            ))}
          </div>
          <div className="bg-gray-950 rounded-b-xl">
            {engineTab === "autopilot"  && <AutoPilotPanel  />}
            {engineTab === "scanner"    && <ScannerPanel    />}
            {engineTab === "automation" && <AutomationPanel />}
          </div>
        </div>
      </div>

    </div>
  );
}
