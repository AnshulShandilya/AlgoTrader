"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getGrokLastScan, getUniverseMovers, getGrokCandidates } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  Newspaper, TrendingUp, TrendingDown, Zap, Building2,
  RefreshCw, ChevronDown, ChevronUp, Radio, AlertTriangle,
  ExternalLink, BarChart3, Globe, Filter
} from "lucide-react";

// ─── types ───────────────────────────────────────────────────────────────────

interface ContractEvent {
  symbol: string;
  company: string;
  event: string;
  event_type: string;
  counterparty: string;
  value_gbp_m: number | null;
  announced_at: string;
  trading_implication: string;
  source: string;
}

interface SetupSentiment {
  score?: number;
  volume?: string;
  x_trend?: string;
  reddit_trend?: string;
  credible_account_stance?: string;
}

interface ContractOrPartnership {
  type?: string;
  counterparty?: string;
  value_gbp_m?: number | null;
  duration_years?: number | null;
  notes?: string;
}

interface GrokSetup {
  id: string;
  symbol: string;
  direction: string;
  asset_class: string;
  catalyst: string;
  catalyst_strength: string;
  contract_or_partnership: ContractOrPartnership;
  sentiment: SetupSentiment;
  entry_price: number;
  stop_price: number;
  target_price: number;
  r_r_ratio: number;
  confidence: number;
  sources: string[];
}

interface UniverseMover {
  symbol: string;
  asset_class: string;
  price: number;
  price_pence: number | null;
  pct_change: number;
  vol_ratio: number;
  rsi: number;
  direction: string;
  is_penny: boolean;
  is_boom: boolean;
}

// ─── helpers ─────────────────────────────────────────────────────────────────

function SentimentBadge({ score }: { score: number }) {
  if (score > 0.3)
    return <span className="px-1.5 py-0.5 text-[10px] rounded bg-green-500/20 text-green-400 font-medium">Bullish</span>;
  if (score < -0.3)
    return <span className="px-1.5 py-0.5 text-[10px] rounded bg-red-500/20 text-red-400 font-medium">Bearish</span>;
  return <span className="px-1.5 py-0.5 text-[10px] rounded bg-gray-700 text-gray-400 font-medium">Neutral</span>;
}

function BoomBadge({ pct, vol }: { pct: number; vol: number }) {
  const big = Math.abs(pct) >= 5;
  return (
    <span className={cn(
      "px-1.5 py-0.5 text-[10px] rounded font-bold inline-flex items-center gap-0.5",
      pct > 0 ? "bg-green-500/25 text-green-300" : "bg-red-500/25 text-red-300"
    )}>
      <Zap className="w-2.5 h-2.5" />
      {pct > 0 ? "+" : ""}{pct.toFixed(1)}% {big ? "🚀" : ""}
    </span>
  );
}

function PennyBadge() {
  return (
    <span className="px-1.5 py-0.5 text-[10px] rounded bg-yellow-500/20 text-yellow-400 font-medium">
      Penny
    </span>
  );
}

function implicationColor(imp: string) {
  if (imp === "buy_watch") return "text-green-400";
  if (imp === "sell_watch") return "text-red-400";
  return "text-gray-400";
}

interface GrokCandidate {
  symbol: string;
  asset_class: string;
  price: number;
  price_pence: number | null;
  pct_change: number;
  vol_ratio: number;
  rsi: number;
  rsi_rising: boolean;
  above_ma50: boolean;
  at_20d_extreme: boolean;
  gap_pct: number;
  direction: string;
  is_penny: boolean;
  score: number;
  score_breakdown: Record<string, number>;
}

function ScoreMeter({ score }: { score: number }) {
  const pct = Math.min(100, score);
  const color = score >= 70 ? "bg-green-500" : score >= 55 ? "bg-yellow-500" : "bg-blue-500";
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-gray-400">{score}</span>
    </div>
  );
}

