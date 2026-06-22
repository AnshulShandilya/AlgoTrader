"""
grok_trader.py
==============
Competition-grade Grok-powered day-trading signal engine for AlgoTrader Pro.

HOW IT WORKS
------------
On each scan (morning pre-market + optional mid-session):
  1. Grok searches live web + X (Twitter) for catalyst-driven movers — earnings,
     upgrades, FDA, product launches, unusual options activity, on-chain events.
  2. Each setup is returned with exact entry, stop, and target prices, a verified
     source URL, and a confidence score.
  3. The existing probability engine scores each setup independently.
  4. All CLAUDE.md risk rules apply: ATR-derived sizing, daily loss limit, no SL removal.

SAFETY INVARIANTS (identical to SA layer)
  - Never places orders automatically; execution is always a deliberate user action.
  - All positions still go through the normal trade executor and risk guards.
  - On any Grok failure the scan returns an empty setup list — never a stale one.

COMPETITION EDGE
  The advantage the Grok approach is famous for:
    • Live X/web search finds catalyst plays BEFORE they are fully priced in.
    • "Catalyst + technical level" confluence produces cleaner setups than pure-TA.
    • Strict JSON schema forces Grok to define entry/stop/target before recommending —
      the same discipline that separates consistent winners from over-traders.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

import httpx

# --------------------------------------------------------------------------- #
# Config (environment variables — no hard-coded secrets)
# --------------------------------------------------------------------------- #
XAI_BASE_URL = os.getenv("XAI_BASE_URL", "https://api.x.ai")
GROK_MODEL   = os.getenv("GROK_MODEL",   "grok-4.3")
GT_TIMEOUT_S = float(os.getenv("GT_TIMEOUT_S", "90"))
GT_MAX_SETUPS = int(os.getenv("GT_MAX_SETUPS", "5"))

# --------------------------------------------------------------------------- #
# System prompt — the "brain" of the Grok Trader
# --------------------------------------------------------------------------- #
GROK_TRADER_SYSTEM = """You are GROK-TRADER, an elite trading signal generator for AlgoTrader Pro (UK-based).
You have access to real-time web search AND X (Twitter/financial social media) search.

You combine FIVE intelligence layers before recommending any trade:

  1. CATALYST FIRST — every trade is anchored to a verifiable, fresh news/event catalyst (< 48h old).
  2. TECHNICAL ALIGNMENT — catalyst direction matches the technical picture (VWAP, prior-day level, breakout structure).
  3. SENTIMENT LAYER — social media and public mood must confirm the direction, not contradict it.
  4. CONTRACT & TENDER INTELLIGENCE — major contract wins, government tenders, and strategic partnerships are high-impact catalysts, especially for UK mid/large caps.
  5. ASYMMETRIC RISK — minimum 2:1 reward-to-risk; never chase entries after the move has extended.

═══════════════════════════════════════════════════════════════
LAYER 1 — FINANCIAL NEWS (search ALL of these)
═══════════════════════════════════════════════════════════════
UK-SPECIFIC sources to search:
  • London Stock Exchange RNS (Regulatory News Service) — lse.co.uk/RNSSearch
  • Proactive Investors (proactiveinvestors.co.uk) — specialist UK small/mid-cap coverage
  • Investors Chronicle (investorschronicle.co.uk)
  • Citywire (citywire.com/money)
  • This is Money / Financial Mail on Sunday
  • The Times Business / Sunday Times Business
  • Reuters UK (reuters.com/business/uk)
  • Financial Times (ft.com)
  • Sky News Business, BBC Business
  • Hargreaves Lansdown news feed (hl.co.uk)
  • SharePad / ShareScope alerts

GLOBAL sources:
  • Bloomberg, Reuters, CNBC, MarketWatch, Seeking Alpha
  • Yahoo Finance earnings/guidance beats
  • Analyst upgrades/downgrades from major brokers (Goldman, JPMorgan, UBS, Berenberg, Peel Hunt)

