"use client";
import { useState, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { getSettings, saveSettings } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { toast } from "sonner";
import { Key, Shield, AlertTriangle, CheckCircle, Zap, List, Bot } from "lucide-react";
import { cn } from "@/lib/utils";

export default function SettingsPage() {
  const { data: current, isLoading } = useQuery({ queryKey: ["settings"], queryFn: getSettings });

  const [form, setForm] = useState({
    alpaca_api_key: "",
    alpaca_secret_key: "",
    paper_trading: true,
    binance_api_key: "",
    binance_secret_key: "",
    binance_testnet: true,
    max_portfolio_risk_pct: 2.0,
    max_drawdown_pct: 10.0,
    max_open_trades: 10,
    max_daily_trades: 20,
    default_position_size_pct: 2.0,
    risk_per_trade_pct: 1.0,
    daily_loss_limit_pct: 3.0,
    watchlist_symbols: "AAPL,TSLA,NVDA,SPY,QQQ",
    grok_auto_trade: false,
    grok_min_confidence: 70,
  });

  useEffect(() => {
    if (current) {
      setForm(f => ({
        ...f,
        paper_trading:              current.paper_trading ?? true,
        binance_testnet:            current.binance_testnet ?? true,
        max_portfolio_risk_pct:     current.max_portfolio_risk_pct,
        max_drawdown_pct:           current.max_drawdown_pct,
        max_open_trades:            current.max_open_trades,
        max_daily_trades:           current.max_daily_trades ?? 20,
        default_position_size_pct:  current.default_position_size_pct,
        risk_per_trade_pct:         current.risk_per_trade_pct ?? 1.0,
        daily_loss_limit_pct:       current.daily_loss_limit_pct ?? 3.0,
        watchlist_symbols:          current.watchlist_symbols ?? "AAPL,TSLA,NVDA,SPY,QQQ",
        grok_auto_trade:            current.grok_auto_trade ?? false,
        grok_min_confidence:        current.grok_min_confidence ?? 70,
      }));
    }
  }, [current]);

  const saveMut = useMutation({
    mutationFn: () => saveSettings({ ...form }),
    onSuccess: () => toast.success("Settings saved!"),
    onError: () => toast.error("Failed to save settings"),
  });

  const field = (label: string, key: keyof typeof form, type = "text", helpText?: string) => (
    <div>
      <Label className="text-sm text-gray-300">{label}</Label>
      <Input
        type={type}
        value={String(form[key])}
        onChange={e => setForm(f => ({
          ...f,
          [key]: type === "number" ? parseFloat(e.target.value) : e.target.value,
        }))}
        className="mt-1.5 bg-gray-800 border-gray-700 text-white focus:border-blue-500"
        step={type === "number" ? 0.1 : undefined}
        placeholder={type === "password" ? "••••••••••••••••" : undefined}
      />
      {helpText && <p className="text-xs text-gray-500 mt-1">{helpText}</p>}
    </div>
  );

  const toggle = (label: string, key: "paper_trading" | "binance_testnet" | "grok_auto_trade",
                  description: string, activeColor = "bg-green-600") => (
    <div className="flex items-center justify-between p-3 bg-gray-800 rounded-xl">
      <div>
        <p className="text-sm text-white font-medium">{label}</p>
        <p className="text-xs text-gray-400">{description}</p>
      </div>
      <button
        onClick={() => setForm(f => ({ ...f, [key]: !f[key] }))}
        className={`relative w-11 h-6 rounded-full transition-colors ${form[key] ? activeColor : "bg-gray-600"}`}
      >
        <div className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${form[key] ? "translate-x-6" : "translate-x-1"}`} />
      </button>
    </div>
  );

  if (isLoading) return <div className="p-6 text-gray-400 text-sm">Loading settings…</div>;

  const hasBinance = current?.binance_api_key;
  const hasAlpaca  = current?.alpaca_api_key;
  const isConnected = hasBinance || hasAlpaca;
  const activeBroker = hasBinance ? "Binance Testnet" : hasAlpaca ? "Alpaca Paper Trading" : null;

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-white">Settings</h1>
        <p className="text-sm text-gray-400 mt-0.5">Configure your broker connection and risk parameters</p>
      </div>

      {/* Connection Status */}
      <Card className={isConnected ? "bg-green-500/10 border-green-500/30" : "bg-yellow-500/10 border-yellow-500/30"}>
        <CardContent className="p-4 flex items-center gap-3">
          {isConnected
            ? <CheckCircle className="w-5 h-5 text-green-400 shrink-0" />
            : <AlertTriangle className="w-5 h-5 text-yellow-400 shrink-0" />}
          <div>
            <p className={`text-sm font-medium ${isConnected ? "text-green-300" : "text-yellow-300"}`}>
              {isConnected ? `${activeBroker} — Active` : "No broker API keys configured"}
            </p>
            {hasBinance && (
              <p className="text-xs text-green-400/70 mt-0.5">Key: {current.binance_api_key} · Testnet mode</p>
            )}
            {!hasBinance && hasAlpaca && (
              <p className="text-xs text-green-400/70 mt-0.5">Key: {current.alpaca_api_key}</p>
            )}
            {hasBinance && hasAlpaca && (
              <p className="text-xs text-blue-400/70 mt-0.5">Binance takes priority over Alpaca when both are set</p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* ── Grok Auto-Execution ── */}
      <Card className={cn(
        "border-2 transition-colors",
        form.grok_auto_trade
          ? "bg-yellow-500/8 border-yellow-500/40"
          : "bg-gray-900 border-gray-700"
      )}>
        <CardHeader>
          <CardTitle className="text-base text-white flex items-center gap-2">
            <Bot className="w-4 h-4 text-yellow-400" />
            Grok Auto-Execution
            <span className={cn(
              "ml-auto text-xs px-2 py-0.5 rounded-full font-normal border",
              form.grok_auto_trade
                ? "bg-yellow-500/20 text-yellow-300 border-yellow-500/40"
                : "bg-gray-700 text-gray-500 border-gray-600"
            )}>
              {form.grok_auto_trade ? "● ENABLED" : "○ DISABLED"}
            </span>
          </CardTitle>
          <CardDescription className="text-gray-400 text-xs">
            When enabled, Grok&apos;s high-confidence setups are automatically placed as paper trades. Without this ON, Grok only shows candidates — it never opens positions.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">

          {/* Master switch */}
          {toggle(
            "Auto-Execute Grok Setups",
            "grok_auto_trade",
            "Automatically place paper orders for qualifying Grok trade setups",
            "bg-yellow-500"
          )}

          {form.grok_auto_trade && (
            <>
              {/* Confidence threshold */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label className="text-sm text-gray-300">Minimum Confidence Threshold</Label>
                  <span className={cn(
                    "text-sm font-bold tabular-nums",
                    form.grok_min_confidence >= 80 ? "text-green-400"
                    : form.grok_min_confidence >= 65 ? "text-yellow-400"
                    : "text-orange-400"
                  )}>
                    {form.grok_min_confidence}%
                  </span>
                </div>
                <input
                  type="range"
                  min={50} max={95} step={5}
                  value={form.grok_min_confidence}
                  onChange={e => setForm(f => ({ ...f, grok_min_confidence: parseInt(e.target.value) }))}
                  className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-yellow-400"
                />
                <div className="flex justify-between text-[10px] text-gray-600">
                  <span>50% — more trades</span>
                  <span>70% — balanced</span>
                  <span>95% — very selective</span>
                </div>
                <p className="text-xs text-gray-500">
                  Grok also requires R:R ≥ 2.0 before executing. At {form.grok_min_confidence}%+
                  confidence, expect fewer but higher-quality setups.
                </p>
              </div>

              {/* What it means box */}
              <div className="bg-yellow-500/8 border border-yellow-500/20 rounded-xl p-3 space-y-1.5 text-xs">
                <p className="font-semibold text-yellow-300">What happens when a Grok setup fires:</p>
                <ul className="space-y-1 text-gray-400">
                  <li>✓ Grok runs its morning scan at 07:45 BST (LSE open) and 14:15 BST (NYSE open)</li>
                  <li>✓ Every 60 seconds, pending setups with conf ≥ {form.grok_min_confidence}% and R:R ≥ 2:1 are executed</li>
                  <li>✓ Position is sized at 2% equity risk, stop and target from Grok&apos;s analysis</li>
                  <li>✓ Trade appears in Trade Log with type (Scalp/Day/Swing) + logic breakdown</li>
                  <li>✓ SL/TP is monitored every 30 seconds by the auto-exec engine</li>
                </ul>
              </div>
            </>
          )}

          {!form.grok_auto_trade && (
            <div className="bg-gray-800/60 border border-gray-700 rounded-xl p-3 text-xs text-gray-500">
              <p>Grok scans are running and finding setups — they&apos;re just not being traded automatically. Enable the switch above to start auto-executing qualifying setups.</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Binance Testnet */}
      <Card className="bg-gray-900 border-yellow-500/40">
        <CardHeader>
          <CardTitle className="text-base text-white flex items-center gap-2">
            <Zap className="w-4 h-4 text-yellow-400" />
            Binance Testnet
            <span className="ml-auto text-xs bg-yellow-500/20 text-yellow-300 px-2 py-0.5 rounded-full font-normal">
              Recommended — Matches TradingView
            </span>
          </CardTitle>
          <CardDescription className="text-gray-400 text-xs">
            Prices match TradingView exactly (Binance feed). Zero delay. Testnet = no real money.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-3 text-xs text-yellow-300 space-y-1">
            <p className="font-medium">How to get Binance Testnet keys (free, 2 minutes):</p>
            <ol className="list-decimal list-inside space-y-0.5 text-yellow-300/80">
              <li>Go to <span className="text-yellow-200 font-medium">testnet.binance.vision</span></li>
              <li>Click <span className="font-medium">&quot;Log In with GitHub&quot;</span> (GitHub account required)</li>
              <li>After login, click <span className="font-medium">&quot;Generate HMAC_SHA256 Key&quot;</span></li>
              <li>Copy the <span className="font-medium">API Key</span> and <span className="font-medium">Secret Key</span> below</li>
              <li>Your testnet wallet starts with <span className="font-medium">1 BTC + 10,000 USDT</span></li>
            </ol>
          </div>
          {field("Binance Testnet API Key", "binance_api_key", "text", "From testnet.binance.vision → Generate HMAC Key")}
          {field("Binance Testnet Secret Key", "binance_secret_key", "password", "Your Binance testnet secret")}
          {toggle("Testnet Mode", "binance_testnet", "Keep ON for paper trading (no real money)")}
          {!form.binance_testnet && (
            <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-3 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
              <p className="text-xs text-red-300">
                <strong>WARNING:</strong> Live Binance mode uses real money. Only enable after thorough testnet testing.
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Alpaca API Keys */}
      <Card className="bg-gray-900 border-gray-800">
        <CardHeader>
          <CardTitle className="text-base text-white flex items-center gap-2">
            <Key className="w-4 h-4" /> Alpaca API Keys
            <span className="ml-auto text-xs bg-gray-700 text-gray-400 px-2 py-0.5 rounded-full font-normal">
              Fallback (stocks + crypto)
            </span>
          </CardTitle>
          <CardDescription className="text-gray-400 text-xs">
            Used only if no Binance keys are set. Get free keys at alpaca.markets → Paper Trading → API Keys.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {field("API Key", "alpaca_api_key", "text", "Starts with PK...")}
          {field("Secret Key", "alpaca_secret_key", "password", "Your Alpaca secret key")}
          {toggle("Paper Trading Mode", "paper_trading", "Simulated orders, no real money")}
        </CardContent>
      </Card>

      {/* Risk Settings */}
      <Card className="bg-gray-900 border-gray-800">
        <CardHeader>
          <CardTitle className="text-base text-white flex items-center gap-2">
            <Shield className="w-4 h-4" /> Risk Management
          </CardTitle>
          <CardDescription className="text-gray-400 text-xs">Global risk limits applied across all strategies</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            {field("Risk Per Trade %", "risk_per_trade_pct", "number", "% of equity risked per trade (used to size position from stop distance — default 1%)")}
            {field("Daily Loss Limit %", "daily_loss_limit_pct", "number", "Pause all strategies if today's realized P&L falls below this % of equity (default 3%)")}
            {field("Max Portfolio Risk %", "max_portfolio_risk_pct", "number", "Max % of portfolio at risk per trade")}
            {field("Max Drawdown %", "max_drawdown_pct", "number", "Bot halts if drawdown exceeds this")}
            {field("Max Open Trades", "max_open_trades", "number", "Maximum concurrent open positions")}
            {field("Max Trades Per Day", "max_daily_trades", "number", "Hard cap on new trades placed in a 24h UTC window")}
            {field("Default Position Size %", "default_position_size_pct", "number", "Default % of equity per trade")}
          </div>

          <div className="bg-orange-500/10 border border-orange-500/30 rounded-xl p-3 text-xs text-orange-300 space-y-1">
            <p className="font-medium">Stop Loss is mandatory on every trade</p>
            <p className="text-orange-300/80">Every BUY order must have a valid stop loss price before it is placed. If no SL can be computed (price = 0), the trade is blocked. SL defaults to 1.5% if not explicitly configured on the strategy.</p>
          </div>

          <div className="bg-gray-800 rounded-xl p-3 text-xs text-gray-400 grid grid-cols-4 gap-3">
            <div className="text-center">
              <p className="text-white font-semibold">{form.max_portfolio_risk_pct}%</p>
              <p>Max Risk/Trade</p>
            </div>
            <div className="text-center">
              <p className="text-white font-semibold">{form.max_drawdown_pct}%</p>
              <p>Kill Switch</p>
            </div>
            <div className="text-center">
              <p className="text-white font-semibold">{form.max_open_trades}</p>
              <p>Max Positions</p>
            </div>
            <div className="text-center">
              <p className="text-orange-300 font-semibold">{form.max_daily_trades}</p>
              <p>Daily Limit</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Watchlist */}
      <Card className="bg-gray-900 border-gray-800">
        <CardHeader>
          <CardTitle className="text-base text-white flex items-center gap-2">
            <List className="w-4 h-4" /> Dashboard Watchlist
          </CardTitle>
          <CardDescription className="text-gray-400 text-xs">
            Comma-separated symbols shown in the market watchlist on the dashboard
          </CardDescription>
        </CardHeader>
        <CardContent>
          {field("Watchlist Symbols", "watchlist_symbols", "text", "e.g. AAPL,TSLA,NVDA,BTC/USDT — max 10 symbols recommended")}
          <div className="flex flex-wrap gap-1.5 mt-2">
            {form.watchlist_symbols.split(",").filter(Boolean).map(s => (
              <span key={s} className="text-[10px] bg-gray-800 text-gray-300 px-2 py-0.5 rounded-full font-mono">
                {s.trim()}
              </span>
            ))}
          </div>
        </CardContent>
      </Card>

      <Separator className="bg-gray-800" />

      <Button
        onClick={() => saveMut.mutate()}
        disabled={saveMut.isPending}
        className="bg-blue-600 hover:bg-blue-700 w-full"
      >
        {saveMut.isPending ? "Saving…" : "Save Settings"}
      </Button>
    </div>
  );
}