function CandidateRow({ c }: { c: GrokCandidate }) {
  const displayPrice = c.asset_class === "uk_stock" && c.price_pence != null
    ? `${c.price_pence}p`
    : c.price < 1 ? `$${c.price.toFixed(4)}` : `$${c.price.toFixed(2)}`;

  const signals = Object.entries(c.score_breakdown)
    .filter(([, v]) => v > 0)
    .map(([k]) => k.replace("_", " "));

  return (
    <div className="py-2 border-b border-gray-800 last:border-0">
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono font-bold text-white w-20 shrink-0">{c.symbol}</span>
        <BoomBadge pct={c.pct_change} vol={c.vol_ratio} />
        {c.is_penny && <PennyBadge />}
        <span className="ml-auto text-[10px] text-gray-500 shrink-0">{displayPrice}</span>
        <ScoreMeter score={c.score} />
      </div>
      <div className="flex items-center gap-1 mt-1 flex-wrap">
        <span className="text-[10px] text-gray-500">Vol {c.vol_ratio.toFixed(1)}x</span>
        <span className="text-[10px] text-gray-600">·</span>
        <span className="text-[10px] text-gray-500">RSI {c.rsi.toFixed(0)}{c.rsi_rising ? "↑" : "↓"}</span>
        {c.above_ma50 && <span className="text-[10px] text-blue-500">above MA50</span>}
        {c.at_20d_extreme && <span className="text-[10px] text-yellow-500">20d extreme</span>}
        {Math.abs(c.gap_pct) >= 1 && (
          <span className={cn("text-[10px]", c.gap_pct > 0 ? "text-green-500" : "text-red-500")}>
            gap {c.gap_pct > 0 ? "+" : ""}{c.gap_pct.toFixed(1)}%
          </span>
        )}
      </div>
    </div>
  );
}

// ─── sub-panels ──────────────────────────────────────────────────────────────

function NewsSetupRow({ setup }: { setup: GrokSetup }) {
  const [open, setOpen] = useState(false);
  const sent: SetupSentiment = setup.sentiment || {};
  const cop: ContractOrPartnership = setup.contract_or_partnership || {};

  return (
    <div className="border border-gray-800 rounded-lg overflow-hidden mb-2">
      <button
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-800/50 transition-colors text-left"
        onClick={() => setOpen(o => !o)}
      >
        <span className={cn(
          "w-16 text-xs font-bold shrink-0",
          setup.direction === "long" ? "text-green-400" : "text-red-400"
        )}>
          {setup.symbol}
        </span>
        <span className={cn(
          "text-[10px] px-1.5 py-0.5 rounded shrink-0",
          setup.direction === "long" ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"
        )}>
          {setup.direction.toUpperCase()}
        </span>
        <span className="text-[11px] text-gray-400 truncate flex-1">{setup.catalyst}</span>
        <span className="text-[10px] text-gray-500 shrink-0">R:{setup.r_r_ratio.toFixed(1)}</span>
        <SentimentBadge score={typeof sent.score === "number" ? sent.score : 0} />
        {open ? <ChevronUp className="w-3 h-3 text-gray-600 shrink-0" /> : <ChevronDown className="w-3 h-3 text-gray-600 shrink-0" />}
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-2 bg-gray-900/50">
          <div className="grid grid-cols-3 gap-2 pt-2 text-center">
            <div>
              <p className="text-[10px] text-gray-500">Entry</p>
              <p className="text-xs font-mono text-white">{setup.entry_price}</p>
            </div>
            <div>
              <p className="text-[10px] text-gray-500">Stop</p>
              <p className="text-xs font-mono text-red-400">{setup.stop_price}</p>
            </div>
            <div>
              <p className="text-[10px] text-gray-500">Target</p>
              <p className="text-xs font-mono text-green-400">{setup.target_price}</p>
            </div>
          </div>

          {/* Sentiment */}
          {(sent?.x_trend || sent?.reddit_trend) && (
            <div className="bg-gray-800/60 rounded p-2 space-y-1">
              <p className="text-[10px] text-gray-500 font-medium uppercase tracking-wide">Sentiment</p>
              {sent.x_trend && sent.x_trend !== "no signal" && (
                <p className="text-[11px] text-gray-300">𝕏: {sent.x_trend}</p>
              )}
              {sent.reddit_trend && sent.reddit_trend !== "no signal" && (
                <p className="text-[11px] text-gray-300">Reddit: {sent.reddit_trend}</p>
              )}
            </div>
          )}

          {/* Contract */}
          {cop.type && cop.type !== "null" && (
            <div className="bg-blue-900/20 border border-blue-800/40 rounded p-2">
              <p className="text-[10px] text-blue-400 font-medium uppercase tracking-wide">Contract / Partnership</p>
              <p className="text-[11px] text-gray-300 mt-0.5">
                {cop.type.replace("_", " ")} with <span className="text-white font-medium">{cop.counterparty ?? "?"}</span>
                {cop.value_gbp_m != null ? <span className="text-yellow-400"> · £{cop.value_gbp_m}m</span> : ""}
              </p>
              {cop.notes && <p className="text-[10px] text-gray-400 mt-0.5">{cop.notes}</p>}
            </div>
          )}

          {setup.sources?.[0] && (
            <a href={setup.sources[0]} target="_blank" rel="noopener noreferrer"
               className="text-[10px] text-blue-500 hover:text-blue-400 flex items-center gap-1">
              <ExternalLink className="w-2.5 h-2.5" /> Source
            </a>
          )}
        </div>
      )}
    </div>
  );
}


