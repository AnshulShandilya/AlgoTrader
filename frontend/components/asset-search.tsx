"use client";
import { useState, useRef, useEffect } from "react";
import { Search, X } from "lucide-react";
import { cn } from "@/lib/utils";

// ── Asset database ────────────────────────────────────────────────────────────
// uk_status:
//   "binance"  → available on Binance Testnet (fully supported, no geo-restrictions)
//   "alpaca"   → available on Alpaca Paper (US stocks via paper trading from UK)
//   "lse"      → LSE-listed UK stock (backtesting via yfinance; live needs UK broker)
//   "yf_only"  → yfinance for backtesting only

const ASSETS = [
  // ── Crypto (Binance Testnet — best option for UK) ─────────────────────────
  { symbol: "BTC/USD",  name: "Bitcoin",        category: "Crypto",    uk_status: "binance" },
  { symbol: "ETH/USD",  name: "Ethereum",       category: "Crypto",    uk_status: "binance" },
  { symbol: "SOL/USD",  name: "Solana",         category: "Crypto",    uk_status: "binance" },
  { symbol: "BNB/USD",  name: "BNB",            category: "Crypto",    uk_status: "binance" },
  { symbol: "XRP/USD",  name: "Ripple",         category: "Crypto",    uk_status: "binance" },
  { symbol: "ADA/USD",  name: "Cardano",        category: "Crypto",    uk_status: "binance" },
  { symbol: "DOGE/USD", name: "Dogecoin",       category: "Crypto",    uk_status: "binance" },
  { symbol: "AVAX/USD", name: "Avalanche",      category: "Crypto",    uk_status: "binance" },
  { symbol: "LINK/USD", name: "Chainlink",      category: "Crypto",    uk_status: "binance" },
  { symbol: "LTC/USD",  name: "Litecoin",       category: "Crypto",    uk_status: "binance" },
  { symbol: "DOT/USD",  name: "Polkadot",       category: "Crypto",    uk_status: "binance" },
  { symbol: "MATIC/USD",name: "Polygon",        category: "Crypto",    uk_status: "binance" },
  { symbol: "UNI/USD",  name: "Uniswap",        category: "Crypto",    uk_status: "binance" },
  { symbol: "ATOM/USD", name: "Cosmos",         category: "Crypto",    uk_status: "binance" },

  // ── US Stocks (Alpaca Paper — accessible from UK for paper trading) ────────
  { symbol: "AAPL",  name: "Apple Inc.",              category: "US Stock", uk_status: "alpaca" },
  { symbol: "MSFT",  name: "Microsoft",               category: "US Stock", uk_status: "alpaca" },
  { symbol: "NVDA",  name: "NVIDIA",                  category: "US Stock", uk_status: "alpaca" },
  { symbol: "TSLA",  name: "Tesla",                   category: "US Stock", uk_status: "alpaca" },
  { symbol: "AMZN",  name: "Amazon",                  category: "US Stock", uk_status: "alpaca" },
  { symbol: "GOOGL", name: "Alphabet (Google)",       category: "US Stock", uk_status: "alpaca" },
  { symbol: "META",  name: "Meta (Facebook)",         category: "US Stock", uk_status: "alpaca" },
  { symbol: "AMD",   name: "Advanced Micro Devices",  category: "US Stock", uk_status: "alpaca" },
  { symbol: "NFLX",  name: "Netflix",                 category: "US Stock", uk_status: "alpaca" },
  { symbol: "COIN",  name: "Coinbase",                category: "US Stock", uk_status: "alpaca" },
  { symbol: "MSTR",  name: "MicroStrategy",           category: "US Stock", uk_status: "alpaca" },
  { symbol: "PLTR",  name: "Palantir",                category: "US Stock", uk_status: "alpaca" },
  { symbol: "HOOD",  name: "Robinhood",               category: "US Stock", uk_status: "alpaca" },
  { symbol: "JPM",   name: "JPMorgan Chase",          category: "US Stock", uk_status: "alpaca" },
  { symbol: "GS",    name: "Goldman Sachs",           category: "US Stock", uk_status: "alpaca" },
  { symbol: "SPY",   name: "S&P 500 ETF",             category: "US ETF",   uk_status: "alpaca" },
  { symbol: "QQQ",   name: "Nasdaq 100 ETF",          category: "US ETF",   uk_status: "alpaca" },
  { symbol: "GLD",   name: "Gold ETF",                category: "US ETF",   uk_status: "alpaca" },

  // ── UK Stocks — London Stock Exchange (.L suffix for yfinance) ────────────
  { symbol: "SHEL.L", name: "Shell PLC",             category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "AZN.L",  name: "AstraZeneca",           category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "HSBA.L", name: "HSBC Holdings",         category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "BP.L",   name: "BP PLC",                category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "BARC.L", name: "Barclays",              category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "LLOY.L", name: "Lloyds Banking Group",  category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "VOD.L",  name: "Vodafone Group",        category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "GLEN.L", name: "Glencore",              category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "RIO.L",  name: "Rio Tinto",             category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "ULVR.L", name: "Unilever",              category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "DGE.L",  name: "Diageo",                category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "LSEG.L", name: "London Stock Exchange Group", category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "GSK.L",  name: "GSK PLC",               category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "RKT.L",  name: "Reckitt Benckiser",     category: "UK Stock (LSE)", uk_status: "lse" },
  { symbol: "NG.L",   name: "National Grid",         category: "UK Stock (LSE)", uk_status: "lse" },
];

