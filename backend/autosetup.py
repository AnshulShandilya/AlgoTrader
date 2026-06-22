"""
Auto-setup engine — runs on startup and every 4 hours.

Source for candidates: the universe screener cache (filter_candidates_raw).
This covers US stocks, UK stocks, crypto, and commodities and works 24/7.
Falls back to the Alpaca intraday scanner if the universe cache is empty.

Three trade types are assigned based on asset class + market conditions,
each with appropriately spaced stops and targets that respect the 2:1 R:R floor.
"""
import logging
from datetime import datetime
from typing import Optional, List
from sqlalchemy import select
from database import SessionLocal
from models import Strategy, StrategyStatus, BotSettings
from scanner import scan_market, format_scan_results

log = logging.getLogger("autosetup")

AUTO_TAG = "[AUTO]"
TOP_N = 10

# Minimum pre-trade signal quality to include in auto-setup
MIN_UNIVERSE_SCORE = 30   # quick_score ≥ 30 to qualify

# Risk params per trade type — all enforce ≥ 2:1 reward:risk
#
# 5-tier system:
#   scalping        5Min — US/UK/EU stocks, crypto, commodities; open/close windows
#   scalping_forex  5Min — forex majors/minors; London & London-NY overlap windows only
#   day_trading    15Min — all markets; avoids midday chop; EOD flatten mandatory
#   intraday_swing  1Hour — held through session; full market-hours gate
#   swing_trading   1Day — CronTrigger fires at EOD close on a complete bar
RISK_BY_TYPE = {
    "scalping": {
        "stop_loss_pct":     0.3,     # 0.3% tight stop — stocks/crypto/commodities
        "take_profit_pct":   0.75,    # 0.75% — 2.5:1 R:R
        "position_size_pct": 3.0,     # 3% — larger to make 0.75% targets meaningful
        "timeframe":         "5Min",
        "template":          "scalping",
    },
    "scalping_forex": {
        "stop_loss_pct":     0.1,     # ~10 pips (EURUSD at 1.10) — very tight
        "take_profit_pct":   0.25,    # ~25 pips — 2.5:1 R:R
        "position_size_pct": 5.0,     # 5% notional — larger to compensate small pip moves
        "timeframe":         "5Min",
        "template":          "scalping",
    },
    "day_trading": {
        "stop_loss_pct":     1.0,     # 1.0%
        "take_profit_pct":   2.5,     # 2.5% — 2.5:1 R:R
        "position_size_pct": 2.0,
        "timeframe":         "15Min",
        "template":          "momentum",
    },
    "intraday_swing": {
        "stop_loss_pct":     1.5,     # 1.5%
        "take_profit_pct":   3.5,     # 3.5% — 2.3:1 R:R
        "position_size_pct": 2.0,
        "timeframe":         "1Hour",
        "template":          "momentum",
    },
    "swing_trading": {
        "stop_loss_pct":     3.0,     # 3.0%
        "take_profit_pct":   6.0,     # 6.0% — 2:1 R:R
        "position_size_pct": 2.0,
        "timeframe":         "1Day",
        "template":          "macd",
    },
}

SCALP_PARAMS = {
    "fast_ema": 9, "slow_ema": 21,
    "rsi_period": 14, "rsi_buy_level": 45, "rsi_sell_level": 55,
    "vol_multiplier": 1.3,
}
MOMENTUM_PARAMS = {
    "fast_ema": 12, "slow_ema": 26,
    "rsi_period": 14, "rsi_buy_level": 50, "rsi_sell_level": 60,
    "vol_multiplier": 1.2,
}
MACD_PARAMS = {
    "fast_ema": 12, "slow_ema": 26, "signal_ema": 9,
    "rsi_period": 14, "rsi_buy_level": 45, "rsi_sell_level": 55,
    "vol_multiplier": 1.1,
}
PARAMS_BY_TEMPLATE = {
    "scalping": SCALP_PARAMS,
    "momentum": MOMENTUM_PARAMS,
    "macd":     MACD_PARAMS,
    # intraday_swing and day_trading both use momentum template
}

_last_scan_results: List[dict] = []
_last_scan_time: Optional[datetime] = None


def get_last_scan() -> dict:
    return {
        "results": _last_scan_results,
        "scanned_at": _last_scan_time.isoformat() if _last_scan_time else None,
        "count": len(_last_scan_results),
    }


def _quick_score(item: dict) -> float:
    """Simple 0–100 score from universe screener fields."""
    score = 0.0
    pct = abs(item.get("pct_change", 0))
    score += min(pct * 5, 30)
    vr = item.get("vol_ratio", 1.0)
    if vr > 1.0:
        score += min((vr - 1.0) * 15, 25)
    if item.get("rsi_rising"):
        score += 10
    if item.get("above_ma50"):
        score += 15
    if item.get("at_20d_extreme"):
        score += 10
    if abs(item.get("gap_pct", 0)) > 1.0:
        score += 5
    return min(round(score, 1), 100)