function ContractRow({ item }: { item: ContractEvent }) {
  return (
    <div className="border border-gray-800 rounded-lg px-3 py-2 mb-2 space-y-1">
      <div className="flex items-center gap-2">
        <Building2 className="w-3.5 h-3.5 text-blue-400 shrink-0" />
        <span className="text-xs font-bold text-white">{item.symbol}</span>
        <span className="text-[10px] text-gray-400 truncate flex-1">{item.company}</span>
        <span className={cn("text-[10px] font-medium shrink-0", implicationColor(item.trading_implication))}>
          {item.trading_implication === "buy_watch" ? "👀 BUY WATCH" :
           item.trading_implication === "sell_watch" ? "⚠ SELL WATCH" : "Monitor"}
        </span>
      </div>
      <p className="text-[11px] text-gray-300">{item.event}</p>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-gray-500">
          {item.event_type.replace(/_/g, " ")} · {item.counterparty}
        </span>
        {item.value_gbp_m && (
          <span className="text-[10px] text-yellow-400 font-medium">£{item.value_gbp_m}m</span>
        )}
        {item.source && (
          <a href={item.source} target="_blank" rel="noopener noreferrer"
             className="text-[10px] text-blue-500 hover:text-blue-400 flex items-center gap-0.5">
            <ExternalLink className="w-2.5 h-2.5" /> Source
          </a>
        )}
      </div>
    </div>
  );
}


function MoverRow({ item }: { item: UniverseMover }) {
  const displayPrice = item.asset_class === "uk_stock" && item.price_pence != null
    ? `${item.price_pence}p`
    : `$${item.price.toFixed(item.price < 1 ? 4 : 2)}`;

  return (
    <div className="flex items-center gap-2 py-1.5 border-b border-gray-800 last:border-0">
      <span className="text-xs font-mono text-white w-20 shrink-0">{item.symbol}</span>
      <span className="text-[11px] text-gray-400 w-16 shrink-0">{displayPrice}</span>
      <BoomBadge pct={item.pct_change} vol={item.vol_ratio} />
      {item.is_penny && <PennyBadge />}
      <span className="text-[10px] text-gray-500 ml-auto shrink-0">Vol {item.vol_ratio.toFixed(1)}x</span>
    </div>
  );
}


// ─── main component ───────────────────────────────────────────────────────────

