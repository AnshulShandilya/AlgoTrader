from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from datetime import datetime, timedelta
import pandas as pd
from typing import Optional


TIMEFRAME_MAP = {
    "1Min": TimeFrame(1, TimeFrameUnit.Minute),
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    "15Min": TimeFrame(15, TimeFrameUnit.Minute),
    "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
    "1Day": TimeFrame(1, TimeFrameUnit.Day),
}

# Known crypto symbols on Alpaca (symbol contains "/" or is in this set)
CRYPTO_BASES = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "LTC", "BCH", "SHIB", "UNI", "AAVE", "DOT", "MATIC", "XTZ"}


def is_crypto(symbol: str) -> bool:
    if "/" in symbol:
        return True
    base = symbol.replace("USD", "").replace("USDT", "")
    return base in CRYPTO_BASES


class AlpacaBroker:
    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self.paper = paper
        self.trading = TradingClient(api_key, secret_key, paper=paper)
        self.stock_data = StockHistoricalDataClient(api_key, secret_key)
        self.crypto_data = CryptoHistoricalDataClient(api_key, secret_key)

    def get_account(self) -> dict:
        acc = self.trading.get_account()
        return {
            "equity": float(acc.equity),
            "cash": float(acc.cash),
            "portfolio_value": float(acc.portfolio_value),
            "buying_power": float(acc.buying_power),
            "day_trade_count": int(acc.daytrade_count),
        }

    def get_positions(self) -> list[dict]:
        positions = self.trading.get_all_positions()
        result = []
        for p in positions:
            result.append({
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc) * 100,
                "side": p.side.value,
            })
        return result

    def get_bars(self, symbol: str, timeframe: str = "1Day", limit: int = 200) -> pd.DataFrame:
        tf = TIMEFRAME_MAP.get(timeframe, TimeFrame(1, TimeFrameUnit.Day))
        start = datetime.utcnow() - timedelta(days=limit * 2)

        if is_crypto(symbol):
            request = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start, limit=limit)
            bars = self.crypto_data.get_crypto_bars(request)
        else:
            request = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start, limit=limit)
            bars = self.stock_data.get_stock_bars(request)

        df = bars.df.reset_index()
        if "symbol" in df.columns:
            df = df[df["symbol"] == symbol].copy()
        df = df.rename(columns={"timestamp": "datetime"})
        df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
        df = df.sort_values("datetime").reset_index(drop=True)
        return df

    def place_market_order(self, symbol: str, qty: float, side: str,
                           stop_loss_price: float = None,
                           take_profit_price: float = None,
                           strategy_name: str = None) -> dict:
        """
        Place a market order.  BUY orders REQUIRE a stop_loss_price — the call
        raises ValueError if one is not supplied, enforcing the no-trade-without-SL rule.

        For equities, submits a bracket order when qty >= 1 whole share.
        For crypto or fractional shares, places the market order then immediately
        submits a separate stop order so the position is never left unprotected.
        """
        if side == "buy" and stop_loss_price is None:
            raise ValueError(
                f"stop_loss_price is required for BUY orders ({symbol}). "
                "No trade without a stop loss."
            )

        tif = TimeInForce.GTC if is_crypto(symbol) else TimeInForce.DAY
        use_bracket = (
            stop_loss_price is not None
            and take_profit_price is not None
            and not is_crypto(symbol)
            and side == "buy"
        )

        if use_bracket:
            whole_qty = int(qty)
            if whole_qty < 1:
                use_bracket = False  # fractional — will place stop separately below
            else:
                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=whole_qty,
                    side=OrderSide.BUY,
                    time_in_force=tif,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
                    stop_loss=StopLossRequest(stop_price=round(stop_loss_price, 2)),
                )

        if not use_bracket:
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=tif,
            )

        order = self.trading.submit_order(req)
        result = {
            "order_id": str(order.id),
            "symbol": order.symbol,
            "qty": float(order.qty),
            "side": order.side.value,
            "status": order.status.value,
            "order_class": order.order_class.value if order.order_class else "simple",
            "submitted_at": str(order.submitted_at),
            "strategy": strategy_name,
        }

        # For fractional/crypto BUY orders that can't use brackets, place a
        # separate stop order immediately so the position is protected.
        if side == "buy" and not use_bracket and stop_loss_price is not None:
            try:
                from alpaca.trading.requests import StopOrderRequest
                stop_req = StopOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=tif,
                    stop_price=round(stop_loss_price, 2),
                )
                stop_order = self.trading.submit_order(stop_req)
                result["stop_order_id"] = str(stop_order.id)
            except Exception as e:
                # Log but don't raise — the position is open, caller must handle
                import logging
                logging.getLogger(__name__).error(
                    f"AlpacaBroker: stop order failed for {symbol} after market fill: {e}. "
                    "Position is UNPROTECTED — manual stop required."
                )
                result["stop_order_error"] = str(e)

        return result

    def close_position(self, symbol: str) -> Optional[dict]:
        try:
            order = self.trading.close_position(symbol)
            return {"order_id": str(order.id), "symbol": symbol, "status": "submitted"}
        except Exception as e:
            return {"error": str(e)}

    def get_orders(self, limit: int = 50) -> list[dict]:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit)
        orders = self.trading.get_orders(filter=req)
        result = []
        for o in orders:
            result.append({
                "order_id": str(o.id),
                "symbol": o.symbol,
                "qty": float(o.qty) if o.qty else 0,
                "filled_qty": float(o.filled_qty) if o.filled_qty else 0,
                "side": o.side.value,
                "order_type": o.type.value,
                "status": o.status.value,
                "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "submitted_at": str(o.submitted_at),
                "filled_at": str(o.filled_at) if o.filled_at else None,
            })
        return result

    def get_live_price(self, symbol: str) -> float:
        """
        Fetch the latest trade price for a US equity (NYSE/NASDAQ) via Alpaca.
        Falls back to the most recent daily bar close if the snapshot is unavailable.
        """
        from alpaca.data.requests import StockLatestQuoteRequest, StockLatestBarRequest
        clean = symbol.upper().replace("/", "")

        # Try latest quote (bid/ask midpoint — most accurate during market hours)
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=clean)
            quote = self.stock_data.get_stock_latest_quote(req)
            q = quote.get(clean)
            if q:
                bid = float(q.bid_price or 0)
                ask = float(q.ask_price or 0)
                if bid > 0 and ask > 0:
                    return round((bid + ask) / 2, 6)
        except Exception:
            pass

        # Fallback: latest bar close
        try:
            req = StockLatestBarRequest(symbol_or_symbols=clean)
            bar = self.stock_data.get_stock_latest_bar(req)
            b = bar.get(clean)
            if b:
                return float(b.close)
        except Exception:
            pass

        raise RuntimeError(f"AlpacaBroker: could not fetch live price for {symbol}")

    def calculate_shares(self, equity: float, position_size_pct: float, current_price: float) -> float:
        dollar_amount = equity * (position_size_pct / 100)
        shares = dollar_amount / current_price
        return round(shares, 6)  # crypto needs more decimal places
