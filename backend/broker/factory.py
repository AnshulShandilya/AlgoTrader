"""
Broker factory — returns the right broker based on configured API keys.
Priority: Binance Testnet > Alpaca Paper.

create_broker() only instantiates — it does NOT probe the network.
Fallback (Binance → Alpaca) is handled at the call site so it can run
async-safely in a thread pool.
"""
from typing import Union
from .alpaca import AlpacaBroker
from .binance import BinanceBroker


def create_broker(settings) -> Union[BinanceBroker, AlpacaBroker]:
    if settings and getattr(settings, "binance_api_key", None):
        return BinanceBroker(
            api_key=settings.binance_api_key,
            secret_key=settings.binance_secret_key,
            testnet=settings.binance_testnet,
        )
    if settings and getattr(settings, "alpaca_api_key", None):
        return AlpacaBroker(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.paper_trading,
        )
    raise ValueError("No broker API keys configured — add keys in Settings")


def create_alpaca_broker(settings) -> AlpacaBroker:
    """Directly instantiate Alpaca (used as fallback when Binance fails)."""
    if settings and getattr(settings, "alpaca_api_key", None):
        return AlpacaBroker(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.paper_trading,
        )
    raise ValueError("No Alpaca API keys configured")


def get_broker_name(settings) -> str:
    """Fast — does NOT probe connectivity. Used only for UI badges."""
    if settings and getattr(settings, "binance_api_key", None):
        return "binance_testnet" if settings.binance_testnet else "binance_live"
    if settings and getattr(settings, "alpaca_api_key", None):
        return "alpaca_paper" if settings.paper_trading else "alpaca_live"
    return "none"
