"""
routers/grok_stream.py
======================
Server-Sent Events (SSE) endpoint that streams Grok's full reasoning live:
  • each web_search / x_search tool call as it fires
  • search result sources as they arrive
  • Grok's incremental reasoning text
  • the final BUY / SELL / HOLD decision with entry / stop / target

Frontend connects with EventSource("http://localhost:8000/grok-stream/live?symbol=BTC/USDT")
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import time
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/grok-stream", tags=["grok-stream"])

_API_BASE = os.getenv("XAI_BASE_URL", "https://api.x.ai")
_MODEL    = os.getenv("GROK_MODEL",   "grok-4.3")
_TIMEOUT  = float(os.getenv("GT_TIMEOUT_S", "90"))


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _sse(event_type: str, payload: dict) -> str:
    """Format one SSE message."""
    data = json.dumps({"type": event_type, "ts": _now(), **payload})
    return f"data: {data}\n\n"


def _load_prompt() -> str:
    from pathlib import Path
    p = Path(__file__).parent.parent / "grok_prompt.txt"
    return p.read_text() if p.exists() else (
        "You are a Situation-Aware Quantitative Trading Agent. "
        "Search the web and X now for news on the provided symbol. "
        "Return ONLY JSON: {decision, confidence_score, market_regime, reasoning, "
        "catalyst, catalyst_source, entry_price, stop_loss, take_profit, "
        "risk_reward_ratio, time_window, invalidation}"
    )


async def _fetch_market_data(symbol: str) -> dict:
    """Fetch OHLCV + indicators from CCXT (Binance testnet)."""
    try:
        import ccxt
        import pandas as pd
        from ta.momentum import RSIIndicator
        from ta.trend import MACD
        from ta.volatility import AverageTrueRange

        testnet = os.getenv("TESTNET", "true").lower() == "true"
        key     = os.getenv("BINANCE_TESTNET_KEY",    "") if testnet else os.getenv("EXCHANGE_API_KEY", "")
        secret  = os.getenv("BINANCE_TESTNET_SECRET", "") if testnet else os.getenv("EXCHANGE_SECRET",  "")

        ex = ccxt.binance({
            "apiKey": key, "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot", "adjustForTimeDifference": True},
        })
        if testnet:
            ex.set_sandbox_mode(True)

        # For symbols not on Binance (stocks), fall back to yfinance
        ccxt_symbol = symbol.replace("-", "/")
        raw = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ex.fetch_ohlcv(ccxt_symbol, timeframe="5m", limit=100)
        )
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])

        balance = 0.0
        try:
            bal = await asyncio.get_event_loop().run_in_executor(None, ex.fetch_balance)
            balance = float(bal.get("USDT", {}).get("free", 0) or 0)
        except Exception:
            pass

        close, high, low = df["close"], df["high"], df["low"]
        rsi  = float(RSIIndicator(close, 14).rsi().iloc[-1])
        mcd  = MACD(close, 12, 26, 9)
        atr  = float(AverageTrueRange(high, low, close, 14).average_true_range().iloc[-1])
        price = float(close.iloc[-1])

        return {
            "symbol":       symbol,
            "price":        round(price, 4),
            "rsi":          round(rsi, 2),
            "macd":         round(float(mcd.macd_diff().iloc[-1]), 4),
            "macd_line":    round(float(mcd.macd().iloc[-1]), 4),
            "macd_signal":  round(float(mcd.macd_signal().iloc[-1]), 4),
            "atr":          round(atr, 4),
            "sma20":        round(float(close.rolling(20).mean().iloc[-1]), 4),
            "sma50":        round(float(close.rolling(50).mean().iloc[-1]), 4),
            "volume_24h":   round(float(df["volume"].tail(288).sum()), 2),
            "balance_usdt": round(balance, 2),
            "source":       "binance_testnet" if testnet else "binance_live",
        }
    except Exception as e:
        # Graceful fallback — return partial data so Grok can still run
        return {
            "symbol": symbol,
            "error":  str(e),
            "note":   "Could not fetch live bars — Grok will use its own market knowledge",
        }


async def _stream_grok(symbol: str, market: dict) -> AsyncGenerator[str, None]:
    """
    Stream the Grok Responses API call as SSE events.
    Handles: tool calls (web_search, x_search), text deltas, final decision.
    """
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        yield _sse("error", {"message": "XAI_API_KEY not set"})
        return

    now_et = dt.datetime.now(dt.timezone(dt.timedelta(hours=-4))).strftime("%Y-%m-%d %H:%M ET")
    risk_pct = float(os.getenv("GT_RISK_PCT", "2.0"))
    balance  = market.get("balance_usdt", 10000)

    user_msg = (
        f"Current time: {now_et}\n"
        f"Symbol: {symbol}\n"
        f"Account balance: ${balance:,.2f} USDT\n"
        f"Risk per trade: {risk_pct}% = ${balance * risk_pct / 100:,.2f}\n\n"
        f"Live market data:\n{json.dumps(market, indent=2)}\n\n"
        "Search the web and X RIGHT NOW for latest news and sentiment on this symbol, "
        "then output your trade decision as JSON exactly as specified."
    )

    payload = {
        "model":  _MODEL,
        "stream": True,
        "input": [
            {"role": "system", "content": _load_prompt()},
            {"role": "user",   "content": user_msg},
        ],
        "tools": [
            {"type": "web_search"},
            {"type": "x_search"},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }

    # ── State tracked across streaming events ───────────────────────────────
    text_buffer = ""
    reasoning_buffer = ""
    sources: list = []
    # item_id → {"tool": str, "query": str} — populated when item.done arrives
    pending_tools: dict = {}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{_API_BASE}/v1/responses",
                headers=headers, json=payload,
            ) as resp:

                if resp.status_code != 200:
                    body = await resp.aread()
                    yield _sse("error", {"message": f"HTTP {resp.status_code}: {body.decode()[:300]}"})
                    return

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw in ("[DONE]", ""):
                        continue

                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    ev_type = ev.get("type", "")

                    # ── New output item added (search starting) ──────────────
                    if ev_type == "response.output_item.added":
                        item = ev.get("item", {})
                        item_type = item.get("type", "")
                        item_id   = item.get("id", "")
                        if "web_search_call" in item_type or "x_search_call" in item_type:
                            tool_name = "web_search" if "web" in item_type else "x_search"
                            pending_tools[item_id] = {"tool": tool_name}
                            # Announce the search (query not known yet — arrives in .done)
                            yield _sse("tool_call", {
                                "tool": tool_name, "query": "", "item_id": item_id,
                            })

                    # ── Search spinning ──────────────────────────────────────
                    elif ev_type in (
                        "response.web_search_call.in_progress",
                        "response.x_search_call.in_progress",
                        "response.web_search_call.searching",
                        "response.x_search_call.searching",
                    ):
                        item_id = ev.get("item_id", "")
                        yield _sse("tool_searching", {"item_id": item_id})

                    # ── Output item DONE — this is where query+sources arrive ─
                    elif ev_type == "response.output_item.done":
                        item      = ev.get("item", {})
                        item_type = item.get("type", "")
                        item_id   = item.get("id", "")

                        if "web_search_call" in item_type or "x_search_call" in item_type:
                            tool_name = "web_search" if "web" in item_type else "x_search"
                            action    = item.get("action", {})
                            query     = action.get("query", "")
                            raw_srcs  = action.get("sources", [])
                            new_urls  = [s.get("url", "") for s in raw_srcs if s.get("url")]
                            sources.extend(u for u in new_urls if u not in sources)

                            yield _sse("tool_result", {
                                "tool":    tool_name,
                                "query":   query,
                                "count":   len(raw_srcs),
                                "sources": new_urls[:8],
                                "item_id": item_id,
                            })

                        elif item_type == "message":
                            # The final text message item
                            for block in item.get("content", []):
                                if block.get("type") == "output_text":
                                    text_buffer = block.get("text", "")
                                for ann in block.get("annotations", []):
                                    url = ann.get("url", "")
                                    if url and url not in sources:
                                        sources.append(url)

                    # ── Grok's internal reasoning summary (show as "thinking") ─
                    elif ev_type == "response.reasoning_summary_text.delta":
                        delta = ev.get("delta", "")
                        reasoning_buffer += delta
                        yield _sse("text_delta", {"delta": delta})

                    elif ev_type == "response.reasoning_summary_text.done":
                        text = ev.get("text", "") or reasoning_buffer
                        if text.strip():
                            yield _sse("thinking", {"text": text.strip()})
                        reasoning_buffer = ""

                    # ── Final response text delta ────────────────────────────
                    elif ev_type == "response.output_text.delta":
                        delta = ev.get("delta", "")
                        text_buffer += delta
                        # Don't stream the JSON — it's garbled mid-stream;
                        # show a "writing decision…" status instead
                        if len(text_buffer) == len(delta):  # first delta
                            yield _sse("status", {"message": "Writing decision…"})

                    elif ev_type == "response.output_text.done":
                        text_buffer = ev.get("text", "") or text_buffer

                    # ── Annotations (inline citations) ───────────────────────
                    elif ev_type == "response.output_text.annotation.added":
                        ann = ev.get("annotation", {})
                        url = ann.get("url", "")
                        if url and url not in sources:
                            sources.append(url)
                            yield _sse("source_found", {
                                "url": url, "title": ann.get("title", ""),
                            })

                    # ── Entire response complete ─────────────────────────────
                    elif ev_type == "response.completed":
                        full_resp = ev.get("response", {})

                        # Fallback: extract text+sources from full response object
                        if not text_buffer:
                            for out in full_resp.get("output", []):
                                if out.get("type") == "message":
                                    for block in out.get("content", []):
                                        if block.get("type") == "output_text":
                                            text_buffer = block.get("text", "")
                                        for ann in block.get("annotations", []):
                                            url = ann.get("url", "")
                                            if url and url not in sources:
                                                sources.append(url)
                                elif "search_call" in out.get("type", ""):
                                    # Also extract sources from search items
                                    for s in out.get("action", {}).get("sources", []):
                                        url = s.get("url", "")
                                        if url and url not in sources:
                                            sources.append(url)

                        # Parse the JSON trade decision
                        try:
                            raw_text = text_buffer.strip()
                            if raw_text.startswith("```"):
                                raw_text = raw_text.split("\n", 1)[-1]
                                raw_text = raw_text[: raw_text.rfind("```")].strip() if "```" in raw_text else raw_text
                            decision = json.loads(raw_text)
                        except json.JSONDecodeError:
                            decision = {
                                "decision":         "HOLD",
                                "confidence_score": 0,
                                "reasoning":        text_buffer[:500] if text_buffer else "Parse error",
                                "market_regime":    "unknown",
                            }

                        usage = full_resp.get("usage", {})
                        yield _sse("decision", {
                            "decision":         str(decision.get("decision", "HOLD")).upper(),
                            "confidence_score": int(decision.get("confidence_score", 0) or 0),
                            "market_regime":    str(decision.get("market_regime", "unknown")),
                            "reasoning":        str(decision.get("reasoning", "")),
                            "catalyst":         str(decision.get("catalyst", "")),
                            "catalyst_source":  str(decision.get("catalyst_source", "")),
                            "entry_price":      float(decision.get("entry_price", 0) or 0),
                            "stop_loss":        float(decision.get("stop_loss", 0) or 0),
                            "take_profit":      float(decision.get("take_profit", 0) or 0),
                            "risk_reward":      float(decision.get("risk_reward_ratio", 0) or 0),
                            "time_window":      str(decision.get("time_window", "")),
                            "invalidation":     str(decision.get("invalidation", "")),
                            "sources":          sources[:10],
                            "usage": {
                                "input_tokens":  usage.get("input_tokens", 0),
                                "output_tokens": usage.get("output_tokens", 0),
                            },
                        })
                        yield _sse("done", {"message": "Analysis complete"})
                        return

    except httpx.TimeoutException:
        yield _sse("error", {"message": f"Grok timed out after {_TIMEOUT}s"})
    except Exception as exc:
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})


async def _full_generator(symbol: str) -> AsyncGenerator[str, None]:
    """Orchestrate the full streaming flow from market data → Grok."""
    yield _sse("status", {"message": f"Starting Grok analysis for {symbol}…"})

    # ── Step 1: Fetch market data ─────────────────────────────────────────
    yield _sse("status", {"message": f"Fetching {symbol} market data from exchange…"})
    market = await _fetch_market_data(symbol)

    if "error" not in market:
        yield _sse("market_data", {
            "symbol":   market["symbol"],
            "price":    market["price"],
            "rsi":      market["rsi"],
            "macd":     market["macd"],
            "atr":      market["atr"],
            "sma20":    market["sma20"],
            "sma50":    market["sma50"],
            "balance":  market["balance_usdt"],
            "source":   market.get("source", "exchange"),
        })
    else:
        yield _sse("status", {"message": f"Exchange data unavailable — {market.get('error', '')}. Grok will use own knowledge."})

    # ── Step 2: Stream from Grok ──────────────────────────────────────────
    yield _sse("status", {"message": f"Sending to Grok-4.3 with live web + X search…"})
    async for event in _stream_grok(symbol, market):
        yield event


@router.get("/live")
async def stream_analysis(
    symbol: str = Query(default="BTC/USDT", description="Trading symbol"),
):
    """
    SSE endpoint. Connect with:
        EventSource("http://localhost:8000/grok-stream/live?symbol=BTC%2FUSDT")
    Emits events: status | market_data | tool_call | tool_searching |
                  tool_result | text_delta | thinking | source_found |
                  decision | done | error
    """
    async def generator():
        async for chunk in _full_generator(symbol):
            yield chunk
        # Keep-alive comment so browser doesn't close the connection early
        yield ": keep-alive\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",    # nginx: disable buffering
            "Access-Control-Allow-Origin": "http://localhost:3000",
            "Access-Control-Allow-Credentials": "true",
        },
    )