═══════════════════════════════════════════════════════════════
LAYER 2 — X / SOCIAL MEDIA SENTIMENT (search X and Reddit)
═══════════════════════════════════════════════════════════════
Search X (Twitter) for:
  • Cashtag mentions: $TICKER and #TICKER trending in past 4 hours
  • Posts from credible accounts: @UKInvestor, @ProactiveInvest, financial journalists, fund managers
  • Unusual spike in cashtag volume (compare last-4h mentions vs prior-24h baseline)
  • Sentiment direction: are credible accounts bullish or bearish? Are retail accounts piling in (caution) or drip-buying (positive)?
  • Short-seller reports or bear case threads (treat as catalyst for short setups)
  • CEO/CFO posts or company official account posts (very high signal)

Search Reddit for:
  • r/UKInvesting, r/UKPersonalFinance, r/investing — trending tickers
  • r/wallstreetbets for US stocks — unusual mention spikes
  • Tone: FOMO/hype (reduce confidence) vs fundamental-driven discussion (increase confidence)

Sentiment scoring guidance:
  - sentiment_score: +1.0 = extremely bullish social consensus with credible sources
  - sentiment_score:  0.0 = neutral / mixed / no signal
  - sentiment_score: -1.0 = extremely bearish (short-seller reports, scandal, heavy selling)
  - sentiment_volume: "high" if cashtag mentions 3x+ above baseline, "normal" otherwise
  - Do NOT treat anonymous hype or meme activity as high-confidence sentiment.

═══════════════════════════════════════════════════════════════
LAYER 3 — CONTRACT WINS, TENDERS & STRATEGIC PARTNERSHIPS
═══════════════════════════════════════════════════════════════
This is a HIGH-ALPHA source specifically for UK-listed companies. Search for:

Government & Public Sector Contracts:
  • Contracts Finder (contractsfinder.service.gov.uk) — UK government contract awards
  • Find a Tender Service (find-tender.service.gov.uk) — post-Brexit UK procurement
  • NHS Supply Chain, MOD procurement, HMRC/DWP IT contracts, NHS Digital
  • Local council and devolved government (Scotland/Wales/NI) awards
  • Defence contracts (for BAE Systems, Rolls-Royce, QinetiQ, Ultra Electronics, Babcock)

Private Sector Major Contracts:
  • RNS announcements tagged "contract win", "preferred supplier", "framework agreement"
  • FTSE 100/250 supply chain awards (e.g. Capita, Serco, Mitie, G4S/Allied Universal types)
  • Energy sector: National Grid, BP, Shell, DESNZ (Dept for Energy Security) awards

Strategic Partnerships & Collaborations:
  • JV (joint venture) announcements, licensing deals, exclusivity agreements
  • Technology partnerships (Microsoft, AWS, Google Cloud partnering with UK firms)
  • NHS/healthcare partnerships (pharma licensing, device procurement)
  • International expansion: UK company winning overseas government contract
  • Consortium bids (UK firm as lead or key partner in major infrastructure bid)

Why this matters for trading:
  • A major government contract win (>£50m) is often NOT fully priced in within the first 2 hours
  • Framework agreements signal recurring multi-year revenue — analysts upgrade within 24–48h
  • Partnership announcements with FTSE 100 or global tech companies validate the business model
  • These are DURABLE catalysts (not one-day wonders) — suitable for swing as well as day trades

═══════════════════════════════════════════════════════════════
STRICT OUTPUT RULES
═══════════════════════════════════════════════════════════════
  - Respond with ONE valid JSON object and nothing else. No markdown, no code fences.
  - Only include setups where you found a VERIFIABLE source URL.
  - r_r_ratio must be ≥ 2.0. Skip anything below.
  - Maximum """ + str(GT_MAX_SETUPS) + """ setups. Quality over quantity.
  - Do NOT include "avoid" symbols in the setups list.
  - confidence is 0.0–1.0 where 1.0 = very high conviction; be calibrated, not optimistic.
  - If you cannot find strong setups today, return an empty setups list — do not fabricate.
  - For contract/tender events: always include contract_value_gbp if disclosed, else null.
  - For UK stocks: prices in pence (p), not pounds. entry_price/stop/target all in pence.
  - trade_type classification rules:
      • "scalping"    — hold < 1 hour, tight stop < 0.5% from entry, target 0.3–0.8%, high RVOL, pure technical
      • "day_trading" — hold 2–6 hours within session, VWAP/ORB/gap setups, target 1–3%, close same day
      • "swing_trading" — hold 1–7 days, catalyst-driven durable move, structural target level, 3–10%+ target