def _determine_trade_type(item: dict) -> str:
    """
    Classify each candidate into one of five tiers across all markets.

    scalping_forex  5Min — forex pairs; tight 0.1%/0.25% SL/TP; London/NY overlap
    scalping        5Min — US/UK/EU stocks, crypto, commodities; 0.3%/0.75%; open+close rush
    day_trading    15Min — all markets; elevated RVOL; avoids midday chop; EOD flatten
    intraday_swing  1Hour — moderate momentum; full session; no EOD flatten
    swing_trading   1Day — fires at market close; low-velocity names
    """
    asset_class = item.get("asset_class", "us_stock")
    pct         = abs(item.get("pct_change", 0))
    vr          = item.get("vol_ratio", 1.0)
    above_ma50  = item.get("above_ma50", False)
    is_boom     = item.get("is_boom", False)
    gap_pct     = abs(item.get("gap_pct", 0))

    # ── Forex: scalping is the PRIMARY strategy ───────────────────────────────
    # Majors & minors trade in pips; any intraday momentum qualifies for a scalp
    if asset_class == "forex":
        if vr >= 1.2 or pct >= 0.1:
            return "scalping_forex"
        return "day_trading"

    # ── Crypto: 24/5, high volatility ────────────────────────────────────────
    if asset_class == "crypto":
        if vr >= 2.0 or is_boom or gap_pct >= 2.0:
            return "scalping"
        if pct >= 2.0 or vr >= 1.5:
            return "day_trading"
        return "intraday_swing"

    # ── UK stocks: wider spreads — scalp on strong momentum days ─────────────
    if asset_class == "uk_stock":
        if (vr >= 2.0 or is_boom) and pct >= 1.5:
            return "scalping"
        if pct >= 2.0 and vr >= 1.5:
            return "day_trading"
        if pct >= 1.0:
            return "intraday_swing"
        return "swing_trading"

    # ── European stocks (.DE, .PA, .AS, .MI, .MC etc.) ───────────────────────
    if asset_class == "eu_stock":
        if (vr >= 2.0 or is_boom) and pct >= 1.5:
            return "scalping"
        if pct >= 2.0 and vr >= 1.5:
            return "day_trading"
        if pct >= 1.0:
            return "intraday_swing"
        return "swing_trading"

    # ── Commodities (gold, oil, natural gas, grains etc.) ────────────────────
    if asset_class in ("commodity", "etf"):
        if (vr >= 2.0 or is_boom) and pct >= 0.5:
            return "scalping"
        if vr >= 1.5 and pct >= 1.0:
            return "intraday_swing"
        return "swing_trading"

    # ── US stocks (NYSE / NASDAQ) ─────────────────────────────────────────────
    if (vr >= 3.0 or is_boom or gap_pct >= 2.0) and pct >= 1.5:
        return "scalping"
    if vr >= 2.0 and pct >= 1.2:
        return "day_trading"
    if above_ma50 and pct >= 0.7:
        return "intraday_swing"
    return "swing_trading"


