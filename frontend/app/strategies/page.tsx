"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getStrategies, getStrategyTemplates, createStrategy, updateStrategy, deleteStrategy, runSignal, executeStrategy, runNow } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { toast } from "sonner";
import { Plus, Play, Pause, Trash2, Zap, TrendingUp, ChevronRight, BarChart3, Settings2, Timer } from "lucide-react";
import { cn } from "@/lib/utils";

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-500/20 text-green-400 border-green-500/30",
  paused: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  stopped: "bg-red-500/20 text-red-400 border-red-500/30",
};

const TEMPLATE_ICONS: Record<string, string> = {
  rsi: "📉", macd: "📊", ma_crossover: "📈", bollinger: "🔔", momentum: "⚡",
};

interface ParamMeta {
  type: string; default: unknown; min?: number; max?: number;
  label: string; options?: string[];
}

function StrategyBuilder({ templates, onClose }: { templates: Record<string, { name: string; description: string; parameters: Record<string, ParamMeta>; risk_config: Record<string, ParamMeta> }>; onClose: () => void }) {
  const qc = useQueryClient();
  const [step, setStep] = useState(1);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [name, setName] = useState("");
  const [symbol, setSymbol] = useState("");
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [riskConfig, setRiskConfig] = useState<Record<string, unknown>>({});

  const createMut = useMutation({
    mutationFn: createStrategy,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      toast.success("Strategy created!");
      onClose();
    },
    onError: () => toast.error("Failed to create strategy"),
  });

  const pickTemplate = (key: string) => {
    setSelectedTemplate(key);
    const tpl = templates[key];
    setParams(Object.fromEntries(Object.entries(tpl.parameters).map(([k, v]) => [k, v.default])));
    setRiskConfig(Object.fromEntries(Object.entries(tpl.risk_config).map(([k, v]) => [k, v.default])));
    setStep(2);
  };

  const tpl = templates[selectedTemplate];

  const renderField = (key: string, meta: ParamMeta, value: unknown, onChange: (v: unknown) => void) => {
    if (meta.type === "select") {
      return (
        <div key={key}>
          <Label className="text-xs text-gray-400">{meta.label}</Label>
          <Select value={String(value)} onValueChange={onChange}>
            <SelectTrigger className="mt-1 bg-gray-800 border-gray-700 text-white">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="bg-gray-800 border-gray-700">
              {meta.options?.map(o => <SelectItem key={o} value={o} className="text-white">{o}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      );
    }
    if (meta.type === "bool") {
      return (
        <div key={key} className="flex items-center justify-between">
          <Label className="text-xs text-gray-400">{meta.label}</Label>
          <button
            onClick={() => onChange(!value)}
            className={cn("relative w-10 h-5 rounded-full transition-colors", value ? "bg-blue-600" : "bg-gray-700")}
          >
            <div className={cn("absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform", value ? "translate-x-5" : "translate-x-0.5")} />
          </button>
        </div>
      );
    }
    return (
      <div key={key}>
        <Label className="text-xs text-gray-400">{meta.label}</Label>
        <Input
          type="number"
          value={String(value)}
          min={meta.min}
          max={meta.max}
          step={meta.type === "float" ? 0.1 : 1}
          onChange={e => onChange(meta.type === "float" ? parseFloat(e.target.value) : parseInt(e.target.value))}
          className="mt-1 bg-gray-800 border-gray-700 text-white"
        />
        {meta.min !== undefined && <p className="text-[10px] text-gray-500 mt-0.5">Range: {meta.min} – {meta.max}</p>}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {/* Stepper */}
      <div className="flex items-center gap-2 text-xs">
        {["Choose Template", "Configure", "Risk Settings"].map((s, i) => (
          <div key={s} className="flex items-center gap-2">
            <div className={cn("w-5 h-5 rounded-full flex items-center justify-center font-bold", step > i + 1 ? "bg-blue-600" : step === i + 1 ? "bg-blue-600" : "bg-gray-700", "text-white text-[10px]")}>
              {i + 1}
            </div>
            <span className={step === i + 1 ? "text-white" : "text-gray-500"}>{s}</span>
            {i < 2 && <ChevronRight className="w-3 h-3 text-gray-600" />}
          </div>
        ))}
      </div>

      <Separator className="bg-gray-800" />

      {step === 1 && (
        <div className="grid grid-cols-1 gap-2">
          {Object.entries(templates).map(([key, tpl]) => (
            <button key={key} onClick={() => pickTemplate(key)}
              className="flex items-start gap-3 p-3 bg-gray-800 hover:bg-gray-700 rounded-xl text-left transition-all border border-gray-700 hover:border-blue-500/50">
              <span className="text-2xl">{TEMPLATE_ICONS[key]}</span>
              <div>
                <p className="font-medium text-white text-sm">{tpl.name}</p>
                <p className="text-xs text-gray-400 mt-0.5">{tpl.description}</p>
              </div>
              <ChevronRight className="w-4 h-4 text-gray-500 ml-auto mt-1 shrink-0" />
            </button>
          ))}
        </div>
      )}

      {step === 2 && tpl && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs text-gray-400">Strategy Name</Label>
              <Input value={name} onChange={e => setName(e.target.value)} placeholder={`My ${tpl.name}`}
                className="mt-1 bg-gray-800 border-gray-700 text-white" />
            </div>
            <div>
              <Label className="text-xs text-gray-400">Symbol</Label>
              <Input value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())} placeholder="AAPL"
                className="mt-1 bg-gray-800 border-gray-700 text-white" />
            </div>
          </div>
          <Separator className="bg-gray-800" />
          <p className="text-xs font-medium text-gray-300 uppercase tracking-wide">Strategy Parameters</p>
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(tpl.parameters).map(([k, meta]) =>
              renderField(k, meta, params[k], v => setParams(p => ({ ...p, [k]: v })))
            )}
          </div>
          <div className="flex gap-2 pt-2">
            <Button variant="outline" size="sm" onClick={() => setStep(1)} className="border-gray-700 text-gray-300">← Back</Button>
            <Button size="sm" onClick={() => setStep(3)} disabled={!name || !symbol} className="bg-blue-600 hover:bg-blue-700">
              Next: Risk Settings →
            </Button>
          </div>
        </div>
      )}

      {step === 3 && tpl && (
        <div className="space-y-4">
          <p className="text-xs font-medium text-gray-300 uppercase tracking-wide">Risk Management</p>
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(tpl.risk_config).map(([k, meta]) =>
              renderField(k, meta, riskConfig[k], v => setRiskConfig(r => ({ ...r, [k]: v })))
            )}
          </div>
          <div className="bg-blue-500/10 border border-blue-500/30 rounded-xl p-3 text-xs text-blue-300 space-y-1">
            <p className="font-medium">Risk Summary</p>
            <p>Stop Loss: <span className="text-red-400">{String(riskConfig.stop_loss_pct)}%</span> · Take Profit: <span className="text-green-400">{String(riskConfig.take_profit_pct)}%</span> · Position: <span className="text-white">{String(riskConfig.position_size_pct)}% of equity</span></p>
          </div>
          <div className="flex gap-2 pt-2">
            <Button variant="outline" size="sm" onClick={() => setStep(2)} className="border-gray-700 text-gray-300">← Back</Button>
            <Button size="sm" onClick={() => createMut.mutate({ name, template: selectedTemplate, symbol, parameters: params, risk_config: riskConfig })}
              disabled={createMut.isPending} className="bg-green-600 hover:bg-green-700">
              {createMut.isPending ? "Creating…" : "Create Strategy ✓"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function StrategiesPage() {
  const qc = useQueryClient();
  const [showBuilder, setShowBuilder] = useState(false);
  const [signalResult, setSignalResult] = useState<null | { strategy_name: string; signal: string; confidence: number; indicators: Record<string, unknown> }>(null);

  const { data: strategies = [] } = useQuery({ queryKey: ["strategies"], queryFn: getStrategies });
  const { data: templates } = useQuery({ queryKey: ["templates"], queryFn: getStrategyTemplates });

  const toggleMut = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => updateStrategy(id, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategies"] }),
  });

  const deleteMut = useMutation({
    mutationFn: deleteStrategy,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["strategies"] }); toast.success("Strategy deleted"); },
  });

  const runNowMut = useMutation({
    mutationFn: runNow,
    onSuccess: (data) => {
      toast.success(`Force-run complete: ${data.strategy}`, { description: "Check Trades tab for results" });
      qc.invalidateQueries({ queryKey: ["strategies", "trades"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg ?? "Force-run failed — check API keys in Settings");
    },
  });

  const signalMut = useMutation({
    mutationFn: runSignal,
    onSuccess: (data) => setSignalResult(data),
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg ?? "Failed to run signal — check API keys in Settings");
    },
  });

  const executeMut = useMutation({
    mutationFn: executeStrategy,
    onSuccess: (data) => {
      if (data.action === "hold") {
        toast.info(`No signal — market conditions not met for entry`, { description: `Confidence: ${((data.signal_confidence ?? 0) * 100).toFixed(0)}%` });
      } else {
        toast.success(`Order placed: ${data.action?.toUpperCase()} ${data.order?.symbol ?? ""}`, {
          description: `Qty: ${data.order?.qty} · Confidence: ${((data.signal_confidence ?? 0) * 100).toFixed(0)}%`,
        });
        qc.invalidateQueries({ queryKey: ["strategies"] });
      }
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg ?? "Execution failed — check API keys in Settings");
    },
  });

  const active = strategies.filter((s: { status: string }) => s.status === "active");
  const paused = strategies.filter((s: { status: string }) => s.status !== "active");

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Strategies</h1>
          <p className="text-sm text-gray-400 mt-0.5">{active.length} active · {paused.length} paused</p>
        </div>
        <Button onClick={() => setShowBuilder(true)} className="bg-blue-600 hover:bg-blue-700 gap-2">
          <Plus className="w-4 h-4" /> New Strategy
        </Button>
      </div>

      {strategies.length === 0 && (
        <Card className="bg-gray-900 border-gray-800 border-dashed">
          <CardContent className="py-16 flex flex-col items-center gap-3">
            <div className="p-4 bg-gray-800 rounded-2xl"><TrendingUp className="w-8 h-8 text-gray-500" /></div>
            <p className="text-gray-300 font-medium">No strategies yet</p>
            <p className="text-gray-500 text-sm text-center max-w-xs">Click &quot;New Strategy&quot; to create your first no-code strategy. No scripting required.</p>
            <Button onClick={() => setShowBuilder(true)} className="mt-2 bg-blue-600 hover:bg-blue-700 gap-2">
              <Plus className="w-4 h-4" /> Create First Strategy
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4">
        {strategies.map((s: { id: number; name: string; template: string; symbol: string; status: string; total_pnl: number; win_rate: number; total_trades: number; parameters: Record<string, unknown>; risk_config: Record<string, unknown> }) => (
          <Card key={s.id} className="bg-gray-900 border-gray-800 hover:border-gray-700 transition-colors">
            <CardContent className="p-5">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-3">
                  <span className="text-2xl mt-0.5">{TEMPLATE_ICONS[s.template] ?? "📊"}</span>
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="font-semibold text-white">{s.name}</p>
                      <Badge className={cn("text-[10px] px-2 py-0 border", STATUS_COLORS[s.status])}>
                        {s.status}
                      </Badge>
                    </div>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {s.symbol} · {templates?.[s.template]?.name ?? s.template}
                    </p>
                    <div className="flex items-center gap-4 mt-3">
                      <div>
                        <p className="text-[10px] text-gray-500 uppercase">Total P&L</p>
                        <p className={cn("text-sm font-semibold", s.total_pnl >= 0 ? "text-green-400" : "text-red-400")}>
                          {s.total_pnl >= 0 ? "+" : ""}${s.total_pnl?.toFixed(2)}
                        </p>
                      </div>
                      <div>
                        <p className="text-[10px] text-gray-500 uppercase">Win Rate</p>
                        <p className="text-sm font-semibold text-white">{s.win_rate}%</p>
                      </div>
                      <div>
                        <p className="text-[10px] text-gray-500 uppercase">Trades</p>
                        <p className="text-sm font-semibold text-white">{s.total_trades}</p>
                      </div>
                      <div>
                        <p className="text-[10px] text-gray-500 uppercase">Stop Loss</p>
                        <p className="text-sm font-semibold text-red-400">{String(s.risk_config?.stop_loss_pct ?? "—")}%</p>
                      </div>
                      <div>
                        <p className="text-[10px] text-gray-500 uppercase">Take Profit</p>
                        <p className="text-sm font-semibold text-green-400">{String(s.risk_config?.take_profit_pct ?? "—")}%</p>
                      </div>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Button size="sm" variant="outline" onClick={() => signalMut.mutate(s.id)}
                    disabled={signalMut.isPending}
                    className="border-gray-700 text-gray-300 hover:text-white hover:border-blue-500 gap-1.5 text-xs">
                    <BarChart3 className="w-3.5 h-3.5" /> Signal
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => runNowMut.mutate(s.id)}
                    disabled={runNowMut.isPending}
                    className="border-gray-700 text-gray-300 hover:text-white hover:border-purple-500 gap-1.5 text-xs">
                    <Timer className="w-3.5 h-3.5" /> Run Now
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => executeMut.mutate(s.id)}
                    disabled={executeMut.isPending || s.status !== "active"}
                    className="border-gray-700 text-gray-300 hover:text-white hover:border-green-500 gap-1.5 text-xs">
                    <Zap className="w-3.5 h-3.5" /> Execute
                  </Button>
                  <Button size="sm" variant="outline"
                    onClick={() => toggleMut.mutate({ id: s.id, status: s.status === "active" ? "paused" : "active" })}
                    className="border-gray-700 text-gray-300 hover:text-white gap-1.5 text-xs">
                    {s.status === "active" ? <><Pause className="w-3.5 h-3.5" /> Pause</> : <><Play className="w-3.5 h-3.5" /> Activate</>}
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => deleteMut.mutate(s.id)}
                    className="border-gray-700 text-red-400 hover:text-red-300 hover:border-red-500/50">
                    <Trash2 className="w-3.5 h-3.5" />
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Signal Result Dialog */}
      <Dialog open={!!signalResult} onOpenChange={() => setSignalResult(null)}>
        <DialogContent className="bg-gray-900 border-gray-800 text-white max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Settings2 className="w-4 h-4" /> Signal Result — {signalResult?.strategy_name}
            </DialogTitle>
          </DialogHeader>
          {signalResult && (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <div className={cn("px-4 py-2 rounded-xl font-bold text-lg",
                  signalResult.signal === "buy" ? "bg-green-500/20 text-green-400" :
                  signalResult.signal === "sell" ? "bg-red-500/20 text-red-400" :
                  "bg-gray-700 text-gray-300")}>
                  {signalResult.signal.toUpperCase()}
                </div>
                <div>
                  <p className="text-sm text-gray-300">Confidence</p>
                  <p className="font-bold text-white">{(signalResult.confidence * 100).toFixed(0)}%</p>
                </div>
              </div>
              <Separator className="bg-gray-800" />
              <div>
                <p className="text-xs text-gray-400 mb-2 uppercase font-medium">Indicators</p>
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(signalResult.indicators).map(([k, v]) => (
                    <div key={k} className="bg-gray-800 rounded-lg p-2.5">
                      <p className="text-[10px] text-gray-400 uppercase">{k.replace(/_/g, " ")}</p>
                      <p className="text-sm font-medium text-white">{typeof v === "number" ? v.toFixed(4) : String(v)}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Strategy Builder Dialog */}
      <Dialog open={showBuilder} onOpenChange={setShowBuilder}>
        <DialogContent className="bg-gray-900 border-gray-800 text-white max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Plus className="w-4 h-4" /> Build New Strategy
            </DialogTitle>
          </DialogHeader>
          {templates && <StrategyBuilder templates={templates} onClose={() => setShowBuilder(false)} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}