export default function NewsIntelPanel() {
  const [tab, setTab]         = useState<"candidates" | "news" | "contracts" | "boom" | "penny">("candidates");
  const [collapsed, setCollapsed] = useState(false);

  const { data: scanData, isLoading: scanLoading, refetch: refetchScan, dataUpdatedAt: scanUpdated } =
    useQuery({ queryKey: ["grokLastScan"], queryFn: getGrokLastScan, refetchInterval: 60_000 });

  const { data: candidateData, isLoading: candidateLoading, refetch: refetchCandidates } =
    useQuery({ queryKey: ["grokCandidates"], queryFn: getGrokCandidates, refetchInterval: 120_000 });

  const { data: boomData, isLoading: boomLoading, refetch: refetchBoom } =
    useQuery({ queryKey: ["universeBoom"], queryFn: () => getUniverseMovers("boom", 40), refetchInterval: 120_000 });

  const { data: pennyData, isLoading: pennyLoading } =
    useQuery({ queryKey: ["universePenny"], queryFn: () => getUniverseMovers("penny", 40), refetchInterval: 120_000 });

  const setups:      GrokSetup[]       = scanData?.setups            || [];
  const contracts:   ContractEvent[]   = scanData?.contract_watchlist || [];
  const candidates:  GrokCandidate[]   = candidateData?.candidates    || [];
  const boomItems:   UniverseMover[]   = boomData?.items              || [];
  const pennyItems:  UniverseMover[]   = pennyData?.items             || [];

  const lastUpdated = scanUpdated ? new Date(scanUpdated).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "—";

  const tabs = [
    { id: "candidates", label: "Queue",     count: candidates.length, icon: Filter     },
    { id: "news",       label: "News",      count: setups.length,     icon: Newspaper  },
    { id: "contracts",  label: "Contracts", count: contracts.length,  icon: Building2  },
    { id: "boom",       label: "Boom 🚀",   count: boomItems.length,  icon: TrendingUp },
    { id: "penny",      label: "Penny",     count: pennyItems.length, icon: BarChart3  },
  ] as const;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-800">
        <Radio className="w-4 h-4 text-blue-400 animate-pulse" />
        <span className="text-sm font-semibold text-white flex-1">Market Intelligence</span>
        <span className="text-[10px] text-gray-500">{lastUpdated}</span>
        <button
          onClick={() => { refetchScan(); refetchBoom(); }}
          className="p-1 hover:bg-gray-800 rounded transition-colors"
          title="Refresh"
        >
          <RefreshCw className="w-3.5 h-3.5 text-gray-400" />
        </button>
        <button onClick={() => setCollapsed(c => !c)} className="p-1 hover:bg-gray-800 rounded transition-colors">
          {collapsed ? <ChevronDown className="w-3.5 h-3.5 text-gray-400" /> : <ChevronUp className="w-3.5 h-3.5 text-gray-400" />}
        </button>
      </div>

      {!collapsed && (
        <>
          {/* Sentiment summary banner */}
          {scanData?.sentiment_summary && (
            <div className="px-4 py-2 bg-blue-950/30 border-b border-blue-900/30 text-[11px] text-blue-300">
              <Globe className="w-3 h-3 inline mr-1 text-blue-400" />
              {scanData.sentiment_summary}
            </div>
          )}

          {/* Session overview */}
          {scanData?.session_overview && (
            <div className="px-4 py-2 bg-gray-800/30 border-b border-gray-800 text-[11px] text-gray-400">
              {scanData.session_overview}
            </div>
          )}

          {/* Tabs */}
          <div className="flex border-b border-gray-800">
            {tabs.map(t => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={cn(
                  "flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-medium transition-colors",
                  tab === t.id
                    ? "text-blue-400 border-b-2 border-blue-400 bg-blue-950/20"
                    : "text-gray-500 hover:text-gray-300"
                )}
              >
                <t.icon className="w-3 h-3" />
                {t.label}
                {t.count > 0 && (
                  <span className={cn(
                    "px-1.5 py-0.5 rounded-full text-[9px] font-bold",
                    tab === t.id ? "bg-blue-500/30 text-blue-300" : "bg-gray-700 text-gray-400"
                  )}>
                    {t.count}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto p-3 min-h-0">

            {/* CANDIDATES TAB */}
            {tab === "candidates" && (
              <div>
                {candidateLoading && (
                  <div className="text-center py-8 text-gray-500 text-sm">Running 4-layer filter…</div>
                )}
                {!candidateLoading && candidates.length === 0 && (
                  <div className="text-center py-8 space-y-2">
                    <Filter className="w-8 h-8 text-gray-700 mx-auto" />
                    <p className="text-sm text-gray-500">No candidates yet</p>
                    <p className="text-xs text-gray-600">Universe screener runs every 60 min from 07:00 BST</p>
                    <p className="text-xs text-gray-600">Stocks must score ≥ 40/100 to qualify for Grok</p>
                  </div>
                )}
                {candidates.length > 0 && (
                  <div>
                    <p className="text-[10px] text-gray-600 mb-2 uppercase tracking-wide">
                      {candidates.length} stocks passed 4-layer filter — queued for Grok scan
                    </p>
                    <div className="flex gap-3 mb-3 flex-wrap">
                      {["uk_stock", "us_stock", "crypto", "commodity"].map(ac => {
                        const n = candidates.filter(c => c.asset_class === ac).length;
                        if (!n) return null;
                        return (
                          <span key={ac} className="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-gray-400">
                            {ac.replace("_", " ")}: {n}
                          </span>
                        );
                      })}
                    </div>
                    {candidates.map((c, i) => <CandidateRow key={i} c={c} />)}
                  </div>
                )}
              </div>
            )}

            {/* NEWS TAB */}
            {tab === "news" && (
              <div>
                {scanLoading && (
                  <div className="text-center py-8 text-gray-500 text-sm">Scanning news…</div>
                )}
                {!scanLoading && setups.length === 0 && (
                  <div className="text-center py-8 space-y-2">
                    <Newspaper className="w-8 h-8 text-gray-700 mx-auto" />
                    <p className="text-sm text-gray-500">No trade setups found</p>
                    <p className="text-xs text-gray-600">{scanData?.session_overview || "Trigger a Grok scan to populate news"}</p>
                  </div>
                )}
                {setups.map(s => <NewsSetupRow key={s.id} setup={s} />)}
              </div>
            )}

            {/* CONTRACTS TAB */}
            {tab === "contracts" && (
              <div>
                {scanLoading && (
                  <div className="text-center py-8 text-gray-500 text-sm">Checking tenders & contracts…</div>
                )}
                {!scanLoading && contracts.length === 0 && (
                  <div className="text-center py-8 space-y-2">
                    <Building2 className="w-8 h-8 text-gray-700 mx-auto" />
                    <p className="text-sm text-gray-500">No contract events today</p>
                    <p className="text-xs text-gray-600">Grok searches Contracts Finder, RNS, and Find a Tender on each scan</p>
                  </div>
                )}
                {contracts.map((c, i) => <ContractRow key={i} item={c} />)}
              </div>
            )}

            {/* BOOM TAB */}
            {tab === "boom" && (
              <div>
                {boomLoading && (
                  <div className="text-center py-8 text-gray-500 text-sm">Scanning universe…</div>
                )}
                {!boomLoading && boomItems.length === 0 && (
                  <div className="text-center py-8 space-y-2">
                    <TrendingUp className="w-8 h-8 text-gray-700 mx-auto" />
                    <p className="text-sm text-gray-500">No boom stocks right now</p>
                    <p className="text-xs text-gray-600">Boom = ≥3% move + ≥1.5× volume vs 20d avg</p>
                  </div>
                )}
                {boomItems.length > 0 && (
                  <div>
                    <p className="text-[10px] text-gray-600 mb-2 uppercase tracking-wide">
                      {boomItems.length} stocks with +3%+ move & high volume today
                    </p>
                    {boomItems.map((item, i) => <MoverRow key={i} item={item} />)}
                  </div>
                )}
              </div>
            )}

            {/* PENNY TAB */}
            {tab === "penny" && (
              <div>
                {pennyLoading && (
                  <div className="text-center py-8 text-gray-500 text-sm">Scanning penny stocks…</div>
                )}
                {!pennyLoading && pennyItems.length === 0 && (
                  <div className="text-center py-8 space-y-2">
                    <BarChart3 className="w-8 h-8 text-gray-700 mx-auto" />
                    <p className="text-sm text-gray-500">No active penny movers</p>
                    <p className="text-xs text-gray-600">UK: under 100p · US: under $5 · with unusual volume</p>
                  </div>
                )}
                {pennyItems.length > 0 && (
                  <div>
                    <p className="text-[10px] text-gray-600 mb-2 uppercase tracking-wide">
                      {pennyItems.length} penny stocks with unusual activity
                    </p>
                    {pennyItems.map((item, i) => <MoverRow key={i} item={item} />)}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="px-4 py-2 border-t border-gray-800 flex items-center justify-between">
            <span className="text-[10px] text-gray-600">
              Sources: LSE RNS · FT · Reuters · X · Reddit · Contracts Finder
            </span>
            <span className={cn(
              "text-[10px] font-medium",
              scanData?.ok ? "text-green-500" : "text-yellow-500"
            )}>
              {scanData?.ok ? "● Live" : scanData?.error ? "● " + scanData.error : "● Pending scan"}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
