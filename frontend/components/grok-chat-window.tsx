"use client";
import { useEffect, useRef, useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  Brain, Search, Globe, MessageCircle, Zap, CheckCircle2, AlertTriangle,
  TrendingUp, TrendingDown, Minus, ExternalLink, Loader2, RefreshCw,
  BarChart2, ChevronDown, Info, StopCircle,
} from "lucide-react";

// ── Types ──────────────────────────────────────────────────────────────────
type MsgType =
  | "status"
  | "market_data"
  | "tool_call"
  | "tool_searching"
  | "tool_result"
  | "text_delta"
  | "thinking"
  | "source_found"
  | "decision"
  | "done"
  | "error";

interface ChatMessage {
  id: number;
  type: MsgType;
  ts: string;
  // status / text / error
  message?: string;
  // market_data
  symbol?: string;
  price?: number;
  rsi?: number;
  macd?: number;
  atr?: number;
  sma20?: number;
  sma50?: number;
  balance?: number;
  source?: string;
  // tool_call / tool_searching
  tool?: string;
  query?: string;
  // tool_result
  count?: number;
  sources?: string[];
  // source_found
  url?: string;
  title?: string;
  // thinking / text_delta
  text?: string;
  delta?: string;
  // decision
  decision?: string;
  confidence_score?: number;
  market_regime?: string;
  reasoning?: string;
  catalyst?: string;
  catalyst_source?: string;
  entry_price?: number;
  stop_loss?: number;
  take_profit?: number;
  risk_reward?: number;
  time_window?: string;
  invalidation?: string;
  usage?: { input_tokens: number; output_tokens: number };
}

// ── Helpers ────────────────────────────────────────────────────────────────
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function timeLabel(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return ""; }
}

function hostOf(url: string) {
  try { return new URL(url).hostname.replace("www.", ""); } catch { return url; }
}

const DECISION_STYLE: Record<string, { bg: string; text: string; border: string; icon: React.ReactNode }> = {
  BUY:  { bg: "bg-green-500/10", text: "text-green-300", border: "border-green-500/30", icon: <TrendingUp  className="w-4 h-4" /> },
  SELL: { bg: "bg-red-500/10",   text: "text-red-300",   border: "border-red-500/30",   icon: <TrendingDown className="w-4 h-4" /> },
  HOLD: { bg: "bg-gray-800",     text: "text-gray-300",  border: "border-gray-700",     icon: <Minus        className="w-4 h-4" /> },
};

const REGIME_COLOR: Record<string, string> = {
  uptrend:       "text-green-400",
  strong_uptrend:"text-green-300",
  downtrend:     "text-red-400",
  strong_downtrend: "text-red-300",
  consolidation: "text-yellow-400",
  range:         "text-yellow-400",
  high_volatility:"text-orange-400",
  unknown:       "text-gray-500",
};

// ── Individual message renderers ───────────────────────────────────────────
function StatusBubble({ msg }: { msg: ChatMessage }) {
  return (
    <div className="flex items-center gap-2 text-[11px] text-gray-500 py-0.5">
      <Info className="w-3 h-3 shrink-0" />
      <span>{msg.message}</span>
      <span className="ml-auto text-[10px]">{timeLabel(msg.ts)}</span>
    </div>
  );
}

