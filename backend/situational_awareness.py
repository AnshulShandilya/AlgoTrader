"""
situational_awareness.py
========================
Real-time market "situational awareness" layer for AlgoTrader Pro, powered by
xAI Grok (with server-side Web + X Live Search via the Responses API).

ROLE & SAFETY MODEL
-------------------
This is an ADVISORY layer. It augments the deterministic trading engine; it does
NOT replace it. The clamp logic below guarantees the model can only ever *reduce*
risk (recommended_risk_multiplier in [SA_RISK_FLOOR, 1.0]), never increase it, and
by default the recommendation is LOG-ONLY (SA_ENFORCE=false) so nothing autonomous
happens until you opt in. Stops, ATR position sizing, and the drawdown kill switch
remain authoritative at all times.

QUICK START
-----------
1) Get an xAI API key from the xAI console and export it yourself (this module
   never asks for or stores your key):
       export XAI_API_KEY="xai-..."
2) pip install httpx  (already present via FastAPI)
3) Verify it is running LIVE against the API:
       python situational_awareness.py --verify
   PASS prints a parsed assessment + latency + sources. FAIL prints the raw error.
4) Offline guardrail self-test (no network/key needed):
       python situational_awareness.py --selftest

IMPLEMENTATION NOTE
-------------------
xAI search_parameters work on the Responses API (/v1/responses), not on the
OpenAI-compatible /v1/chat/completions endpoint where they were deprecated.
The Responses API uses `input` (not `messages`) and returns output in
`output[0].content[0].text`.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

# --------------------------------------------------------------------------- #
# Configuration (all via environment so models/keys are never hard-coded)
# --------------------------------------------------------------------------- #
XAI_BASE_URL = os.getenv("XAI_BASE_URL", "https://api.x.ai")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-4.3")
SA_RISK_FLOOR = float(os.getenv("SA_RISK_FLOOR", "0.25"))   # lowest size the LLM may suggest
SA_ENFORCE = os.getenv("SA_ENFORCE", "false").lower() == "true"  # default: advisory/log-only
SA_SEARCH_MODE = os.getenv("SA_SEARCH_MODE", "on")          # on | auto | off
SA_MAX_SEARCH_RESULTS = int(os.getenv("SA_MAX_SEARCH_RESULTS", "20"))
SA_TIMEOUT_S = float(os.getenv("SA_TIMEOUT_S", "90"))

# --------------------------------------------------------------------------- #
# The system prompt — this is the "instructions for Grok". Source of truth.
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You are GROK-SA, the real-time market situational-awareness layer for AlgoTrader Pro, an automated paper-trading research system. You are an ADVISORY analyst. You do NOT place trades, and you never instruct the system to remove or widen stop-losses, disable the drawdown kill switch, increase position size beyond its normal sizing, or override any risk limit. The deterministic risk engine (mandatory stops, ATR-based position sizing, drawdown kill switch) is always authoritative; your job is to inform its risk posture, not to replace it.

Your task each run: using the live Web and X (Twitter) search tools available to you, gather current, decision-relevant information about the provided watchlist symbols, their asset classes (US stocks, UK stocks, crypto, commodities), and the broader macro environment, then return a single structured assessment.

HARD RULES:
- Respond with ONE valid JSON object and nothing else. No markdown, no code fences, no commentary outside the JSON.
- "recommended_risk_multiplier" is a DE-RISKING dial only: a float in [0.0, 1.0], where 1.0 = normal risk (defer fully to the deterministic engine) and lower = reduce exposure. You may NEVER recommend increasing risk above 1.0.
- Recommend a lower multiplier or "risk_off" when you find: imminent or active high-impact scheduled events (FOMC, CPI, NFP/jobs data, central-bank decisions, major earnings, large crypto token unlocks or listings), unusually thin liquidity, conflicting/whipsaw signals, or sharp moves driven by unverified rumor.
- Distinguish verified, sourced news from rumor. Treat single unsourced social posts with caution and never base a strong call on them. Cite the sources you used.
- Be calibrated about uncertainty. If information is sparse or conflicting, set "confidence" low, "market_regime" to "unknown", and "recommended_risk_multiplier" near 1.0 (defer to the deterministic engine rather than guessing).
- This output is for an automated research/paper-trading system. It is NOT financial advice for any person.

Return JSON with EXACTLY these fields:
{
  "timestamp_utc": "<ISO8601 current UTC>",
  "market_regime": "strong_uptrend|uptrend|range|downtrend|strong_downtrend|high_volatility|unknown",
  "risk_posture": "risk_on|neutral|risk_off",
  "recommended_risk_multiplier": <float 0.0-1.0>,
  "macro_summary": "<1-3 sentences>",
  "event_risk": [{"event": "<name>", "when_utc": "<ISO8601 or 'active'>", "impact": "high|medium|low", "affected": ["<symbol or asset class>"]}],
  "per_symbol": {"<SYMBOL>": {"bias": "bullish|neutral|bearish", "sentiment": <float -1.0 to 1.0>, "note": "<short>", "catalysts": ["<short>"]}},
  "confidence": <float 0.0-1.0>,
  "sources": ["<url>"],
  "caveats": "<short>"
}"""

