"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Shield, Zap, CheckCircle2, AlertCircle, RefreshCw } from "lucide-react";
import { getGrokAutoTradeStatus, toggleGrokAutoTrade, setGrokMinConfidence } from "@/lib/api";

interface AutoTradeState {
  grok_auto_trade: boolean;
  grok_min_confidence: number;
  pending_setups: number;
  executed_setup_ids: string[];
}

export default function AutoTradePanel() {
  const [state, setState] = useState<AutoTradeState>({
    grok_auto_trade: false,
    grok_min_confidence: 70,
    pending_setups: 0,
    executed_setup_ids: [],
  });
  const [loading, setLoading]   = useState(false);
  const [toggling, setToggling] = useState(false);
  const [localConf, setLocalConf] = useState(70);

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const data = await getGrokAutoTradeStatus();
      setState(data);
      setLocalConf(data.grok_min_confidence ?? 70);
    } catch {
      // silently ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 15_000);
    return () => clearInterval(interval);
  }, []);

  const handleToggle = async () => {
    const next = !state.grok_auto_trade;
    setToggling(true);
    try {
      await toggleGrokAutoTrade(next);
      setState((prev) => ({ ...prev, grok_auto_trade: next }));
    } catch {
      // revert on error
    } finally {
      setToggling(false);
    }
  };

  const handleConfidenceChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setLocalConf(Number(e.target.value));
  };

  const handleConfidenceCommit = async () => {
    try {
      await setGrokMinConfidence(localConf);
      setState((prev) => ({ ...prev, grok_min_confidence: localConf }));
    } catch {
      // ignore
    }
  };

  const isActive = state.grok_auto_trade;
  const confColor = localConf >= 80 ? "text-green-400" : localConf >= 65 ? "text-yellow-400" : "text-red-400";

  return (
    <Card className="border border-gray-800 bg-gray-900">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <Zap className="h-4 w-4 text-yellow-500" />
            Grok Auto-Trade
          </CardTitle>
          <div className="flex items-center gap-2">
            <Badge className={
              isActive
                ? "border border-green-500/50 text-green-400 bg-green-500/10 text-xs"
                : "border border-gray-700 text-gray-500 bg-transparent text-xs"
            }>
              {isActive ? "LIVE" : "OFF"}
            </Badge>
            <button
              onClick={fetchStatus}
              disabled={loading}
              className="p-1 text-gray-600 hover:text-gray-300 transition-colors"
            >
              <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Master toggle */}
        <div className="flex items-center justify-between rounded-lg border border-gray-800 bg-gray-800/40 p-3">
          <div className="flex items-center gap-2">
            <Shield className={`h-4 w-4 ${isActive ? "text-green-500" : "text-gray-600"}`} />
            <div>
              <p className="text-xs font-medium text-gray-300">Auto-Execute Setups</p>
              <p className="text-xs text-gray-500 mt-0.5">
                Grok trades winning setups automatically
              </p>
            </div>
          </div>
          <button
            onClick={handleToggle}
            disabled={toggling}
            className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none ${
              isActive ? "bg-green-600" : "bg-gray-700"
            } disabled:opacity-50`}
          >
            <span
              className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
                isActive ? "translate-x-4" : "translate-x-1"
              }`}
            />
          </button>
        </div>

        {/* Min confidence slider */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs text-gray-400">Min Confidence</p>
            <span className={`text-sm font-bold tabular-nums ${confColor}`}>
              {localConf}%
            </span>
          </div>
          <input
            type="range"
            min={50}
            max={95}
            step={5}
            value={localConf}
            onChange={handleConfidenceChange}
            onMouseUp={handleConfidenceCommit}
            onTouchEnd={handleConfidenceCommit}
            disabled={!isActive}
            className="w-full h-1.5 accent-yellow-500 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
          />
          <div className="flex justify-between text-[10px] text-gray-600">
            <span>50% aggressive</span>
            <span>95% selective</span>
          </div>
        </div>

        {/* Status grid */}
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-md bg-gray-800/60 p-2 text-center">
            <p className="text-[10px] text-gray-500">Pending Setups</p>
            <p className={`text-lg font-bold tabular-nums ${
              state.pending_setups > 0 ? "text-yellow-400" : "text-gray-600"
            }`}>
              {state.pending_setups}
            </p>
          </div>
          <div className="rounded-md bg-gray-800/60 p-2 text-center">
            <p className="text-[10px] text-gray-500">Executed Today</p>
            <p className="text-lg font-bold tabular-nums text-blue-400">
              {state.executed_setup_ids.length}
            </p>
          </div>
        </div>

        {/* SL/TP monitor status */}
        <div className="flex items-center gap-2 rounded-lg bg-green-500/5 border border-green-500/20 p-2">
          <CheckCircle2 className="h-3.5 w-3.5 text-green-500 shrink-0" />
          <p className="text-xs text-gray-400">
            SL/TP monitor checks all open trades every 30 s
          </p>
        </div>

        {isActive && (
          <div className="flex items-start gap-2 rounded-lg border border-yellow-500/30 bg-yellow-500/5 p-2">
            <AlertCircle className="h-3.5 w-3.5 text-yellow-500 shrink-0 mt-0.5" />
            <p className="text-xs text-yellow-400/90">
              Auto-trade ON — Grok places paper orders when setups meet threshold.
              Every trade has mandatory SL.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