async def run_auto_setup(force: bool = False) -> dict:
    """
    Creates [AUTO] strategies from the highest-scoring universe candidates.

    1. Reads the universe screener cache (filter_candidates_raw) — works 24/7, multi-asset.
    2. If cache is empty, triggers a fresh screen.
    3. Falls back to the Alpaca intraday scanner as last resort.
    4. Assigns trade_type (scalping / day_trading / swing_trading) per candidate.
    5. Creates strategies with properly spaced SL/TP (≥ 2:1 R:R).
    """
    global _last_scan_results, _last_scan_time

    # ── Step 1: Get universe candidates ────────────────────────────────────────
    from universe import get_universe_cache, screen_universe
    cache = get_universe_cache()
    candidates = list(cache.get("filter_candidates_raw", []))

    if len(candidates) < 5:
        log.info("Universe cache empty — running fresh screen…")
        try:
            result = screen_universe()
            candidates = list(result.get("filter_candidates_raw", []))
        except Exception as e:
            log.warning(f"Universe screener error: {e}")
            candidates = []

    # ── Step 2: Score and rank ──────────────────────────────────────────────────
    if candidates:
        for c in candidates:
            c["_score"] = _quick_score(c)
        candidates = [c for c in candidates if c["_score"] >= MIN_UNIVERSE_SCORE]
        candidates.sort(key=lambda x: x["_score"], reverse=True)
        top_candidates = candidates[:TOP_N]

        log.info(
            f"Universe source: {len(top_candidates)} candidates selected "
            f"(scored ≥ {MIN_UNIVERSE_SCORE} from {len(candidates)} qualifying)"
        )

        # Format results for display
        _last_scan_results = [
            {
                "rank":        i + 1,
                "symbol":      c["symbol"],
                "asset_class": c.get("asset_class", "unknown"),
                "score":       c["_score"],
                "pct_change":  c.get("pct_change", 0),
                "vol_ratio":   c.get("vol_ratio", 1),
                "rsi":         c.get("rsi", 50),
                "direction":   c.get("direction", "up"),
                "trade_type":  _determine_trade_type(c),
                "bias":        "bull" if c.get("direction") == "up" else "bear",
            }
            for i, c in enumerate(top_candidates)
        ]
        _last_scan_time = datetime.utcnow()

    # ── Step 3: Fallback to Alpaca intraday scanner ────────────────────────────
    if not candidates or not top_candidates:
        log.warning("Universe candidates empty — falling back to Alpaca intraday scanner")
        async with SessionLocal() as db:
            settings_result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
            settings = settings_result.scalar_one_or_none()
            if not settings or not settings.alpaca_api_key:
                return {"status": "skipped", "reason": "no_api_keys_and_no_universe_data"}

            from broker import AlpacaBroker
            broker = AlpacaBroker(settings.alpaca_api_key, settings.alpaca_secret_key, settings.paper_trading)

        try:
            # include_stocks=True here — session gating in scheduler handles non-market hours
            top_assets = await scan_market(broker, top_n=TOP_N, include_stocks=True)
        except Exception as e:
            log.error(f"Fallback scan failed: {e}")
            return {"status": "error", "reason": str(e)}

        if not top_assets:
            return {"status": "error", "reason": "no_assets_scored"}

        _last_scan_results = format_scan_results(top_assets)
        _last_scan_time = datetime.utcnow()

        # Convert to candidate-style dicts
        top_candidates = [
            {
                "symbol":      a.symbol,
                "asset_class": "crypto" if "/" in a.symbol else "us_stock",
                "_score":      a.score,
                "pct_change":  a.momentum_pct,
                "vol_ratio":   a.volume_ratio,
                "above_ma50":  False,
                "is_boom":     a.volume_ratio >= 2.0,
            }
            for a in top_assets
        ]

    # ── Step 4: Wipe old auto strategies ─────────────────────────────────────
    async with SessionLocal() as db:
        result = await db.execute(select(Strategy).where(Strategy.name.like(f"{AUTO_TAG}%")))
        old = result.scalars().all()
        for s in old:
            await db.delete(s)
        await db.commit()
        log.info(f"Removed {len(old)} old auto strategies")

        # ── Step 5: Create fresh strategies ──────────────────────────────────
        created = []
        TYPE_SHORT = {
            "scalping":        "SCALP",
            "scalping_forex":  "SCALP-FX",
            "day_trading":     "DAY",
            "intraday_swing":  "SWING-1H",
            "swing_trading":   "SWING",
        }
        for rank, candidate in enumerate(top_candidates, start=1):
            symbol     = candidate["symbol"]
            trade_type = _determine_trade_type(candidate)
            cfg        = RISK_BY_TYPE[trade_type]
            template   = cfg["template"]
            # intraday_swing uses momentum params
            param_key  = template if template in PARAMS_BY_TEMPLATE else "momentum"
            params     = {
                **PARAMS_BY_TEMPLATE[param_key],
                "timeframe":   cfg["timeframe"],
                "trade_type":  trade_type,   # persisted so scheduler can read it
            }
            risk = {
                "stop_loss_pct":     cfg["stop_loss_pct"],
                "take_profit_pct":   cfg["take_profit_pct"],
                "position_size_pct": cfg["position_size_pct"],
            }

            strategy = Strategy(
                name=f"{AUTO_TAG} #{rank} {symbol} [{TYPE_SHORT[trade_type]}]",
                template=template,
                symbol=symbol,
                parameters=params,
                risk_config=risk,
                status=StrategyStatus.active,
            )
            db.add(strategy)
            created.append(symbol)
            log.info(
                f"  [{rank}] {symbol} → {trade_type} "
                f"SL {cfg['stop_loss_pct']}% / TP {cfg['take_profit_pct']}% "
                f"({cfg['timeframe']})"
            )

        await db.commit()
        log.info(f"Created {len(created)} auto strategies")

    # ── Step 6: Reload scheduler ──────────────────────────────────────────────
    from scheduler import reload_jobs
    await reload_jobs()

    log.info(f"Auto-setup complete — {len(created)} strategies active")

    from routers.events import write_event
    top_syms = [r["symbol"] for r in (_last_scan_results or [])[:3]]
    await write_event(
        type="scan", severity="info",
        title=f"Auto-setup complete — {len(created)} strategies deployed",
        body=(
            f"Top picks: {', '.join(top_syms) or 'none'} · "
            f"Asset classes: {', '.join(sorted({c.get('asset_class','?') for c in top_candidates}))}"
        ),
        meta={
            "total_candidates": len(candidates),
            "deployed": len(created),
            "symbols": created,
            "source": "universe_screener" if candidates else "alpaca_scanner",
        },
    )

    return {
        "status":   "ok",
        "created":  len(created),
        "symbols":  created,
        "top_assets": _last_scan_results,
    }