function MarketDataBubble({ msg }: { msg: ChatMessage }) {
  const aboveSma20 = msg.price && msg.sma20 ? msg.price > msg.sma20 : null;
  return (
    <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <BarChart2 className="w-3.5 h-3.5 text-blue-400" />
        <span className="text-xs font-semibold text-blue-300">{msg.symbol} · Market Data</span>
        <span className="ml-auto text-[10px] text-gray-600">{timeLabel(msg.ts)}</span>
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        {[
          { label: "Price",   value: `$${msg.price?.toLocaleString()}`, hl: null },
          { label: "RSI(14)", value: msg.rsi?.toFixed(1),
            hl: msg.rsi ? msg.rsi > 70 ? "text-red-400" : msg.rsi < 30 ? "text-green-400" : "text-gray-300" : null },
          { label: "MACD",    value: msg.macd != null ? `${msg.macd >= 0 ? "+" : ""}${msg.macd?.toFixed(4)}` : "—",
            hl: msg.macd != null ? msg.macd >= 0 ? "text-green-400" : "text-red-400" : null },
          { label: "ATR",     value: msg.atr?.toFixed(2), hl: null },
          { label: "SMA20",   value: `$${msg.sma20?.toLocaleString()}`,
            hl: aboveSma20 === true ? "text-green-400" : aboveSma20 === false ? "text-red-400" : null },
          { label: "Balance", value: `$${msg.balance?.toLocaleString()}`, hl: "text-gray-300" },
        ].map(({ label, value, hl }) => (
          <div key={label} className="bg-gray-900/50 rounded px-1.5 py-1">
            <p className="text-[9px] text-gray-500 uppercase tracking-wide">{label}</p>
            <p className={cn("text-xs font-mono font-semibold", hl ?? "text-gray-300")}>{value ?? "—"}</p>
          </div>
        ))}
      </div>
      {msg.source && (
        <p className="text-[10px] text-gray-600">source: {msg.source}</p>
      )}
    </div>
  );
}

function ToolCallBubble({ msg }: { msg: ChatMessage }) {
  const isX = msg.tool === "x_search";
  return (
    <div className="flex items-start gap-2.5 py-1">
      <div className={cn(
        "shrink-0 w-6 h-6 rounded-full flex items-center justify-center mt-0.5",
        isX ? "bg-gray-800" : "bg-blue-500/10",
      )}>
        {isX
          ? <MessageCircle className="w-3 h-3 text-gray-300" />
          : <Globe className="w-3 h-3 text-blue-400" />}
      </div>
      <div className="flex-1 min-w-0">
        <span className={cn("text-[10px] font-semibold uppercase tracking-wide",
          isX ? "text-gray-400" : "text-blue-400"
        )}>
          {isX ? "X Search" : "Web Search"}
        </span>
        {msg.query && (
          <p className="text-[11px] text-gray-400 mt-0.5 font-mono bg-gray-800 rounded px-2 py-1 leading-relaxed">
            &ldquo;{msg.query}&rdquo;
          </p>
        )}
      </div>
      <span className="text-[10px] text-gray-600 shrink-0">{timeLabel(msg.ts)}</span>
    </div>
  );
}

function ToolSearchingBubble({ msg }: { msg: ChatMessage }) {
  return (
    <div className="flex items-center gap-2 text-[11px] text-gray-500 pl-8 py-0.5">
      <Loader2 className="w-3 h-3 animate-spin text-blue-500 shrink-0" />
      <span className="text-gray-500">searching live data…</span>
    </div>
  );
}

