"use client";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { deployPreview, deployExecute } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import {
  X, Zap, TrendingUp, TrendingDown, Minus,
  AlertTriangle, CheckCircle, Loader2, ShieldCheck,
} from "lucide-react";

type DeployRow = {
  symbol: string;
  strategy: string;
  parameters?: Record<string, unknown>;
  score: number;
  profit_factor: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  total_trades: number;
  expectancy: number;
};

type Preview = {
  signal: "buy" | "sell" | "hold";
  confidence: number;
  current_price: number;
  equity: number;
  position_usd: number;
  estimated_qty: number;
  stop_price: number;
  target_price: number;
  indicators: Record<string, number>;
  broker: string;
  note?: string | null;
};

type ExecuteResult = {
  placed: boolean;
  reason?: string;
  signal: string;
  qty?: number;
  current_price?: number;
  order?: Record<string, unknown>;
  strategy_id?: number;
  trade_id?: number;
};

type Props = {
  row: DeployRow;
  onClose: () => void;
};

const SIGNAL_META = {
  buy:  { label: "BUY",  color: "text-green-400",  bg: "bg-green-500/10 border-green-500/30", Icon: TrendingUp },
  sell: { label: "SELL", color: "text-red-400",    bg: "bg-red-500/10 border-red-500/30",     Icon: TrendingDown },
  hold: { label: "HOLD", color: "text-gray-400",   bg: "bg-gray-700/40 border-gray-600/30",   Icon: Minus },
};

