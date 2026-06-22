"""
fetch_t212_directory.py
=======================
Pulls the complete instrument catalog from the Trading 212 API and saves:
  - t212_tickers.json      → flat list of ticker strings (for watcher loops)
  - t212_catalog.json      → detailed metadata keyed by ticker
  - data/universe_tickers.json (updated) → merges T212 instruments into the
                             existing universe so the live screener picks them up

Usage:
  python fetch_t212_directory.py            # normal run
  python fetch_t212_directory.py --merge    # also merges into universe_tickers.json

Requirements:
  T212_API_KEY and T212_ENV must be set in .env (see instructions below).

How to get your API key:
  1. Log into Trading 212 web (trading212.com) or the mobile app.
  2. Go to Settings → API → Create a new token.
  3. Grant 'Read' scope only — never grant Withdrawal permission.
  4. Paste the token into .env as T212_API_KEY=<your_token>.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────

# T212 uses HTTP Basic Auth: base64(API_KEY_ID:API_SECRET)
API_KEY_ID = os.getenv("T212_API_KEY_ID", "")
API_SECRET = os.getenv("T212_API_SECRET", "")
T212_ENV   = os.getenv("T212_ENV", "live").lower()

def _build_auth_header() -> str:
    token = base64.b64encode(f"{API_KEY_ID}:{API_SECRET}".encode()).decode()
    return f"Basic {token}"

BASE_URL = (
    "https://live.trading212.com"
    if T212_ENV == "live"
    else "https://demo.trading212.com"
)

ENDPOINT = f"{BASE_URL}/api/v0/equity/metadata/instruments"

OUTPUT_DIR = Path(__file__).parent
TICKERS_FILE  = OUTPUT_DIR / "t212_tickers.json"
CATALOG_FILE  = OUTPUT_DIR / "t212_catalog.json"
UNIVERSE_FILE = OUTPUT_DIR / "data" / "universe_tickers.json"

# Asset-type labels T212 uses — map to our internal asset_class
T212_TYPE_MAP = {
    "STOCK":   "us_stock",
    "ETF":     "etf",
    "FOREX":   "forex",
    "CRYPTO":  "crypto",
    "FUTURE":  "commodity",
    "BOND":    "bond",
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _classify_currency(currency: str | None) -> str:
    """Rough guess at asset class from listing currency when type tag absent."""
    if currency in ("GBp", "GBX", "GBP"):
        return "uk_stock"
    if currency in ("USD", "CAD", "AUD"):
        return "us_stock"
    if currency == "EUR":
        return "eu_stock"
    return "us_stock"


def _is_uk(item: dict) -> bool:
    """Detect LSE-listed instruments."""
    exchange = (item.get("exchange") or "").upper()
    currency = item.get("currencyCode") or ""
    ticker   = (item.get("ticker") or "")
    return (
        "LSE" in exchange
        or "LONDON" in exchange
        or currency in ("GBp", "GBX", "GBP")
        or ticker.endswith("_EQ")   # T212 format for UK equities
    )


# ─── Main ────────────────────────────────────────────────────────────────────

def fetch_all_trading212_stocks(merge_into_universe: bool = False) -> None:
    if not API_KEY_ID or not API_SECRET:
        print(
            "ERROR: T212_API_KEY_ID or T212_API_SECRET is not set.\n"
            "  Add both to backend/.env:\n"
            "    T212_API_KEY_ID=<your_key_id>\n"
            "    T212_API_SECRET=<your_security_key>\n"
        )
        sys.exit(1)

    print(f"Connecting to Trading 212 ({T212_ENV.upper()}) — {ENDPOINT}")
    # T212 uses HTTP Basic Auth: Authorization: Basic base64(key_id:secret)
    headers = {"Authorization": _build_auth_header()}

    t0 = time.time()
    try:
        response = requests.get(ENDPOINT, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else "?"
        print(f"HTTP {status} error: {e}")
        if status == 401:
            print("  → API key invalid or expired. Regenerate in Trading 212 settings.")
        elif status == 403:
            print("  → API key lacks 'Read' permission for instruments.")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Network error: {e}")
        sys.exit(1)

    instruments = response.json()
    elapsed = time.time() - t0
    print(f"Retrieved {len(instruments)} instruments in {elapsed:.1f}s")

    # ── Parse and classify ────────────────────────────────────────────────────
    ticker_list: list[str] = []
    detailed_catalog: dict = {}

    # Groupings for universe merge
    by_class: dict[str, list[str]] = {
        "uk_stocks_t212": [],
        "us_stocks_t212": [],
        "eu_stocks_t212": [],
        "etf_t212":       [],
        "crypto_t212":    [],
        "forex_t212":     [],
        "commodity_t212": [],
    }

    for item in instruments:
        ticker = (item.get("ticker") or "").strip()
        if not ticker:
            continue

        short_name = item.get("shortName") or item.get("name") or ""
        currency   = item.get("currencyCode") or ""
        isin       = item.get("isin") or ""
        item_type  = (item.get("type") or "").upper()
        exchange   = item.get("exchange") or ""

        # Determine asset class
        if item_type in T212_TYPE_MAP:
            asset_class = T212_TYPE_MAP[item_type]
        elif _is_uk(item):
            asset_class = "uk_stock"
        else:
            asset_class = _classify_currency(currency)

        # Override for UK
        if _is_uk(item):
            asset_class = "uk_stock"

        ticker_list.append(ticker)
        detailed_catalog[ticker] = {
            "name":        short_name,
            "currency":    currency,
            "isin":        isin,
            "exchange":    exchange,
            "type":        item_type,
            "asset_class": asset_class,
        }

        # Group for universe
        group_key = {
            "uk_stock":  "uk_stocks_t212",
            "us_stock":  "us_stocks_t212",
            "eu_stock":  "eu_stocks_t212",
            "etf":       "etf_t212",
            "crypto":    "crypto_t212",
            "forex":     "forex_t212",
            "commodity": "commodity_t212",
        }.get(asset_class, "us_stocks_t212")

        by_class[group_key].append(ticker)

    # ── Write tickers list ────────────────────────────────────────────────────
    with open(TICKERS_FILE, "w") as f:
        json.dump(ticker_list, f, indent=2)

    print(f"\nWrote {len(ticker_list)} tickers → {TICKERS_FILE.name}")

    # ── Write detailed catalog ────────────────────────────────────────────────
    with open(CATALOG_FILE, "w") as f:
        json.dump(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "environment": T212_ENV,
                "total": len(detailed_catalog),
                "instruments": detailed_catalog,
            },
            f,
            indent=2,
        )

    print(f"Wrote detailed catalog → {CATALOG_FILE.name}")

    # ── Breakdown by class ────────────────────────────────────────────────────
    print("\nBreakdown by asset class:")
    for grp, tickers in by_class.items():
        if tickers:
            print(f"  {grp:<25} {len(tickers):>5} instruments")

    # ── Merge into universe_tickers.json ─────────────────────────────────────
    if merge_into_universe:
        _merge_into_universe(by_class)

    print("\nDone.")
    print("  t212_tickers.json  — flat ticker list, ready for the watcher loop")
    print("  t212_catalog.json  — full metadata (name, ISIN, exchange, type)")
    if merge_into_universe:
        print("  universe_tickers.json — updated with all T212 instruments")


def _merge_into_universe(by_class: dict[str, list[str]]) -> None:
    """Merge T212 tickers into the existing universe_tickers.json."""
    UNIVERSE_FILE.parent.mkdir(exist_ok=True)

    existing: dict[str, list[str]] = {}
    if UNIVERSE_FILE.exists():
        with open(UNIVERSE_FILE) as f:
            existing = json.load(f)

    added = 0
    for group, tickers in by_class.items():
        if not tickers:
            continue
        current = set(existing.get(group, []))
        new_ones = [t for t in tickers if t not in current]
        existing[group] = list(current | set(tickers))
        added += len(new_ones)

    with open(UNIVERSE_FILE, "w") as f:
        json.dump(existing, f, indent=2)

    total = sum(len(v) for v in existing.values())
    print(f"\nMerged into universe_tickers.json: +{added} new tickers ({total} total)")


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    merge = "--merge" in sys.argv
    fetch_all_trading212_stocks(merge_into_universe=merge)