function ToolResultBubble({ msg }: { msg: ChatMessage }) {
  const [expanded, setExpanded] = useState(false);
  const isX = msg.tool === "x_search";
  return (
    <div className="pl-8 space-y-1">
      <button
        onClick={() => setExpanded(v => !v)}
        className="flex items-center gap-1.5 text-[11px] text-gray-400 hover:text-gray-200 transition-colors"
      >
        <CheckCircle2 className="w-3 h-3 text-green-500" />
        <span>
          {isX ? "X" : "Web"} search returned {msg.count ?? 0} result{(msg.count ?? 0) !== 1 ? "s" : ""}
        </span>
        {(msg.sources?.length ?? 0) > 0 && (
          <ChevronDown className={cn("w-3 h-3 transition-transform", expanded && "rotate-180")} />
        )}
        <span className="ml-auto text-[10px] text-gray-600">{timeLabel(msg.ts)}</span>
      </button>

      {expanded && msg.sources && msg.sources.length > 0 && (
        <div className="space-y-0.5 pl-4 border-l border-gray-800">
          {msg.sources.map((url, i) => (
            <a key={i} href={url} target="_blank" rel="noreferrer"
              className="flex items-center gap-1 text-[10px] text-blue-500 hover:text-blue-300 truncate">
              <ExternalLink className="w-2.5 h-2.5 shrink-0" />
              {hostOf(url)}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function ThinkingBubble({ msg }: { msg: ChatMessage }) {
  const [expanded, setExpanded] = useState(false);
  const text = msg.text || "";
  const preview = text.slice(0, 180);
  const hasMore = text.length > 180;

  return (
    <div className="rounded-lg border border-purple-500/20 bg-purple-500/5 p-3 space-y-1.5">
      <div className="flex items-center gap-2">
        <Brain className="w-3.5 h-3.5 text-purple-400" />
        <span className="text-[10px] font-semibold text-purple-300 uppercase tracking-wide">Grok reasoning</span>
        <span className="ml-auto text-[10px] text-gray-600">{timeLabel(msg.ts)}</span>
      </div>
      <p className="text-[11px] text-gray-300 leading-relaxed">
        {expanded ? text : preview}
        {hasMore && !expanded && "…"}
      </p>
      {hasMore && (
        <button onClick={() => setExpanded(v => !v)}
          className="text-[10px] text-purple-400 hover:text-purple-300">
          {expanded ? "Show less" : "Read full reasoning"}
        </button>
      )}
    </div>
  );
}

function DecisionBubble({ msg }: { msg: ChatMessage }) {
  const dec = msg.decision || "HOLD";
  const style = DECISION_STYLE[dec] ?? DECISION_STYLE.HOLD;
  const conf  = msg.confidence_score ?? 0;
  const rr    = msg.risk_reward ?? 0;
  const regime = msg.market_regime ?? "unknown";

  return (
    <div className={cn("rounded-lg border p-4 space-y-3", style.bg, style.border)}>
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className={cn("flex items-center gap-1.5 text-lg font-bold", style.text)}>
          {style.icon}
          {dec}
        </div>
        <div className={cn("text-sm font-semibold",
          conf >= 75 ? "text-green-400" : conf >= 55 ? "text-yellow-400" : "text-gray-400"
        )}>
          {conf}% confidence
        </div>
        <div className={cn("text-xs ml-2", REGIME_COLOR[regime] ?? "text-gray-500")}>
          {regime.replace(/_/g, " ")}
        </div>
        <span className="ml-auto text-[10px] text-gray-600">{timeLabel(msg.ts)}</span>
      </div>

      {/* Price levels — only show if BUY/SELL */}
      {dec !== "HOLD" && msg.entry_price && msg.entry_price > 0 && (
        <div className="grid grid-cols-3 gap-2 text-center">
          {[
            { label: "Entry",  val: msg.entry_price,  color: "text-gray-200" },
            { label: "Stop",   val: msg.stop_loss,    color: "text-red-400" },
            { label: "Target", val: msg.take_profit,  color: "text-green-400" },
          ].map(({ label, val, color }) => (
            <div key={label} className="bg-gray-900/60 rounded px-2 py-1.5">
              <p className="text-[9px] text-gray-500 uppercase">{label}</p>
              <p className={cn("text-sm font-mono font-bold", color)}>
                {val ? `$${val.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* R:R */}
      {dec !== "HOLD" && rr > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500">Risk:Reward</span>
          <div className="flex-1 h-1.5 bg-gray-800 rounded-full">
            <div className={cn("h-full rounded-full",
              rr >= 3 ? "bg-green-500" : rr >= 2 ? "bg-yellow-500" : "bg-red-400"
            )} style={{ width: `${Math.min(100, (rr / 5) * 100)}%` }} />
          </div>
          <span className={cn("text-xs font-bold",
            rr >= 3 ? "text-green-400" : rr >= 2 ? "text-yellow-400" : "text-red-400"
          )}>{rr.toFixed(1)}R</span>
        </div>
      )}

      {/* Reasoning */}
      {msg.reasoning && (
        <p className="text-[11px] text-gray-300 leading-relaxed border-l-2 border-gray-600 pl-2.5">
          {msg.reasoning}
        </p>
      )}

      {/* Catalyst */}
      {msg.catalyst && (
        <div className="text-[11px] space-y-0.5">
          <span className="text-gray-500">Catalyst: </span>
          <span className="text-gray-300">{msg.catalyst}</span>
          {msg.catalyst_source && (
            <a href={msg.catalyst_source} target="_blank" rel="noreferrer"
              className="ml-2 text-blue-500 hover:text-blue-400 inline-flex items-center gap-0.5">
              source <ExternalLink className="w-2.5 h-2.5" />
            </a>
          )}
        </div>
      )}

      {/* Time window + invalidation */}
      <div className="flex flex-wrap gap-3 text-[10px] text-gray-500">
        {msg.time_window && <span>Window: <span className="text-gray-400">{msg.time_window}</span></span>}
        {msg.invalidation && <span>Invalidate if: <span className="text-gray-400">{msg.invalidation.slice(0, 80)}</span></span>}
      </div>

      {/* Sources */}
      {msg.sources && msg.sources.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1 border-t border-gray-800">
          <span className="text-[10px] text-gray-600">Sources:</span>
          {msg.sources.slice(0, 5).map((url, i) => (
            <a key={i} href={url} target="_blank" rel="noreferrer"
              className="text-[10px] text-blue-500 hover:text-blue-400 flex items-center gap-0.5">
              {hostOf(url)} <ExternalLink className="w-2 h-2" />
            </a>
          ))}
          {msg.sources.length > 5 && (
            <span className="text-[10px] text-gray-600">+{msg.sources.length - 5} more</span>
          )}
        </div>
      )}

      {/* Token usage */}
      {msg.usage && (
        <p className="text-[10px] text-gray-600">
          tokens: {msg.usage.input_tokens} in · {msg.usage.output_tokens} out
        </p>
      )}
    </div>
  );
}

function ErrorBubble({ msg }: { msg: ChatMessage }) {
  return (
    <div className="flex items-start gap-2 rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2">
      <AlertTriangle className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" />
      <p className="text-[11px] text-red-300">{msg.message}</p>
    </div>
  );
}

function DoneBubble({ msg }: { msg: ChatMessage }) {
  return (
    <div className="flex items-center gap-2 text-[11px] text-green-500 py-0.5">
      <CheckCircle2 className="w-3 h-3" />
      <span>Analysis complete</span>
      <span className="ml-auto text-[10px] text-gray-600">{timeLabel(msg.ts)}</span>
    </div>
  );
}

function TextDeltaBubble({ msgs }: { msgs: ChatMessage[] }) {
  const text = msgs.map(m => m.delta).join("");
  return (
    <div className="pl-2 border-l-2 border-purple-800">
      <p className="text-[11px] text-gray-400 leading-relaxed font-mono">{text}</p>
    </div>
  );
}

// ── Message list renderer ──────────────────────────────────────────────────
function MessageList({ messages }: { messages: ChatMessage[] }) {
  // Coalesce consecutive text_delta into one block
  const rendered: React.ReactNode[] = [];
  let i = 0;
  while (i < messages.length) {
    const msg = messages[i];

    if (msg.type === "text_delta") {
      // Collect run of deltas
      const deltas = [msg];
      while (i + 1 < messages.length && messages[i + 1].type === "text_delta") {
        i++;
        deltas.push(messages[i]);
      }
      rendered.push(<TextDeltaBubble key={`delta-${msg.id}`} msgs={deltas} />);
    } else if (msg.type === "status") {
      rendered.push(<StatusBubble key={msg.id} msg={msg} />);
    } else if (msg.type === "market_data") {
      rendered.push(<MarketDataBubble key={msg.id} msg={msg} />);
    } else if (msg.type === "tool_call") {
      rendered.push(<ToolCallBubble key={msg.id} msg={msg} />);
    } else if (msg.type === "tool_searching") {
      rendered.push(<ToolSearchingBubble key={msg.id} msg={msg} />);
    } else if (msg.type === "tool_result") {
      rendered.push(<ToolResultBubble key={msg.id} msg={msg} />);
    } else if (msg.type === "thinking") {
      rendered.push(<ThinkingBubble key={msg.id} msg={msg} />);
    } else if (msg.type === "source_found") {
      // small inline source pill
      rendered.push(
        <div key={msg.id} className="pl-8">
          <a href={msg.url} target="_blank" rel="noreferrer"
            className="text-[10px] text-blue-500 hover:text-blue-300 flex items-center gap-0.5">
            <ExternalLink className="w-2.5 h-2.5" /> {hostOf(msg.url ?? "")}
            {msg.title && ` — ${msg.title.slice(0, 50)}`}
          </a>
        </div>
      );
    } else if (msg.type === "decision") {
      rendered.push(<DecisionBubble key={msg.id} msg={msg} />);
    } else if (msg.type === "done") {
      rendered.push(<DoneBubble key={msg.id} msg={msg} />);
    } else if (msg.type === "error") {
      rendered.push(<ErrorBubble key={msg.id} msg={msg} />);
    }

    i++;
  }
  return <>{rendered}</>;
}

// ── Main component ─────────────────────────────────────────────────────────
const SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AAPL", "NVDA", "TSLA", "SPY"];

export default function GrokChatWindow() {
  const [messages, setMessages]   = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [symbol, setSymbol]       = useState("BTC/USDT");
  const [autoScroll, setAutoScroll] = useState(true);
  const esRef      = useRef<EventSource | null>(null);
  const bottomRef  = useRef<HTMLDivElement>(null);
  const scrollRef  = useRef<HTMLDivElement>(null);
  const msgIdRef   = useRef(0);

  const addMsg = useCallback((raw: object) => {
    const msg = { id: msgIdRef.current++, ...(raw as object) } as ChatMessage;
    setMessages(prev => [...prev, msg]);
  }, []);

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, autoScroll]);

  const stopStream = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    setStreaming(false);
  }, []);

  const startScan = useCallback(() => {
    stopStream();
    setMessages([]);
    setStreaming(true);
    setAutoScroll(true);

    const encoded = encodeURIComponent(symbol);
    const url     = `${API_BASE}/grok-stream/live?symbol=${encoded}`;
    const es      = new EventSource(url);
    esRef.current = es;

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        addMsg(data);
        if (data.type === "done" || data.type === "decision") {
          // Keep stream open until "done" is explicitly received
          if (data.type === "done") {
            setStreaming(false);
            es.close();
            esRef.current = null;
          }
        }
      } catch {}
    };

    es.onerror = () => {
      addMsg({ type: "error", ts: new Date().toISOString(), message: "Connection to stream lost" });
      setStreaming(false);
      es.close();
      esRef.current = null;
    };
  }, [symbol, stopStream, addMsg]);

  // Cleanup on unmount
  useEffect(() => () => stopStream(), [stopStream]);

  const isScrolledUp = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return false;
    return el.scrollHeight - el.scrollTop - el.clientHeight > 100;
  }, []);

  return (
    <Card className="bg-gray-900 border-gray-800 flex flex-col" style={{ height: "600px" }}>
      <CardHeader className="pb-2 shrink-0">
        <CardTitle className="text-sm font-medium text-gray-300 flex items-center justify-between gap-2">
          {/* Left: title + status */}
          <div className="flex items-center gap-2 min-w-0">
            <Brain className="w-4 h-4 text-purple-400 shrink-0" />
            <span className="truncate">Grok Intelligence Feed</span>
            {streaming && (
              <span className="flex items-center gap-1 text-[10px] text-purple-300 font-normal shrink-0">
                <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse" />
                live
              </span>
            )}
          </div>

          {/* Right: symbol selector + buttons */}
          <div className="flex items-center gap-2 shrink-0">
            <select
              value={symbol}
              onChange={e => setSymbol(e.target.value)}
              disabled={streaming}
              className="bg-gray-800 border border-gray-700 text-gray-300 text-[11px] rounded px-2 py-1 focus:outline-none"
            >
              {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>

            {streaming ? (
              <Button size="sm" variant="outline"
                className="h-7 px-2.5 text-[11px] border-red-700 text-red-400 hover:border-red-500"
                onClick={stopStream}
              >
                <StopCircle className="w-3 h-3 mr-1" /> Stop
              </Button>
            ) : (
              <Button size="sm"
                className="h-7 px-2.5 text-[11px] bg-purple-600 hover:bg-purple-500 text-white"
                onClick={startScan}
              >
                <Zap className="w-3 h-3 mr-1" /> Ask Grok
              </Button>
            )}

            {!streaming && messages.length > 0 && (
              <button onClick={() => setMessages([])}
                className="text-gray-600 hover:text-gray-400" title="Clear">
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </CardTitle>
      </CardHeader>

      <CardContent className="flex-1 overflow-hidden p-0">
        <div
          ref={scrollRef}
          className="h-full overflow-y-auto px-4 py-3 space-y-2.5"
          onScroll={() => setAutoScroll(!isScrolledUp())}
        >
          {/* Empty state */}
          {messages.length === 0 && !streaming && (
            <div className="flex flex-col items-center justify-center h-full gap-4 text-center">
              <div className="relative">
                <Brain className="w-12 h-12 text-gray-800" />
                <Zap className="w-4 h-4 text-purple-500 absolute -top-0.5 -right-0.5" />
              </div>
              <div className="space-y-1.5 max-w-xs">
                <p className="text-sm font-medium text-gray-400">Grok Intelligence Feed</p>
                <p className="text-[11px] text-gray-600 leading-relaxed">
                  Watch Grok search the web + X in real-time, read its reasoning,
                  and see exactly why it decides to trade — or not.
                </p>
              </div>
              <Button
                className="bg-purple-600 hover:bg-purple-500 text-white text-xs px-4"
                onClick={startScan}
              >
                <Zap className="w-3.5 h-3.5 mr-1.5" />
                Ask Grok about {symbol}
              </Button>
              <p className="text-[10px] text-gray-700">
                Connects live to xAI API · searches web + X · streams reasoning
              </p>
            </div>
          )}

          {/* Live loading skeleton before first message */}
          {streaming && messages.length === 0 && (
            <div className="flex items-center gap-2 text-gray-500 text-xs">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Connecting to Grok…
            </div>
          )}

          <MessageList messages={messages} />

          {/* Typing indicator while streaming but before decision */}
          {streaming && !messages.some(m => m.type === "decision") && messages.length > 0 && (
            <div className="flex items-center gap-1.5 text-[11px] text-purple-400 pl-2">
              <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-bounce [animation-delay:0ms]" />
              <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-bounce [animation-delay:150ms]" />
              <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-bounce [animation-delay:300ms]" />
              <span className="ml-1 text-gray-600">Grok is thinking…</span>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </CardContent>

      {/* Bottom bar — auto-scroll notice */}
      {!autoScroll && messages.length > 0 && (
        <div className="shrink-0 px-4 pb-2">
          <button
            onClick={() => { setAutoScroll(true); bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }}
            className="w-full flex items-center justify-center gap-1.5 text-[10px] text-gray-500 hover:text-gray-300 bg-gray-800 rounded py-1.5 transition-colors"
          >
            <ChevronDown className="w-3 h-3" />
            Jump to latest
          </button>
        </div>
      )}
    </Card>
  );
}