const UK_BADGE: Record<string, { label: string; color: string }> = {
  binance: { label: "Binance Testnet",   color: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30" },
  alpaca:  { label: "Alpaca Paper",      color: "bg-blue-500/20 text-blue-300 border-blue-500/30" },
  lse:     { label: "LSE — Backtest only", color: "bg-purple-500/20 text-purple-300 border-purple-500/30" },
  yf_only: { label: "Backtest only",     color: "bg-gray-500/20 text-gray-400 border-gray-600" },
};

const CATEGORY_ORDER = ["Crypto", "US Stock", "US ETF", "UK Stock (LSE)"];

type Asset = typeof ASSETS[0];

interface AssetSearchProps {
  value: string;
  onChange: (symbol: string) => void;
  placeholder?: string;
  label?: string;
}

export default function AssetSearch({ value, onChange, placeholder = "Search symbol or name…", label }: AssetSearchProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const q = query.toLowerCase();
  const filtered = q.length === 0
    ? ASSETS
    : ASSETS.filter(a =>
        a.symbol.toLowerCase().includes(q) ||
        a.name.toLowerCase().includes(q) ||
        a.category.toLowerCase().includes(q)
      );

  // Group by category in defined order
  const grouped: Record<string, Asset[]> = {};
  for (const cat of CATEGORY_ORDER) {
    const items = filtered.filter(a => a.category === cat);
    if (items.length) grouped[cat] = items;
  }

  const handleSelect = (asset: Asset) => {
    onChange(asset.symbol);
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={ref} className="relative">
      {label && <p className="text-xs text-gray-400 mb-1">{label}</p>}
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500 pointer-events-none" />
        <input
          type="text"
          value={open ? query : value}
          onChange={e => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          placeholder={open ? placeholder : value || placeholder}
          className="w-full pl-8 pr-8 h-8 bg-gray-800 border border-gray-700 rounded-md text-sm text-white focus:border-blue-500 focus:outline-none placeholder:text-gray-500"
        />
        {(value || query) && (
          <button
            onClick={() => { onChange(""); setQuery(""); setOpen(false); }}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-white"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {open && (
        <div className="absolute z-50 top-full mt-1 w-full min-w-[320px] bg-gray-900 border border-gray-700 rounded-xl shadow-2xl overflow-hidden">
          {/* UK note */}
          <div className="px-3 py-2 bg-blue-500/10 border-b border-gray-800 text-xs text-blue-300">
            <span className="font-medium">UK traders:</span> Crypto via Binance Testnet · US stocks via Alpaca Paper · LSE stocks for backtesting
          </div>

          <div className="max-h-72 overflow-y-auto">
            {Object.keys(grouped).length === 0 ? (
              <p className="text-xs text-gray-500 text-center py-6">No assets found for &quot;{query}&quot;</p>
            ) : (
              Object.entries(grouped).map(([cat, assets]) => (
                <div key={cat}>
                  <div className="px-3 py-1.5 text-xs font-semibold text-gray-500 uppercase tracking-wider bg-gray-950 sticky top-0">
                    {cat}
                  </div>
                  {assets.map(asset => {
                    const badge = UK_BADGE[asset.uk_status];
                    return (
                      <button
                        key={asset.symbol}
                        onClick={() => handleSelect(asset)}
                        className={cn(
                          "w-full flex items-center justify-between px-3 py-2 hover:bg-gray-800 transition-colors text-left",
                          value === asset.symbol && "bg-blue-600/20"
                        )}
                      >
                        <div>
                          <span className="text-sm font-mono font-semibold text-white">{asset.symbol}</span>
                          <span className="text-xs text-gray-400 ml-2">{asset.name}</span>
                        </div>
                        <span className={cn("text-xs px-1.5 py-0.5 rounded border shrink-0 ml-2", badge.color)}>
                          {badge.label}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>

          {/* Legend */}
          <div className="px-3 py-2 border-t border-gray-800 bg-gray-950 flex flex-wrap gap-2">
            {Object.entries(UK_BADGE).map(([key, b]) => (
              <span key={key} className={cn("text-xs px-1.5 py-0.5 rounded border", b.color)}>
                {b.label}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