RETURN THIS EXACT JSON SCHEMA:
{
  "scan_time_utc": "<ISO8601>",
  "model_version": "grok-3",
  "market_bias": "bullish|neutral|bearish",
  "session_overview": "<2-3 sentences on today's macro + sector themes>",
  "sentiment_summary": "<1-2 sentences on overall social media mood across markets today>",
  "setups": [
    {
      "id": "<uuid4>",
      "symbol": "<TICKER>",
      "direction": "long|short",
      "asset_class": "us_stock|uk_stock|crypto|commodity|etf",
      "trade_type": "scalping|day_trading|swing_trading",
      "setup_type": "gap_and_go|orb_breakout|vwap_reclaim|momentum_continuation|reversal_at_key_level|catalyst_breakout|contract_win|partnership_announcement",
      "catalyst": "<one sentence describing the SPECIFIC catalyst found>",
      "catalyst_age_hours": <float>,
      "catalyst_strength": "strong|moderate|weak",
      "catalyst_source": "<primary source URL>",
      "contract_or_partnership": {
        "type": "government_contract|private_contract|jv|licensing|partnership|framework|null",
        "counterparty": "<who awarded it or who the partner is, or null>",
        "value_gbp_m": <contract value in £ millions, or null if undisclosed>,
        "duration_years": <contract length in years, or null>,
        "notes": "<one sentence on strategic significance, or null>"
      },
      "sentiment": {
        "score": <float -1.0 to +1.0>,
        "volume": "high|normal|low",
        "x_trend": "<brief description of X/Twitter mood, or 'no signal'>",
        "reddit_trend": "<brief description of Reddit mood, or 'no signal'>",
        "credible_account_stance": "bullish|bearish|neutral|no_signal"
      },
      "entry_price": <float>,
      "entry_trigger": "<exact condition to enter>",
      "stop_price": <float>,
      "stop_rationale": "<why this level invalidates the thesis>",
      "target_price": <float>,
      "target_rationale": "<why this is the target>",
      "r_r_ratio": <float, must be ≥ 2.0>,
      "time_window_et": "<e.g. '08:00-10:00 BST'>",
      "invalidation": "<conditions that cancel the setup before entry>",
      "trade_logic": {
        "why_now": "<1 sentence: what makes this the right time to enter>",
        "technical_confluence": "<1–2 sentences: key technical levels or patterns that align>",
        "catalyst_rationale": "<1 sentence: how the catalyst directly supports the direction>",
        "expected_timeline": "<e.g. '2–4 hours' for scalping/day, '3–7 days' for swing>",
        "what_invalidates": "<1 sentence: what would prove the thesis wrong>"
      },
      "rvol_estimate": <float>,
      "confidence": <float 0.0-1.0>,
      "sources": ["<url1>", "<url2>"]
    }
  ],
  "contract_watchlist": [
    {
      "symbol": "<TICKER>",
      "company": "<full company name>",
      "event": "<contract/tender/partnership description>",
      "event_type": "contract_win|tender_bid|partnership|framework_agreement|jv",
      "counterparty": "<awarding body or partner>",
      "value_gbp_m": <float or null>,
      "announced_at": "<ISO8601 or date string>",
      "trading_implication": "<buy_watch|sell_watch|monitor>",
      "source": "<url>"
    }
  ],
  "avoid_today": [
    {"symbol": "<TICKER>", "reason": "<why to avoid today>"}
  ],
  "sources_searched": ["<url>"]
}"""


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #
@dataclass
class GrokSetup:
    id: str = ""
    symbol: str = ""
    direction: str = "long"
    asset_class: str = "us_stock"
    trade_type: str = "day_trading"
    setup_type: str = "catalyst_breakout"
    catalyst: str = ""
    catalyst_age_hours: float = 0.0
    catalyst_strength: str = "moderate"
    catalyst_source: str = ""
    # Contract / tender / partnership intelligence
    contract_or_partnership: dict = field(default_factory=dict)
    # Social media sentiment layer
    sentiment: dict = field(default_factory=dict)
    entry_price: float = 0.0
    entry_trigger: str = ""
    stop_price: float = 0.0
    stop_rationale: str = ""
    target_price: float = 0.0
    target_rationale: str = ""
    r_r_ratio: float = 0.0
    time_window_et: str = ""
    invalidation: str = ""
    trade_logic: dict = field(default_factory=dict)
    rvol_estimate: float = 1.0
    confidence: float = 0.0
    sources: list = field(default_factory=list)
    # Enriched by our probability engine after Grok returns
    pre_trade_probability: Optional[int] = None
    pre_trade_verdict: Optional[str] = None
    pre_trade_expected_r: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GrokScan:
    scan_time_utc: str = ""
    model_version: str = GROK_MODEL
    market_bias: str = "neutral"
    session_overview: str = ""
    sentiment_summary: str = ""
    setups: list[GrokSetup] = field(default_factory=list)
    # Tracks contract/tender/partnership events even if no trade setup yet
    contract_watchlist: list[dict] = field(default_factory=list)
    avoid_today: list[dict] = field(default_factory=list)
    sources_searched: list[str] = field(default_factory=list)
    ok: bool = True
    error: Optional[str] = None
    latency_ms: Optional[int] = None

    @classmethod
    def empty(cls, reason: str) -> "GrokScan":
        return cls(
            scan_time_utc=_utcnow_iso(),
            market_bias="neutral",
            session_overview="",
            sentiment_summary="",
            setups=[],
            contract_watchlist=[],
            ok=False,
            error=reason,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


def _parse_scan(raw_json: dict, latency_ms: int) -> GrokScan:
    setups = []
    for s in raw_json.get("setups", []):
        # Enforce minimum R:R — skip anything < 2.0 that snuck through
        rrr = float(s.get("r_r_ratio", 0) or 0)
        if rrr < 2.0:
            continue
        setup = GrokSetup(
            id=str(s.get("id") or uuid.uuid4()),
            symbol=str(s.get("symbol", "")).upper(),
            direction=str(s.get("direction", "long")),
            asset_class=str(s.get("asset_class", "us_stock")),
            setup_type=str(s.get("setup_type", "catalyst_breakout")),
            catalyst=str(s.get("catalyst", "")),
            catalyst_age_hours=float(s.get("catalyst_age_hours", 0) or 0),
            catalyst_strength=str(s.get("catalyst_strength", "moderate")),
            catalyst_source=str(s.get("catalyst_source", "")),
            contract_or_partnership=dict(s.get("contract_or_partnership") or {}),
            sentiment=dict(s.get("sentiment") or {}),
            entry_price=float(s.get("entry_price", 0) or 0),
            entry_trigger=str(s.get("entry_trigger", "")),
            stop_price=float(s.get("stop_price", 0) or 0),
            stop_rationale=str(s.get("stop_rationale", "")),
            target_price=float(s.get("target_price", 0) or 0),
            target_rationale=str(s.get("target_rationale", "")),
            r_r_ratio=round(rrr, 2),
            time_window_et=str(s.get("time_window_et", "")),
            invalidation=str(s.get("invalidation", "")),
            rvol_estimate=float(s.get("rvol_estimate", 1.0) or 1.0),
            confidence=min(1.0, max(0.0, float(s.get("confidence", 0.5) or 0.5))),
            sources=list(s.get("sources", []) or []),
        )
        setups.append(setup)

    return GrokScan(
        scan_time_utc=str(raw_json.get("scan_time_utc", _utcnow_iso())),
        model_version=str(raw_json.get("model_version", GROK_MODEL)),
        market_bias=str(raw_json.get("market_bias", "neutral")),
        session_overview=str(raw_json.get("session_overview", "")),
        sentiment_summary=str(raw_json.get("sentiment_summary", "")),
        setups=setups,
        contract_watchlist=list(raw_json.get("contract_watchlist", []) or []),
        avoid_today=list(raw_json.get("avoid_today", []) or []),
        sources_searched=list(raw_json.get("sources_searched", []) or []),
        ok=True,
        latency_ms=latency_ms,
    )


def _enrich_with_probability(scan: GrokScan) -> GrokScan:
    """Score each setup with the pre-trade probability engine."""
    try:
        from probability import score_trade, PreTradeSignal
    except ImportError:
        return scan

    for setup in scan.setups:
        if setup.entry_price <= 0 or setup.stop_price <= 0 or setup.target_price <= 0:
            continue
        entry = setup.entry_price
        stop  = setup.stop_price
        tgt   = setup.target_price
        sl_pct = abs(entry - stop) / entry * 100
        tp_pct = abs(tgt - entry)  / entry * 100

        sig = PreTradeSignal(
            signal_confidence=setup.confidence,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            strategy_closed_trades=0,
            strategy_win_rate=0.5,
            strategy_avg_r=1.0,
            strategy_profit_factor=1.5,
            recent_closes=[],
            symbol=setup.symbol,
            timeframe="5Min",
        )
        result = score_trade(sig)
        setup.pre_trade_probability = result.score
        setup.pre_trade_verdict     = result.verdict
        setup.pre_trade_expected_r  = round(result.expected_r, 2)

    return scan


# --------------------------------------------------------------------------- #
# In-memory cache
# --------------------------------------------------------------------------- #
_last_scan: Optional[GrokScan] = None

SCAN_HEALTH: dict[str, Any] = {
    "model": GROK_MODEL,
    "last_scan_utc": None,
    "last_latency_ms": None,
    "last_error": None,
    "setups_found": 0,
}


def get_last_scan() -> Optional[GrokScan]:
    return _last_scan


def get_latest_setups() -> list[dict]:
    """Return the latest Grok setups as plain dicts (for auto_exec consumption)."""
    scan = _last_scan
    if not scan or not scan.ok:
        return []
    result = []
    for s in scan.setups:
        result.append({
            "id":                      s.id,
            "symbol":                  s.symbol,
            "direction":               s.direction,
            "asset_class":             s.asset_class,
            "catalyst":                s.catalyst,
            "catalyst_strength":       s.catalyst_strength,
            "contract_or_partnership": s.contract_or_partnership,
            "sentiment":               s.sentiment,
            "entry_price":             s.entry_price,
            "stop_price":              s.stop_price,
            "target_price":            s.target_price,
            "r_r_ratio":               s.r_r_ratio,
            "confidence":              s.confidence,
            "rvol_estimate":           s.rvol_estimate,
            "pre_trade_probability":   s.pre_trade_probability,
        })
    return result


# --------------------------------------------------------------------------- #
# Core scan function
# --------------------------------------------------------------------------- #
def _build_user_prompt(context: dict) -> str:
    now_bst = dt.datetime.now(dt.timezone(dt.timedelta(hours=1))).strftime("%Y-%m-%d %H:%M BST")
    now_et  = dt.datetime.now(dt.timezone(dt.timedelta(hours=-4))).strftime("%H:%M ET")

    # ── Pull live filtered candidates from the 4-layer funnel ────────────────
    # Falls back to a curated default list when the screener hasn't run yet
    try:
        from stock_filter import get_grok_candidates, get_grok_watchlist, summarise
        candidates = get_grok_candidates()
        if candidates:
            watchlist = get_grok_watchlist(candidates)
            # Build rich context: symbol + why it qualified
            candidate_context = []
            for c in candidates:
                candidate_context.append({
                    "symbol":      c.symbol,
                    "asset_class": c.asset_class,
                    "price":       c.display_price,
                    "move_pct":    f"{c.pct_change:+.1f}%",
                    "vol_ratio":   f"{c.vol_ratio:.1f}x avg",
                    "rsi":         round(c.rsi, 1),
                    "score":       c.score,
                    "signals": [
                        k for k, v in c.score_breakdown.items() if v > 0
                    ],
                })
        else:
            raise ValueError("no candidates yet")
    except Exception:
        # Screener hasn't run yet — use curated fallback watchlist
        candidate_context = []
        watchlist = context.get("watchlist", [
            "LLOY.L", "BARC.L", "BP.L", "HSBA.L", "AZN.L", "VOD.L",
            "GSK.L", "SHEL.L", "RIO.L", "ULVR.L", "BT-A.L",
            "AAPL", "NVDA", "TSLA", "META", "MSFT",
            "BTC-USD", "ETH-USD", "SOL-USD",
            "GC=F", "CL=F",
        ])

    # ── Pull Situational Awareness context ───────────────────────────────────
    # The SA layer runs every 20 min and produces macro regime + per-symbol sentiment.
    # Injecting it here means Grok-Trader knows the macro backdrop BEFORE recommending
    # entries — it can avoid setups that conflict with the SA risk posture.
    sa_context: dict = {}
    try:
        from situational_awareness import get_last_assessment
        sa = get_last_assessment()
        if sa and sa.ok:
            sa_context = {
                "market_regime":    sa.market_regime,
                "risk_posture":     sa.risk_posture,
                "macro_summary":    sa.macro_summary[:300],
                "event_risk":       sa.event_risk[:3],           # top 3 upcoming events
                "per_symbol_sa":    sa.per_symbol,               # symbol-level SA bias
                "sa_confidence":    sa.confidence,
                "sa_timestamp":     sa.timestamp_utc,
                "sa_note": (
                    "IMPORTANT: risk_posture='risk_off' means avoid new longs. "
                    "event_risk items are scheduled macro events that may cause sharp moves — "
                    "do NOT open a setup that faces a high-impact event within its trade timeline."
                ) if sa.risk_posture == "risk_off" else (
                    "SA is neutral/risk-on. Proceed per normal signal quality filters."
                ),
            }
    except Exception:
        pass  # SA not available — proceed without it

    payload = {
        "current_time":    f"{now_bst} / {now_et}",
        "user_location":   "UK",
        "primary_markets": "LSE (UK stocks), NYSE/NASDAQ (US stocks), CME (commodities), Crypto",
        "screened_candidates": candidate_context if candidate_context else None,
        "watchlist":       watchlist,
        "watchlist_source": "4-layer funnel (scored from 18,000+ universe)" if candidate_context else "curated fallback",
        "asset_classes":   context.get("asset_classes", ["uk_stocks", "us_stocks", "crypto", "commodity"]),
        "open_positions":  context.get("open_positions", []),
        "session_bias":    context.get("session_bias", "neutral"),
        "situational_awareness": sa_context if sa_context else "not_available",
        "special_instructions": [
            "These tickers were PRE-SELECTED by an automated screener because they show unusual volume or price moves TODAY.",
            "PRIORITY: For each ticker, search RNS/web/X to find WHY it is moving — that catalyst is your edge.",
            "PRIORITY: Search LSE RNS for announcements in the last 24h (contract wins, earnings, partnerships).",
            "PRIORITY: Search X/Twitter cashtag trends for each listed ticker.",
            "PRIORITY: Search Contracts Finder and Find a Tender for UK government contract awards in last 48h.",
            "Search r/UKInvesting and r/investing for trending stocks from this list.",
            "Check FT, Reuters UK, Proactive Investors for today's UK corporate news.",
            "For any contract win or partnership announcement: populate contract_watchlist even if no trade setup.",
        ],
        "notes": context.get("notes", ""),
    }

    source_note = (
        f"These {len(watchlist)} tickers were selected by automated 4-layer scoring from 18,000+ universe."
        if candidate_context else
        "Using curated fallback watchlist (screener not yet run)."
    )

    return (
        f"It is {now_bst}. You are scanning for a UK-based trader.\n\n"
        f"{source_note}\n\n"
        "Your three priorities:\n"
        "1. FIND THE CATALYST — each ticker is moving for a reason. Search RNS, FT, Reuters UK, X to find it.\n"
        "2. SENTIMENT — check X cashtags and r/UKInvesting for public mood on each ticker.\n"
        "3. CONTRACTS & TENDERS — search Contracts Finder, Find a Tender, RNS for any contract wins.\n\n"
        "Return the JSON exactly as specified. Populate contract_watchlist for ANY contract/tender event found.\n\n"
        + json.dumps(payload, default=str, ensure_ascii=False)
    )


async def run_scan(
    context: Optional[dict] = None,
    *,
    model: str = GROK_MODEL,
) -> GrokScan:
    """Trigger a full Grok day-trading scan. Returns GrokScan (never raises)."""
    global _last_scan

    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        return GrokScan.empty("XAI_API_KEY not set")

    ctx = context or {}
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": GROK_TRADER_SYSTEM},
            {"role": "user",   "content": _build_user_prompt(ctx)},
        ],
        "tools": [
            {"type": "web_search"},
            {"type": "x_search"},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{XAI_BASE_URL}/v1/responses"

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=GT_TIMEOUT_S) as client:
            resp = await client.post(url, headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - started) * 1000)

            if resp.status_code != 200:
                err = f"HTTP {resp.status_code}: {resp.text[:400]}"
                SCAN_HEALTH["last_error"] = err
                return GrokScan.empty(err)

            raw = resp.json()

            # Extract text from Responses API output
            content = ""
            for item in raw.get("output", []):
                if item.get("type") == "message":
                    for block in item.get("content", []):
                        if block.get("type") == "output_text":
                            content = block.get("text", "")
                            break

            if not content:
                err = "empty output from Responses API"
                SCAN_HEALTH["last_error"] = err
                return GrokScan.empty(err)

            data = json.loads(_strip_fences(content))
            scan = _parse_scan(data, latency_ms)
            scan = _enrich_with_probability(scan)

            _last_scan = scan
            SCAN_HEALTH.update({
                "last_scan_utc": _utcnow_iso(),
                "last_latency_ms": latency_ms,
                "last_error": None,
                "setups_found": len(scan.setups),
            })
            return scan

    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        SCAN_HEALTH["last_error"] = err
        return GrokScan.empty(err)


# --------------------------------------------------------------------------- #
# FastAPI router
# --------------------------------------------------------------------------- #
try:
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/grok-trader", tags=["grok-trader"])

    @router.get("/health")
    async def gt_health() -> dict:
        return SCAN_HEALTH

    @router.get("/setups")
    async def gt_latest() -> dict:
        scan = get_last_scan()
        if scan is None:
            return {"available": False, "message": "No scan run yet this session. POST /grok-trader/scan to start."}
        return {"available": True, **scan.to_dict()}

    @router.post("/scan")
    async def gt_scan(context: Optional[dict] = None) -> dict:
        """Trigger an on-demand Grok day-trading scan (takes 15–40s)."""
        scan = await run_scan(context or {})
        return scan.to_dict()

except ImportError:
    router = None


# --------------------------------------------------------------------------- #
# Scheduler integration
# --------------------------------------------------------------------------- #
async def morning_scan_job(get_context=None) -> None:
    """APScheduler job: run at 13:00 UTC (9 AM ET) every trading day."""
    ctx = get_context() if get_context else {}
    scan = await run_scan(ctx)

    try:
        from routers.events import write_event
        if scan.ok and scan.setups:
            body = (
                f"Grok Trader found {len(scan.setups)} setup(s) — "
                f"bias: {scan.market_bias}. "
                + ", ".join(
                    f"{s.symbol} {s.direction} ({s.setup_type}) conf={int(s.confidence*100)}%"
                    for s in scan.setups[:3]
                )
            )
            await write_event("grok_scan", "Grok Trader: morning scan complete", body, level="info")
        elif scan.ok:
            await write_event("grok_scan", "Grok Trader: no setups found today", scan.session_overview, level="info")
        else:
            await write_event("grok_scan", "Grok Trader: scan failed", scan.error or "", level="warning")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# CLI verify
# --------------------------------------------------------------------------- #
async def _verify_cli() -> int:
    print(f"[gt-verify] model={GROK_MODEL}  endpoint={XAI_BASE_URL}/v1/responses")
    if not os.getenv("XAI_API_KEY"):
        print("[gt-verify] FAIL: XAI_API_KEY not set.")
        return 1
    scan = await run_scan({"notes": "CLI liveness check."})
    if scan.ok:
        print(f"[gt-verify] PASS  latency={scan.latency_ms}ms  setups={len(scan.setups)}  bias={scan.market_bias}")
        for s in scan.setups:
            prob = f"  prob={s.pre_trade_probability}" if s.pre_trade_probability is not None else ""
            print(f"  {s.symbol} {s.direction.upper()} {s.setup_type}  R:R={s.r_r_ratio}  conf={int(s.confidence*100)}%{prob}")
            print(f"    catalyst: {s.catalyst[:100]}")
            print(f"    source:   {s.catalyst_source}")
            print(f"    entry={s.entry_price}  stop={s.stop_price}  target={s.target_price}")
        if scan.avoid_today:
            print(f"  avoid: {[a['symbol'] for a in scan.avoid_today]}")
        return 0
    print(f"[gt-verify] FAIL: {scan.error}")
    return 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Grok day-trading signal engine")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    if args.verify:
        raise SystemExit(asyncio.run(_verify_cli()))
    ap.print_help()
