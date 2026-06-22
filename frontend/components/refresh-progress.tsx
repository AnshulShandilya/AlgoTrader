"use client";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { CheckCircle2, X, Loader2 } from "lucide-react";

// ── Types ──────────────────────────────────────────────────────────────────────

export interface RefreshPhase {
  at: number;      // seconds elapsed when this phase becomes active
  message: string;
}

export interface RefreshConfig {
  label: string;
  estimatedSeconds: number;
  phases: RefreshPhase[];
}

// ── Preset configs for every long-running operation ───────────────────────────

export const REFRESH_CONFIGS = {
  universe_scan: {
    label: "Universe Scan",
    estimatedSeconds: 65,
    phases: [
      { at: 0,  message: "Loading ticker universe (770+ assets)…" },
      { at: 8,  message: "Fetching price history from Yahoo Finance…" },
      { at: 20, message: "Computing RSI, MA50 & volume ratios…" },
      { at: 38, message: "Filtering candidates through 4-layer funnel…" },
      { at: 52, message: "Scoring and ranking active setups…" },
      { at: 60, message: "Saving results to in-memory cache…" },
    ],
  } satisfies RefreshConfig,

  scanner_run: {
    label: "Market Scanner",
    estimatedSeconds: 35,
    phases: [
      { at: 0,  message: "Connecting to Alpaca data feed…" },
      { at: 5,  message: "Fetching 5-min bars for universe…" },
      { at: 18, message: "Computing 20-indicator composite scores…" },
      { at: 28, message: "Ranking top candidates…" },
      { at: 32, message: "Deploying auto scalping strategies…" },
    ],
  } satisfies RefreshConfig,

  autopilot: {
    label: "AutoPilot Scan",
    estimatedSeconds: 150,
    phases: [
      { at: 0,   message: "Loading strategy universe…" },
      { at: 12,  message: "Backtesting 1,300+ parameter combinations…" },
      { at: 50,  message: "Running walk-forward validation…" },
      { at: 90,  message: "Computing DSR & Monte Carlo significance…" },
      { at: 120, message: "Ranking by expectancy & profit factor…" },
      { at: 140, message: "Deploying top-ranked strategies…" },
    ],
  } satisfies RefreshConfig,

  grok_scan: {
    label: "Grok Intelligence",
    estimatedSeconds: 22,
    phases: [
      { at: 0,  message: "Connecting to xAI Grok API…" },
      { at: 5,  message: "Analysing current market conditions…" },
      { at: 12, message: "Generating trade recommendations…" },
      { at: 18, message: "Scoring setups by conviction…" },
    ],
  } satisfies RefreshConfig,

  backtest: {
    label: "Backtest",
    estimatedSeconds: 50,
    phases: [
      { at: 0,  message: "Loading historical price data…" },
      { at: 8,  message: "Simulating strategy on in-sample period…" },
      { at: 25, message: "Running out-of-sample validation…" },
      { at: 38, message: "Computing Sharpe, DSR & Monte Carlo…" },
      { at: 46, message: "Building walk-forward report…" },
    ],
  } satisfies RefreshConfig,

  walk_forward: {
    label: "Walk-Forward",
    estimatedSeconds: 90,
    phases: [
      { at: 0,  message: "Preparing walk-forward windows…" },
      { at: 10, message: "Running in-sample optimisations…" },
      { at: 40, message: "Validating out-of-sample windows…" },
      { at: 70, message: "Computing aggregate metrics…" },
      { at: 82, message: "Applying DSR & selection-bias correction…" },
    ],
  } satisfies RefreshConfig,
} as const;

export type RefreshConfigKey = keyof typeof REFRESH_CONFIGS;

// ── Component ──────────────────────────────────────────────────────────────────

interface RefreshProgressProps {
  isActive: boolean;
  config: RefreshConfig | RefreshConfigKey;
  onDismiss?: () => void;
}

export function RefreshProgress({ isActive, config: configProp, onDismiss }: RefreshProgressProps) {
  const config: RefreshConfig =
    typeof configProp === "string" ? REFRESH_CONFIGS[configProp] : configProp;

  const [elapsed, setElapsed] = useState(0);
  const [visible, setVisible] = useState(false);
  const [done, setDone]       = useState(false);
  const intervalRef           = useRef<ReturnType<typeof setInterval> | null>(null);
  const dismissTimerRef       = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (isActive) {
      // Reset and start
      if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
      setElapsed(0);
      setDone(false);
      setVisible(true);
      intervalRef.current = setInterval(() => {
        setElapsed(e => e + 0.5);
      }, 500);
    } else if (visible) {
      // Operation finished — fill bar and auto-dismiss
      if (intervalRef.current) clearInterval(intervalRef.current);
      setDone(true);
      dismissTimerRef.current = setTimeout(() => {
        setVisible(false);
        setElapsed(0);
        setDone(false);
      }, 3500);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [isActive]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!visible) return null;

  // Progress: cap at 95% while still running, jump to 100% on done
  const rawProgress  = elapsed / config.estimatedSeconds;
  const displayPct   = done ? 100 : Math.min(rawProgress * 100, 95);
  const remaining    = Math.max(0, Math.ceil(config.estimatedSeconds - elapsed));

  // Current phase message — find last phase whose `at` threshold has been crossed
  const currentPhase = [...config.phases]
    .filter(p => p.at <= elapsed)
    .at(-1);
  const message = done
    ? "Done — results updated."
    : (currentPhase?.message ?? config.phases[0].message);

  return (
    <div className={cn(
      "fixed bottom-6 right-6 z-50 w-80",
      "bg-gray-900 border border-gray-700/80 rounded-xl shadow-2xl shadow-black/50",
      "transition-all duration-300 ease-out",
      visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4 pointer-events-none"
    )}>
      {/* Header row */}
      <div className="flex items-center justify-between px-4 pt-3.5 pb-2">
        <div className="flex items-center gap-2">
          {done
            ? <CheckCircle2 className="w-4 h-4 text-green-400 shrink-0" />
            : <Loader2 className="w-4 h-4 text-blue-400 animate-spin shrink-0" />}
          <span className="text-sm font-semibold text-white">{config.label}</span>
        </div>

        <div className="flex items-center gap-2">
          {!done && (
            <span className="text-[11px] text-gray-500 tabular-nums">
              ~{remaining}s
            </span>
          )}
          {done && (
            <span className="text-[11px] text-green-400 font-medium">Complete</span>
          )}
          {onDismiss && (
            <button
              onClick={onDismiss}
              className="text-gray-600 hover:text-gray-400 transition-colors ml-1"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div className="px-4">
        <div className="w-full bg-gray-800 rounded-full h-1.5 overflow-hidden">
          <div
            className={cn(
              "h-1.5 rounded-full transition-all",
              done ? "bg-green-500 duration-500" : "bg-blue-500 duration-700"
            )}
            style={{ width: `${displayPct}%` }}
          />
        </div>
        {/* Percentage + time scale */}
        <div className="flex justify-between mt-1">
          <span className="text-[10px] text-gray-600 tabular-nums">{Math.round(displayPct)}%</span>
          {!done && (
            <span className="text-[10px] text-gray-600 tabular-nums">
              {Math.round(elapsed)}s / ~{config.estimatedSeconds}s
            </span>
          )}
        </div>
      </div>

      {/* Status message */}
      <div className="px-4 pb-4 pt-1">
        <p className={cn(
          "text-[12px] leading-snug transition-colors duration-300",
          done ? "text-green-400" : "text-gray-400"
        )}>
          {message}
        </p>
      </div>
    </div>
  );
}