# --------------------------------------------------------------------------- #
# Liveness / health state (observable so you can prove it is "running live")
# --------------------------------------------------------------------------- #
HEALTH: dict[str, Any] = {
    "model": GROK_MODEL,
    "enforce": SA_ENFORCE,
    "search_mode": SA_SEARCH_MODE,
    "risk_floor": SA_RISK_FLOOR,
    "api_endpoint": "responses",  # using /v1/responses (not /v1/chat/completions)
    "last_success_utc": None,
    "last_latency_ms": None,
    "last_error": None,
}

# In-memory cache of the most recent assessment (survives server restarts
# only for the current process; persisted via event_log for history)
_last_assessment: Optional[SituationAssessment] = None


@dataclass
class SituationAssessment:
    market_regime: str = "unknown"
    risk_posture: str = "neutral"
    recommended_risk_multiplier: float = 1.0
    macro_summary: str = ""
    event_risk: list = field(default_factory=list)
    per_symbol: dict = field(default_factory=dict)
    confidence: float = 0.0
    sources: list = field(default_factory=list)
    caveats: str = ""
    timestamp_utc: str = ""
    ok: bool = True
    error: Optional[str] = None
    latency_ms: Optional[int] = None

    @classmethod
    def safe_default(cls, reason: str) -> "SituationAssessment":
        # On ANY failure/uncertainty we fall back to the deterministic engine:
        # multiplier 1.0 (no change), neutral, unknown regime. The LLM is never
        # a single point of failure that halts trading or silently changes size.
        return cls(
            market_regime="unknown",
            risk_posture="neutral",
            recommended_risk_multiplier=1.0,
            confidence=0.0,
            caveats=f"safe_default: {reason}",
            timestamp_utc=_utcnow_iso(),
            ok=False,
            error=reason,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def clamp_multiplier(value: Any) -> float:
    """Guardrail: coerce to a de-risk-only multiplier in [SA_RISK_FLOOR, 1.0].
    Non-numeric or out-of-range -> safest sensible value. The LLM can never
    push risk above the deterministic sizing (cap 1.0)."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return 1.0
    if x != x:  # NaN
        return 1.0
    return max(SA_RISK_FLOOR, min(1.0, x))


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers the model may add."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


def _parse_assessment(content: str, latency_ms: int) -> SituationAssessment:
    data = json.loads(_strip_fences(content))  # raises on bad JSON -> caught by caller
    a = SituationAssessment(
        market_regime=str(data.get("market_regime", "unknown")),
        risk_posture=str(data.get("risk_posture", "neutral")),
        recommended_risk_multiplier=clamp_multiplier(data.get("recommended_risk_multiplier", 1.0)),
        macro_summary=str(data.get("macro_summary", "")),
        event_risk=data.get("event_risk", []) or [],
        per_symbol=data.get("per_symbol", {}) or {},
        confidence=float(data.get("confidence", 0.0) or 0.0),
        sources=data.get("sources", []) or [],
        caveats=str(data.get("caveats", "")),
        timestamp_utc=str(data.get("timestamp_utc", _utcnow_iso())),
        ok=True,
        latency_ms=latency_ms,
    )
    return a


def _build_user_message(context: dict) -> str:
    """`context` is whatever your engine already knows. Keep it compact."""
    payload = {
        "asof_utc": context.get("asof_utc", _utcnow_iso()),
        "asset_classes": context.get("asset_classes", ["us_stocks", "uk_stocks", "crypto", "commodities"]),
        "watchlist": context.get("watchlist", []),
        "open_positions": context.get("open_positions", []),
        "recent_signals": context.get("recent_signals", []),
        "notes": context.get("notes", ""),
    }
    return (
        "Assess the current market situation for the following AlgoTrader Pro context "
        "and return the JSON object exactly as specified in your instructions.\n\n"
        + json.dumps(payload, default=str, ensure_ascii=False)
    )


def _extract_citations(raw_response: dict) -> list:
    """Pull citation URLs from the Responses API output object."""
    citations = []
    for item in raw_response.get("output", []):
        for block in item.get("content", []):
            for ann in block.get("annotations", []):
                url = ann.get("url") or ann.get("source_url", "")
                if url and url not in citations:
                    citations.append(url)
    return citations


def _extract_text(raw_response: dict) -> str:
    """Extract the text content from a Responses API response object."""
    for item in raw_response.get("output", []):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    return block.get("text", "")
    return ""


async def assess_situation(
    context: dict,
    *,
    model: str = GROK_MODEL,
    _http_client: Any = None,
) -> SituationAssessment:
    """Run one live situational-awareness assessment via xAI Responses API.
    Always returns a valid SituationAssessment; on any error returns a safe
    default (multiplier 1.0) — this function never raises."""
    global _last_assessment

    try:
        import httpx
    except ImportError:
        return SituationAssessment.safe_default("httpx package not installed")

    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        return SituationAssessment.safe_default("XAI_API_KEY not set")

    payload: dict = {
        "model": model,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_message(context)},
        ],
    }

    # Agent Tools API — web_search and x_search (both non-deprecated)
    if SA_SEARCH_MODE != "off":
        payload["tools"] = [
            {"type": "web_search"},
            {"type": "x_search"},
        ]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{XAI_BASE_URL}/v1/responses"

    started = time.perf_counter()
    try:
        owns_client = _http_client is None
        if owns_client:
            _http_client = httpx.AsyncClient(timeout=SA_TIMEOUT_S)

        try:
            resp = await _http_client.post(url, headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - started) * 1000)

            if resp.status_code != 200:
                error_body = resp.text[:400]
                HEALTH["last_error"] = f"HTTP {resp.status_code}: {error_body}"
                return SituationAssessment.safe_default(HEALTH["last_error"])

            raw = resp.json()
            content = _extract_text(raw)
            if not content:
                HEALTH["last_error"] = "empty response text from Responses API"
                return SituationAssessment.safe_default(HEALTH["last_error"])

            assessment = _parse_assessment(content, latency_ms)

            # Merge citations from annotations if model didn't include them in JSON
            if not assessment.sources:
                assessment.sources = _extract_citations(raw)

            _last_assessment = assessment
            HEALTH["last_success_utc"] = _utcnow_iso()
            HEALTH["last_latency_ms"] = latency_ms
            HEALTH["last_error"] = None
            return assessment

        finally:
            if owns_client:
                await _http_client.aclose()

    except Exception as exc:
        HEALTH["last_error"] = f"{type(exc).__name__}: {exc}"
        return SituationAssessment.safe_default(HEALTH["last_error"])


def get_last_assessment() -> Optional[SituationAssessment]:
    """Return the most recent in-memory assessment (None if never run)."""
    return _last_assessment


def effective_risk_multiplier(assessment: SituationAssessment) -> float:
    """What the deterministic sizing layer should multiply by.
    Advisory by default (SA_ENFORCE=false): always returns 1.0.
    Set SA_ENFORCE=true to let the multiplier actually reduce position size."""
    if not SA_ENFORCE:
        return 1.0
    return assessment.recommended_risk_multiplier


# --------------------------------------------------------------------------- #
# FastAPI router (registers automatically when imported by main.py)
# --------------------------------------------------------------------------- #
try:
    from fastapi import APIRouter

    router = APIRouter(prefix="/situational-awareness", tags=["situational-awareness"])

    @router.get("/health")
    async def sa_health() -> dict:
        return HEALTH

    @router.get("/latest")
    async def sa_latest() -> dict:
        """Return the most recent cached assessment without calling the API."""
        a = get_last_assessment()
        if a is None:
            return {"available": False, "message": "No assessment run yet this session"}
        return {"available": True, **a.to_dict()}

    @router.post("")
    async def sa_assess(context: Optional[dict] = None) -> dict:
        """Trigger an on-demand assessment (calls Grok live — may take ~5-30s)."""
        result = await assess_situation(context or {})
        return result.to_dict()

except ImportError:
    router = None


# --------------------------------------------------------------------------- #
# APScheduler integration
# --------------------------------------------------------------------------- #
async def scheduled_assessment_job(
    get_context: Callable[[], dict],
    on_result: Callable[[SituationAssessment], Any],
) -> None:
    """Wire into APScheduler, e.g.:
        scheduler.add_job(scheduled_assessment_job, "interval", minutes=20,
                          args=[build_context, log_assessment_to_activity_feed])
    """
    context = get_context()
    assessment = await assess_situation(context)
    result = on_result(assessment)
    if asyncio.iscoroutine(result):
        await result


# --------------------------------------------------------------------------- #
# CLI: --verify (live) and --selftest (offline guardrail check)
# --------------------------------------------------------------------------- #
async def _verify() -> int:
    endpoint = f"{XAI_BASE_URL}/v1/responses"
    print(f"[verify] model={GROK_MODEL}  endpoint={endpoint}  search={SA_SEARCH_MODE}")
    if not os.getenv("XAI_API_KEY"):
        print("[verify] FAIL: XAI_API_KEY is not set. `export XAI_API_KEY=...` and retry.")
        return 1
    context = {
        "watchlist": ["BTC-USD", "AAPL", "SPY", "GC=F"],
        "asset_classes": ["crypto", "us_stocks", "us_etf", "commodities"],
        "open_positions": [],
        "notes": "Liveness check — please search for latest news on each symbol.",
    }
    a = await assess_situation(context)
    if a.ok:
        print(f"[verify] PASS  latency={a.latency_ms}ms")
        print(f"         regime={a.market_regime}  posture={a.risk_posture}  "
              f"confidence={int(a.confidence * 100)}%")
        print(f"         risk_mult={a.recommended_risk_multiplier:.2f}  "
              f"enforce={SA_ENFORCE} -> applied={effective_risk_multiplier(a):.2f}")
        print(f"         sources({len(a.sources)}): {a.sources[:5]}")
        print(f"         macro: {a.macro_summary[:300]}")
        if a.per_symbol:
            for sym, info in list(a.per_symbol.items())[:4]:
                print(f"         {sym}: {info.get('bias')} sent={info.get('sentiment',0):+.2f} "
                      f"— {info.get('note','')[:80]}")
        return 0
    print(f"[verify] FAIL: {a.error}")
    return 1


def _selftest() -> int:
    """No network/key needed: proves the guardrails behave."""
    assert clamp_multiplier(2.0) == 1.0, "must cap above-1.0 to 1.0 (no risk increase)"
    assert clamp_multiplier(-5) == SA_RISK_FLOOR, "must clamp below floor to floor"
    assert clamp_multiplier("not-a-number") == 1.0, "non-numeric -> 1.0 (defer)"
    assert clamp_multiplier(0.5) == max(SA_RISK_FLOOR, 0.5)
    # bad JSON / failure path yields a safe default that does NOT change risk
    sd = SituationAssessment.safe_default("simulated outage")
    assert sd.recommended_risk_multiplier == 1.0 and sd.ok is False
    # a well-formed but over-aggressive model response gets clamped on parse
    sample = json.dumps({
        "timestamp_utc": _utcnow_iso(),
        "market_regime": "strong_uptrend",
        "risk_posture": "risk_on",
        "recommended_risk_multiplier": 3.5,   # model tries to amplify risk
        "macro_summary": "test",
        "event_risk": [],
        "per_symbol": {"BTC-USD": {"bias": "bullish", "sentiment": 0.6, "note": "x", "catalysts": []}},
        "confidence": 0.7,
        "sources": ["https://example.com"],
        "caveats": "test",
    })
    parsed = _parse_assessment(sample, 123)
    assert parsed.recommended_risk_multiplier == 1.0, "parse must clamp amplification to 1.0"
    print("[selftest] PASS: guardrails hold (de-risk only, safe defaults, failure-neutral).")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Grok situational-awareness layer")
    ap.add_argument("--verify", action="store_true", help="live API liveness check")
    ap.add_argument("--selftest", action="store_true", help="offline guardrail check")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    if args.verify:
        raise SystemExit(asyncio.run(_verify()))
    ap.print_help()
