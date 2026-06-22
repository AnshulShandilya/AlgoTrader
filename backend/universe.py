"""
universe.py
===========
Full UK + USA stock universe screener.

Runs every 60 minutes to find:
  - Boom stocks: price up >3% today with volume >1.5x average
  - Penny stock movers: price <$5 (US) or <100p (UK) with unusual volume/move
  - News-triggered spikes: cross-checked against Grok contract_watchlist

Results are cached in _universe_cache and served via /market/universe-movers.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

UNIVERSE_FILE = Path(__file__).parent / "data" / "universe_tickers.json"

# Boom thresholds
BOOM_PRICE_CHANGE_PCT  = 3.0   # >= 3% move today
BOOM_VOLUME_RATIO      = 1.5   # >= 1.5x average volume
PENNY_MAX_PRICE_USD    = 5.0   # US penny stock threshold
PENNY_MAX_PRICE_GBP_P  = 100.0 # UK penny stock threshold (pence)

# Curated always-on watchlist — used as fallback when Yahoo is rate-limited.
# These 30 tickers are fetched in a small batch (less likely to trigger rate limits).
CURATED_FALLBACK = [
    # UK FTSE blue chips
    "LLOY.L", "BARC.L", "BP.L", "HSBA.L", "AZN.L",
    "VOD.L", "GSK.L", "SHEL.L", "RIO.L", "ULVR.L",
    # US mega caps
    "AAPL", "NVDA", "TSLA", "META", "MSFT",
    "AMZN", "GOOGL", "INTC", "AMD", "ABBV",
    # High-beta / movers
    "HIVE", "CLSK", "MARA", "RIOT", "GME",
    # Crypto
    "BTC-USD", "ETH-USD", "SOL-USD",
    # Commodities
    "GC=F", "CL=F",
]

# Cache — updated every run
_universe_cache: dict = {
    "updated_at": None,
    "boom_stocks": [],
    "penny_movers": [],
    "all_movers": [],
    "total_screened": 0,
    "error": None,
}


def _load_tickers() -> dict[str, list[str]]:
    try:
        with open(UNIVERSE_FILE) as f:
            data = json.load(f)
        # T212-sourced groups use internal codes (e.g. $LLOY_EQ) that yfinance
        # doesn't recognise. Skip them — the curated groups already cover FTSE100/250.
        skip = {"uk_stocks_t212", "us_stocks_t212"}
        return {k: v for k, v in data.items() if k not in skip}
    except Exception as e:
        logger.warning(f"Could not load universe file: {e}")
        return {}


def _is_uk(symbol: str) -> bool:
    return symbol.upper().endswith(".L")


def _is_penny(symbol: str, price: float) -> bool:
    if _is_uk(symbol):
        # UK: yfinance returns price in £, convert to pence
        return (price * 100) < PENNY_MAX_PRICE_GBP_P
    return price < PENNY_MAX_PRICE_USD


_EU_SUFFIXES = (
    ".DE", ".PA", ".AS", ".MI", ".MC", ".ST", ".SW",
    ".CO", ".HE", ".OL", ".LS", ".BR", ".AT", ".WA",
)


def _classify(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith(".L"):
        return "uk_stock"
    if any(s.endswith(sfx.upper()) for sfx in _EU_SUFFIXES):
        return "eu_stock"
    if s.endswith("=F"):
        return "commodity"
    if s.endswith("=X"):
        return "forex"
    if s.endswith("-USD") or s.endswith("-BTC") or s.endswith("-ETH"):
        return "crypto"
    return "us_stock"


def screen_universe() -> dict:
    """
    Download daily bars for all universe tickers in bulk via yfinance,
    compute price change and volume ratio, return movers.
    """
    global _universe_cache

    tickers_by_group = _load_tickers()
    all_tickers: list[str] = []
    for group, tickers in tickers_by_group.items():
        all_tickers.extend(tickers)

    # Deduplicate
    all_tickers = list(dict.fromkeys(all_tickers))
    logger.info(f"Universe screener: downloading {len(all_tickers)} tickers...")

    boom_stocks   = []
    penny_movers  = []
    all_movers    = []
    screened      = 0
    errors        = 0

    # 60 days needed for 50d MA; includes gap (open column) and RSI slope
    try:
        raw = yf.download(
            all_tickers,
            period="60d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.error(f"Universe bulk download failed: {e}")
        _universe_cache["error"] = str(e)
        return _universe_cache

    # All candidates that pass Layer 1+2 (for stock_filter to score)
    filter_candidates_raw: list[dict] = []

    for sym in all_tickers:
        try:
            if len(all_tickers) == 1:
                df = raw
            else:
                df = raw[sym] if sym in raw.columns.get_level_values(0) else None
            if df is None or len(df) < 5:
                continue

            df.columns = [c.lower() for c in df.columns]
            close  = df["close"].dropna()
            volume = df["volume"].dropna()
            opens  = df["open"].dropna() if "open" in df.columns else close
            if len(close) < 5:
                continue

            price     = float(close.iloc[-1])
            prev      = float(close.iloc[-2])
            if prev <= 0 or price <= 0:
                continue

            pct_change = (price - prev) / prev * 100
            avg_vol    = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
            last_vol   = float(volume.iloc[-1])
            vol_ratio  = last_vol / avg_vol if avg_vol > 0 else 1.0

            screened += 1

            # ── Extra indicators for stock_filter ────────────────────────────

            # RSI (14)
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rsi_s = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
            rsi   = float(rsi_s.iloc[-1]) if len(rsi_s) >= 14 else 50.0

            # RSI slope — rising if current RSI > 3 bars ago
            rsi_3ago    = float(rsi_s.iloc[-4]) if len(rsi_s) >= 4 else rsi
            rsi_rising  = rsi > rsi_3ago

            # 50-day MA
            ma50_s      = close.rolling(50).mean()
            ma50        = float(ma50_s.iloc[-1]) if len(close) >= 50 else float(close.mean())
            above_ma50  = price > ma50 and not np.isnan(ma50)

            # 20-day high/low extreme
            high20      = float(close.iloc[-20:].max()) if len(close) >= 20 else price
            low20       = float(close.iloc[-20:].min()) if len(close) >= 20 else price
            at_20d_extreme = (price >= high20 * 0.99) or (price <= low20 * 1.01)

            # Gap: today's open vs yesterday's close
            today_open  = float(opens.iloc[-1]) if len(opens) >= 1 else price
            gap_pct     = (today_open - prev) / prev * 100 if prev > 0 else 0.0

            # Penny / boom flags
            is_boom  = abs(pct_change) >= BOOM_PRICE_CHANGE_PCT and vol_ratio >= BOOM_VOLUME_RATIO
            is_penny = _is_penny(sym, price)
            is_mover = abs(pct_change) >= 2.0 or vol_ratio >= 1.5

            entry = {
                "symbol":          sym,
                "asset_class":     _classify(sym),
                "price":           round(price, 4),
                "price_pence":     round(price * 100, 1) if _is_uk(sym) else None,
                "pct_change":      round(pct_change, 2),
                "vol_ratio":       round(vol_ratio, 2),
                "rsi":             round(rsi, 1),
                "rsi_rising":      rsi_rising,
                "above_ma50":      above_ma50,
                "at_20d_extreme":  at_20d_extreme,
                "gap_pct":         round(gap_pct, 2),
                "direction":       "up" if pct_change > 0 else "down",
                "is_penny":        is_penny,
                "is_boom":         is_boom,
                "scanned_at":      datetime.now(timezone.utc).isoformat(),
            }

            # Always store for filter if it has any activity
            if is_mover or is_boom or at_20d_extreme:
                filter_candidates_raw.append(entry)
                all_movers.append(entry)

            if is_boom:
                boom_stocks.append(entry)
            if is_penny and is_mover:
                penny_movers.append(entry)

        except Exception:
            errors += 1
            continue

    # ── Curated fallback when Yahoo rate-limits the bulk download ────────────
    # If fewer than 30 tickers came through, run a small second batch on the
    # curated watchlist so Grok always has meaningful candidates.
    if screened < 30:
        logger.warning(
            f"Bulk download returned only {screened} tickers — "
            "likely rate-limited. Running curated fallback batch…"
        )
        already = {e["symbol"] for e in filter_candidates_raw}
        fallback_needed = [s for s in CURATED_FALLBACK if s not in already]
        if fallback_needed:
            try:
                fallback_raw = yf.download(
                    fallback_needed,
                    period="60d",
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=True,
                    progress=False,
                    threads=False,   # single-threaded = gentler on rate limits
                )
                for sym in fallback_needed:
                    try:
                        if len(fallback_needed) == 1:
                            df = fallback_raw
                        else:
                            df = fallback_raw[sym] if sym in fallback_raw.columns.get_level_values(0) else None
                        if df is None or len(df) < 5:
                            continue

                        df.columns = [c.lower() for c in df.columns]
                        close  = df["close"].dropna()
                        volume = df["volume"].dropna()
                        opens  = df["open"].dropna() if "open" in df.columns else close
                        if len(close) < 5:
                            continue

                        price  = float(close.iloc[-1])
                        prev   = float(close.iloc[-2])
                        if prev <= 0 or price <= 0:
                            continue

                        pct_change = (price - prev) / prev * 100
                        avg_vol    = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
                        last_vol   = float(volume.iloc[-1])
                        vol_ratio  = last_vol / avg_vol if avg_vol > 0 else 1.0

                        screened += 1

                        delta      = close.diff()
                        gain       = delta.clip(lower=0).rolling(14).mean()
                        loss       = (-delta.clip(upper=0)).rolling(14).mean()
                        rsi_s      = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
                        rsi        = float(rsi_s.iloc[-1]) if len(rsi_s) >= 14 else 50.0
                        rsi_3ago   = float(rsi_s.iloc[-4]) if len(rsi_s) >= 4 else rsi
                        rsi_rising = rsi > rsi_3ago

                        ma50_s     = close.rolling(50).mean()
                        ma50       = float(ma50_s.iloc[-1]) if len(close) >= 50 else float(close.mean())
                        above_ma50 = price > ma50 and not np.isnan(ma50)

                        high20       = float(close.iloc[-20:].max()) if len(close) >= 20 else price
                        low20        = float(close.iloc[-20:].min()) if len(close) >= 20 else price
                        at_20d_extreme = (price >= high20 * 0.99) or (price <= low20 * 1.01)

                        today_open = float(opens.iloc[-1]) if len(opens) >= 1 else price
                        gap_pct    = (today_open - prev) / prev * 100 if prev > 0 else 0.0

                        is_boom    = abs(pct_change) >= BOOM_PRICE_CHANGE_PCT and vol_ratio >= BOOM_VOLUME_RATIO
                        is_penny   = _is_penny(sym, price)
                        is_mover   = abs(pct_change) >= 2.0 or vol_ratio >= 1.5

                        entry = {
                            "symbol":         sym,
                            "asset_class":    _classify(sym),
                            "price":          round(price, 4),
                            "price_pence":    round(price * 100, 1) if _is_uk(sym) else None,
                            "pct_change":     round(pct_change, 2),
                            "vol_ratio":      round(vol_ratio, 2),
                            "rsi":            round(rsi, 1),
                            "rsi_rising":     rsi_rising,
                            "above_ma50":     above_ma50,
                            "at_20d_extreme": at_20d_extreme,
                            "gap_pct":        round(gap_pct, 2),
                            "direction":      "up" if pct_change > 0 else "down",
                            "is_penny":       is_penny,
                            "is_boom":        is_boom,
                            "scanned_at":     datetime.now(timezone.utc).isoformat(),
                            "source":         "curated_fallback",
                        }

                        # Always include curated tickers in filter candidates (lower bar)
                        filter_candidates_raw.append(entry)
                        all_movers.append(entry)

                        if is_boom:
                            boom_stocks.append(entry)
                        if is_penny and is_mover:
                            penny_movers.append(entry)

                    except Exception:
                        errors += 1
                        continue
            except Exception as e:
                logger.warning(f"Curated fallback batch failed: {e}")

    # Sort by absolute price change
    boom_stocks.sort(key=lambda x: abs(x["pct_change"]), reverse=True)
    penny_movers.sort(key=lambda x: x["vol_ratio"], reverse=True)
    all_movers.sort(key=lambda x: abs(x["pct_change"]), reverse=True)

    _universe_cache = {
        "updated_at":             datetime.now(timezone.utc).isoformat(),
        "boom_stocks":            boom_stocks[:50],
        "penny_movers":           penny_movers[:50],
        "all_movers":             all_movers[:100],
        "filter_candidates_raw":  filter_candidates_raw,   # for stock_filter
        "total_screened":         screened,
        "errors":                 errors,
        "error":                  None,
    }

    logger.info(
        f"Universe screener done: {screened} screened, "
        f"{len(boom_stocks)} boom, {len(penny_movers)} penny movers, "
        f"{len(filter_candidates_raw)} filter candidates"
    )

    # Run 4-layer filter → update Grok candidate cache
    try:
        from stock_filter import update_grok_candidates
        update_grok_candidates(filter_candidates_raw)
    except Exception as e:
        logger.warning(f"stock_filter update failed: {e}")

    return _universe_cache


def get_universe_cache() -> dict:
    return _universe_cache


def boom_check(symbols: list[str]) -> list[dict]:
    """
    Quick boom check for a specific list of symbols (used by news panel).
    Returns boom status for each symbol.
    """
    if not symbols:
        return []

    results = []
    try:
        raw = yf.download(
            symbols,
            period="10d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.warning(f"boom_check download failed: {e}")
        return []

    for sym in symbols:
        try:
            if len(symbols) == 1:
                df = raw
            else:
                df = raw[sym] if sym in raw.columns.get_level_values(0) else None
            if df is None or len(df) < 3:
                results.append({"symbol": sym, "is_boom": False, "error": "no_data"})
                continue

            df.columns = [c.lower() for c in df.columns]
            close  = df["close"].dropna()
            volume = df["volume"].dropna()

            price      = float(close.iloc[-1])
            prev       = float(close.iloc[-2])
            pct_change = (price - prev) / prev * 100
            avg_vol    = float(volume.iloc[-10:].mean())
            vol_ratio  = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0
            is_boom    = abs(pct_change) >= BOOM_PRICE_CHANGE_PCT and vol_ratio >= BOOM_VOLUME_RATIO

            results.append({
                "symbol":     sym,
                "price":      round(price, 4),
                "pct_change": round(pct_change, 2),
                "vol_ratio":  round(vol_ratio, 2),
                "is_boom":    is_boom,
                "direction":  "up" if pct_change > 0 else "down",
            })
        except Exception as ex:
            results.append({"symbol": sym, "is_boom": False, "error": str(ex)})

    return results