export default function DeployModal({ row, onClose }: Props) {
  const [posSize, setPosSize]   = useState(5);
  const [slPct, setSlPct]       = useState(1.5);
  const [tpPct, setTpPct]       = useState(3.0);
  const [preview, setPreview]   = useState<Preview | null>(null);
  const [executed, setExecuted] = useState<ExecuteResult | null>(null);

  const payload = {
    symbol:           row.symbol,
    strategy:         row.strategy,
    parameters:       row.parameters ?? {},
    position_size_pct: posSize,
    stop_loss_pct:    slPct,
    take_profit_pct:  tpPct,
  };

  const previewMut = useMutation({
    mutationFn: () => deployPreview(payload),
    onSuccess:  (data: Preview) => setPreview(data),
    onError:    (e: Error) => toast.error("Preview failed", { description: e.message }),
  });

  const executeMut = useMutation({
    mutationFn: () => deployExecute({ ...payload, confirmed: true }),
    onSuccess:  (data: ExecuteResult) => {
      setExecuted(data);
      if (data.placed) {
        toast.success("Order placed!", {
          description: `${data.signal.toUpperCase()} ${data.qty} ${row.symbol} @ $${data.current_price}`,
        });
      } else {
        toast.info("No order placed", { description: data.reason });
      }
    },
    onError: (e: Error) => toast.error("Order failed", { description: e.message }),
  });

  const sig = preview ? SIGNAL_META[preview.signal] ?? SIGNAL_META.hold : null;
  const canTrade = preview?.signal !== "hold";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
      <div className="relative w-full max-w-xl bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <div className="flex items-center gap-3">
            <Zap className="w-5 h-5 text-yellow-400" />
            <div>
              <p className="text-sm font-bold text-white">Deploy Strategy</p>
              <p className="text-xs text-gray-500">{row.symbol} · {row.strategy.replace(/_/g, " ")}</p>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white p-1 rounded-full hover:bg-gray-800 transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 px-6 py-5 space-y-5">

          {/* Backtest summary */}
          <div className="grid grid-cols-4 gap-2">
            {[
              { label: "Score",    val: row.score,            color: row.score >= 60 ? "text-green-400" : row.score >= 35 ? "text-yellow-400" : "text-red-400" },
              { label: "PF",       val: `${row.profit_factor}×`, color: row.profit_factor >= 1.5 ? "text-green-400" : "text-yellow-400" },
              { label: "DD",       val: `${row.max_drawdown_pct}%`, color: row.max_drawdown_pct < 10 ? "text-green-400" : "text-red-400" },
              { label: "Trades",   val: row.total_trades,    color: "text-gray-300" },
            ].map(m => (
              <div key={m.label} className="bg-gray-800/60 rounded-xl p-3 text-center">
                <p className="text-[10px] text-gray-500 uppercase tracking-wide">{m.label}</p>
                <p className={cn("text-lg font-black mt-0.5 tabular-nums", m.color)}>{m.val}</p>
              </div>
            ))}
          </div>

          {/* Risk settings */}
          <div className="space-y-3">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Risk Settings</p>
            <div className="grid grid-cols-3 gap-3">
              {[
                { label: "Position Size", key: "pos", val: posSize, set: setPosSize, min: 1, max: 25, step: 0.5, suffix: "%" },
                { label: "Stop Loss",     key: "sl",  val: slPct,   set: setSlPct,   min: 0.5, max: 10, step: 0.25, suffix: "%" },
                { label: "Take Profit",   key: "tp",  val: tpPct,   set: setTpPct,   min: 0.5, max: 20, step: 0.25, suffix: "%" },
              ].map(r => (
                <div key={r.key} className="bg-gray-800/50 rounded-xl p-3">
                  <label className="text-[10px] text-gray-500 uppercase tracking-wide block mb-2">{r.label}</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number" min={r.min} max={r.max} step={r.step}
                      value={r.val}
                      onChange={e => r.set(Number(e.target.value))}
                      className="w-full bg-gray-900 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-white text-center font-bold focus:outline-none focus:border-yellow-500/50"
                    />
                    <span className="text-xs text-gray-500 shrink-0">{r.suffix}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Live signal preview */}
          {!preview && !executed && (
            <button
              onClick={() => { setPreview(null); previewMut.mutate(); }}
              disabled={previewMut.isPending}
              className="w-full py-3 rounded-xl bg-gray-800 hover:bg-gray-700 border border-gray-600 text-sm font-semibold text-white transition-colors flex items-center justify-center gap-2"
            >
              {previewMut.isPending
                ? <><Loader2 className="w-4 h-4 animate-spin" /> Fetching live signal…</>
                : <><Zap className="w-4 h-4 text-yellow-400" /> Get Live Signal</>}
            </button>
          )}

          {preview && !executed && sig && (
            <div className={cn("rounded-xl border p-4 space-y-4", sig.bg)}>
              {/* Signal header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className={cn("w-10 h-10 rounded-full flex items-center justify-center border", sig.bg)}>
                    <sig.Icon className={cn("w-5 h-5", sig.color)} />
                  </div>
                  <div>
                    <p className={cn("text-xl font-black", sig.color)}>{sig.label}</p>
                    <p className="text-xs text-gray-500">Confidence {Math.round(preview.confidence * 100)}%</p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-xs text-gray-500">Price</p>
                  <p className="text-lg font-bold text-white">${preview.current_price.toLocaleString()}</p>
                  <p className="text-[10px] text-gray-600">{preview.broker}</p>
                </div>
              </div>

              {/* Order details */}
              {preview.signal !== "hold" && (
                <div className="grid grid-cols-2 gap-2 pt-1">
                  <div className="bg-black/20 rounded-lg p-2.5">
                    <p className="text-[10px] text-gray-500">Position Size</p>
                    <p className="text-sm font-bold text-white">${preview.position_usd.toLocaleString(undefined, {maximumFractionDigits: 0})}</p>
                    <p className="text-[10px] text-gray-600">{preview.estimated_qty} units</p>
                  </div>
                  <div className="bg-black/20 rounded-lg p-2.5">
                    <p className="text-[10px] text-gray-500">Account Equity</p>
                    <p className="text-sm font-bold text-white">${preview.equity.toLocaleString(undefined, {maximumFractionDigits: 0})}</p>
                    <p className="text-[10px] text-gray-600">{posSize}% allocated</p>
                  </div>
                  <div className="bg-black/20 rounded-lg p-2.5">
                    <p className="text-[10px] text-red-500">Stop Loss</p>
                    <p className="text-sm font-bold text-red-400">${preview.stop_price.toLocaleString()}</p>
                    <p className="text-[10px] text-gray-600">−{slPct}%</p>
                  </div>
                  <div className="bg-black/20 rounded-lg p-2.5">
                    <p className="text-[10px] text-green-500">Take Profit</p>
                    <p className="text-sm font-bold text-green-400">${preview.target_price.toLocaleString()}</p>
                    <p className="text-[10px] text-gray-600">+{tpPct}%</p>
                  </div>
                </div>
              )}

              {preview.signal === "hold" && (
                <div className="flex items-center gap-2 py-1">
                  <AlertTriangle className="w-4 h-4 text-yellow-400 shrink-0" />
                  <p className="text-xs text-gray-400">
                    Current signal is HOLD — the strategy sees no entry right now.
                    You can still deploy, but no order will be placed until the next BUY/SELL signal.
                  </p>
                </div>
              )}

              {preview.note && (
                <div className="flex items-start gap-2 rounded-lg bg-blue-500/5 border border-blue-500/20 p-2.5">
                  <AlertTriangle className="w-3.5 h-3.5 text-blue-400 shrink-0 mt-0.5" />
                  <p className="text-[11px] text-blue-300">{preview.note}</p>
                </div>
              )}

              {/* Re-fetch link */}
              <button
                onClick={() => previewMut.mutate()}
                disabled={previewMut.isPending}
                className="text-[11px] text-gray-600 hover:text-gray-400 underline"
              >
                Refresh signal
              </button>
            </div>
          )}

          {/* Executed result */}
          {executed && (
            <div className={cn(
              "rounded-xl border p-5 text-center space-y-2",
              executed.placed
                ? "bg-green-950/30 border-green-600/40"
                : "bg-gray-800/50 border-gray-700"
            )}>
              {executed.placed ? (
                <>
                  <CheckCircle className="w-10 h-10 text-green-400 mx-auto" />
                  <p className="text-base font-bold text-green-400">Order Placed!</p>
                  <p className="text-sm text-gray-300">
                    <span className="font-bold text-white">{executed.signal?.toUpperCase()}</span>{" "}
                    {executed.qty} {row.symbol} @ ${executed.current_price?.toLocaleString()}
                  </p>
                  <p className="text-xs text-gray-500">
                    Order ID: {String(executed.order?.order_id ?? "—")} ·{" "}
                    Strategy saved as "AutoPilot · {row.symbol} · {row.strategy}"
                  </p>
                  <p className="text-xs text-green-600 mt-1">
                    Strategy is now active — the automation engine will manage it going forward.
                  </p>
                </>
              ) : (
                <>
                  <Minus className="w-10 h-10 text-gray-500 mx-auto" />
                  <p className="text-sm font-semibold text-gray-300">No Order Placed</p>
                  <p className="text-xs text-gray-500">{executed.reason}</p>
                  <p className="text-xs text-gray-600">
                    Strategy saved — will place an order next time the signal triggers.
                  </p>
                </>
              )}
            </div>
          )}

          {/* Warning disclaimer */}
          {preview && !executed && (
            <div className="flex items-start gap-2 rounded-lg bg-yellow-500/5 border border-yellow-500/20 p-3">
              <ShieldCheck className="w-4 h-4 text-yellow-400 shrink-0 mt-0.5" />
              <p className="text-[11px] text-gray-500">
                This will place a <strong className="text-yellow-400">paper / testnet</strong> market order.
                No real money is at risk. The strategy will be saved and managed by the automation engine.
              </p>
            </div>
          )}
        </div>

        {/* Footer actions */}
        {!executed && (
          <div className="flex gap-3 px-6 py-4 border-t border-gray-800 shrink-0">
            <button
              onClick={onClose}
              className="flex-1 py-2.5 rounded-xl bg-gray-800 hover:bg-gray-700 text-sm text-gray-300 font-medium transition-colors"
            >
              Cancel
            </button>
            {preview && (
              <button
                onClick={() => executeMut.mutate()}
                disabled={executeMut.isPending}
                className={cn(
                  "flex-1 py-2.5 rounded-xl text-sm font-bold transition-colors flex items-center justify-center gap-2",
                  canTrade
                    ? "bg-green-500 hover:bg-green-400 text-black"
                    : "bg-gray-700 hover:bg-gray-600 text-white"
                )}
              >
                {executeMut.isPending
                  ? <><Loader2 className="w-4 h-4 animate-spin" />Placing Order…</>
                  : canTrade
                  ? <><CheckCircle className="w-4 h-4" />Confirm &amp; Place Order</>
                  : <><CheckCircle className="w-4 h-4" />Deploy (no order yet)</>}
              </button>
            )}
          </div>
        )}
        {executed && (
          <div className="px-6 py-4 border-t border-gray-800 shrink-0">
            <button
              onClick={onClose}
              className="w-full py-2.5 rounded-xl bg-gray-800 hover:bg-gray-700 text-sm text-gray-300 font-medium transition-colors"
            >
              Close
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
