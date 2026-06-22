"use client";
import { useQuery } from "@tanstack/react-query";
import { getPortfolioSnapshot } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TrendingUp, TrendingDown, DollarSign, AlertCircle, RefreshCw } from "lucide-react";
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
} from "recharts";
import { cn } from "@/lib/utils";

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"];

interface BrokerSummary {
  broker: string;
  portfolio_value: number;
  cash: number;
  equity: number;
  unrealized_pnl: number;
  positions_count: number;
}

interface Position {
  symbol: string;
  side: string;
  qty: number;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
}

export default function PortfolioPage() {
  const { data: portfolio, isLoading, error, dataUpdatedAt } = useQuery({
    queryKey: ["portfolio"],
    queryFn: getPortfolioSnapshot,
    retry: false,
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return (
      <div className="p-6 flex items-center gap-2 text-gray-400 text-sm">
        <RefreshCw className="w-4 h-4 animate-spin" /> Loading portfolio…
      </div>
    );
  }

  if (error || !portfolio) {
    return (
      <div className="p-6">
        <Card className="bg-yellow-500/10 border-yellow-500/30">
          <CardContent className="p-5 flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-yellow-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-yellow-300 font-medium">No portfolio data</p>
              <p className="text-yellow-300/70 text-sm mt-1">
                Add your Alpaca paper trading API keys in{" "}
                <a href="/settings" className="underline">Settings</a> to see live data.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  const positions: Position[] = portfolio.positions || [];
  const alpaca: BrokerSummary | null  = portfolio.brokers?.alpaca  ?? null;
  const binance: BrokerSummary | null = portfolio.brokers?.binance ?? null;

  const pieData = positions.map(p => ({ name: p.symbol, value: Math.abs(p.market_value) }));
  const barData = positions.map(p => ({ symbol: p.symbol, pnl: p.unrealized_pl }));
  const lastRefresh = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : "—";

  const pnlColor = (v: number) => v >= 0 ? "text-green-400" : "text-red-400";
  const pnlPrefix = (v: number) => v >= 0 ? "+" : "";

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Portfolio</h1>
          <p className="text-sm text-gray-400 mt-0.5">
            Paper trading · {positions.length} open position{positions.length !== 1 ? "s" : ""}
          </p>
        </div>
        <p className="text-[11px] text-gray-600">Updated {lastRefresh}</p>
      </div>

      {/* Combined Summary */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          {
            label: "Total Portfolio Value",
            value: `$${Number(portfolio.portfolio_value ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}`,
            icon: DollarSign,
            color: "text-white",
          },
          {
            label: "Total Cash",
            value: `$${Number(portfolio.cash ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}`,
            icon: DollarSign,
            color: "text-blue-400",
          },
          {
            label: "Unrealized P&L",
            value: `${pnlPrefix(portfolio.total_unrealized_pnl ?? 0)}$${(portfolio.total_unrealized_pnl ?? 0).toFixed(2)}`,
            icon: (portfolio.total_unrealized_pnl ?? 0) >= 0 ? TrendingUp : TrendingDown,
            color: pnlColor(portfolio.total_unrealized_pnl ?? 0),
          },
          {
            label: "Open Positions",
            value: String(portfolio.open_positions_count ?? positions.length),
            icon: TrendingUp,
            color: "text-white",
          },
        ].map(m => (
          <Card key={m.label} className="bg-gray-900 border-gray-800">
            <CardContent className="p-5">
              <p className="text-xs text-gray-400 uppercase font-medium tracking-wide">{m.label}</p>
              <p className={cn("text-xl font-bold mt-1", m.color)}>{m.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Broker Breakdown */}
      {(alpaca || binance) && (
        <div>
          <h2 className="text-xs font-medium text-gray-500 uppercase tracking-widest mb-3">Broker Breakdown</h2>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Alpaca */}
            {alpaca && (
              <Card className="bg-gray-900 border-blue-500/20">
                <CardContent className="p-5">
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <p className="text-sm font-semibold text-blue-300">Alpaca Paper</p>
                      <p className="text-[10px] text-gray-500 mt-0.5">US equities · paper trading</p>
                    </div>
                    <Badge className="bg-blue-500/10 text-blue-400 border-blue-500/30 text-[10px]">ACTIVE</Badge>
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <p className="text-[10px] text-gray-500 uppercase tracking-wide">Value</p>
                      <p className="text-base font-bold text-white mt-0.5">
                        ${Number(alpaca.portfolio_value).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-gray-500 uppercase tracking-wide">Cash</p>
                      <p className="text-base font-bold text-blue-300 mt-0.5">
                        ${Number(alpaca.cash).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-gray-500 uppercase tracking-wide">Unrealized P&L</p>
                      <p className={cn("text-base font-bold mt-0.5", pnlColor(alpaca.unrealized_pnl))}>
                        {pnlPrefix(alpaca.unrealized_pnl)}${alpaca.unrealized_pnl.toFixed(2)}
                      </p>
                    </div>
                  </div>
                  <p className="text-[10px] text-gray-600 mt-3">{alpaca.positions_count} open position{alpaca.positions_count !== 1 ? "s" : ""}</p>
                </CardContent>
              </Card>
            )}

            {/* Binance */}
            {binance && (
              <Card className="bg-gray-900 border-yellow-500/20">
                <CardContent className="p-5">
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <p className="text-sm font-semibold text-yellow-300">Binance Testnet</p>
                      <p className="text-[10px] text-gray-500 mt-0.5">Crypto · testnet (simulated)</p>
                    </div>
                    <Badge className="bg-yellow-500/10 text-yellow-400 border-yellow-500/30 text-[10px]">TESTNET</Badge>
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <p className="text-[10px] text-gray-500 uppercase tracking-wide">Value</p>
                      <p className="text-base font-bold text-white mt-0.5">
                        ${Number(binance.portfolio_value).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-gray-500 uppercase tracking-wide">Cash</p>
                      <p className="text-base font-bold text-yellow-300 mt-0.5">
                        ${Number(binance.cash).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-gray-500 uppercase tracking-wide">Unrealized P&L</p>
                      <p className={cn("text-base font-bold mt-0.5", pnlColor(binance.unrealized_pnl))}>
                        {pnlPrefix(binance.unrealized_pnl)}${binance.unrealized_pnl.toFixed(2)}
                      </p>
                    </div>
                  </div>
                  <p className="text-[10px] text-gray-600 mt-3">{binance.positions_count} open position{binance.positions_count !== 1 ? "s" : ""}</p>
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      )}

      {/* Charts */}
      {positions.length > 0 && (
        <div className="grid grid-cols-2 gap-4">
          <Card className="bg-gray-900 border-gray-800">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-gray-300">Position Allocation</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Pie
                    data={pieData} cx="50%" cy="50%"
                    innerRadius={50} outerRadius={85}
                    dataKey="value" paddingAngle={3} labelLine={false}
                  >
                    {pieData.map((_: unknown, i: number) => (
                      <Cell key={i} fill={COLORS[i % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8, fontSize: 12 }}
                    formatter={(v: unknown) => [`$${Number(v).toFixed(2)}`, "Value"]}
                  />
                </PieChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card className="bg-gray-900 border-gray-800">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-gray-300">Unrealized P&L by Position</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={barData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="symbol" tick={{ fontSize: 11, fill: "#6b7280" }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: "#6b7280" }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `$${v.toFixed(0)}`} />
                  <Tooltip
                    contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8, fontSize: 12 }}
                    formatter={(v: unknown) => [`$${Number(v).toFixed(2)}`, "P&L"]}
                  />
                  <Bar dataKey="pnl" radius={[4, 4, 0, 0]} fill="#3b82f6" />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Positions Table */}
      <Card className="bg-gray-900 border-gray-800">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium text-gray-300">
            Open Positions ({positions.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {positions.length === 0 ? (
            <div className="py-12 text-center text-gray-500 text-sm">No open positions</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[11px] text-gray-500 uppercase tracking-wide border-b border-gray-800">
                    {["Symbol", "Side", "Qty", "Avg Entry", "Current Price", "Market Value", "Unrealized P&L", "P&L %"].map(h => (
                      <th key={h} className="text-left py-2 pr-4 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800">
                  {positions.map((p, idx) => (
                    <tr key={`${p.symbol}-${idx}`} className="hover:bg-gray-800/50 transition-colors">
                      <td className="py-3 pr-4 font-semibold text-white">{p.symbol}</td>
                      <td className="py-3 pr-4">
                        <Badge className={cn("text-[10px] border",
                          p.side === "long"
                            ? "bg-green-500/20 text-green-400 border-green-500/30"
                            : "bg-red-500/20 text-red-400 border-red-500/30"
                        )}>
                          {p.side}
                        </Badge>
                      </td>
                      <td className="py-3 pr-4 text-gray-300 font-mono text-xs">{Number(p.qty).toFixed(4)}</td>
                      <td className="py-3 pr-4 text-gray-300">${Number(p.avg_entry_price).toFixed(2)}</td>
                      <td className="py-3 pr-4 text-white font-medium">${Number(p.current_price).toFixed(2)}</td>
                      <td className="py-3 pr-4 text-gray-300">${Number(p.market_value).toFixed(2)}</td>
                      <td className={cn("py-3 pr-4 font-semibold", pnlColor(p.unrealized_pl))}>
                        {pnlPrefix(p.unrealized_pl)}${Number(p.unrealized_pl).toFixed(2)}
                      </td>
                      <td className={cn("py-3 pr-4 font-semibold", pnlColor(p.unrealized_plpc))}>
                        {pnlPrefix(p.unrealized_plpc)}{Number(p.unrealized_plpc).toFixed(2)}%
                      </td>
                    </tr>
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
