"""
Binance Testnet broker.
Prices match TradingView (BINANCE feed) exactly.
Testnet URL: https://testnet.binance.vision
Get keys at: https://testnet.binance.vision → Log In with GitHub → Generate HMAC Keys
"""
import pandas as pd
from datetime import datetime
from typing import Optional
from binance import Client
from binance.exceptions import BinanceAPIException

TIMEFRAME_MAP = {
    "1Min":  Client.KLINE_INTERVAL_1MINUTE,
    "5Min":  Client.KLINE_INTERVAL_5MINUTE,
    "15Min": Client.KLINE_INTERVAL_15MINUTE,
    "1Hour": Client.KLINE_INTERVAL_1HOUR,
    "1Day":  Client.KLINE_INTERVAL_1DAY,
}

# Price precision per asset (Binance requires exact step sizes)
LOT_SIZE_OVERRIDE = {
    "BTCUSDT": 5,  "ETHUSDT": 4, "SOLUSDT": 2,
    "BNBUSDT": 3,  "XRPUSDT": 1, "DOGEUSDT": 0,
    "LTCUSDT": 3,  "LINKUSDT": 2, "ADAUSDT": 0,
    "AVAXUSDT": 2, "DOTUSDT": 2, "UNIUSDT": 2,
    "AAVEUSDT": 3, "ATOMUSDT": 2, "MATICUSDT": 0,
}


def to_binance_symbol(symbol: str) -> str:
    """BTC/USD or BTC/USDT → BTCUSDT"""
    s = symbol.replace("/", "").upper()
    if s.endswith("USD") and not s.endswith("USDT"):
        s = s + "T"
    return s


def to_display_symbol(binance_symbol: str) -> str:
    """BTCUSDT → BTC/USDT"""
    if binance_symbol.endswith("USDT"):
        return binance_symbol[:-4] + "/USDT"
    return binance_symbol


class BinanceBroker:
    def __init__(self, api_key: str, secret_key: str, testnet: bool = True):
        import time as _time
        self.testnet = testnet
        self.client = Client(api_key, secret_key, testnet=testnet)
        # Sync clock offset — Binance rejects requests where local time drifts
        # more than ~1 000 ms from server time (error -1022 "invalid signature")
        try:
            server_ms = self.client.get_server_time()["serverTime"]
            self.client.timestamp_offset = server_ms - int(_time.time() * 1000)
        except Exception:
            self.client.timestamp_offset = 0

        # Separate real-market client for bar data — testnet prices diverge from
        # live market and would cause strategies to generate wrong signals.
        # Public klines endpoints need no authentication.
        if testnet:
            self._data_client = Client("", "", testnet=False)
        else:
            self._data_client = self.client

    def _account_snapshot(self):
        """
        Single-pass account + positions using one batch ticker call.
        Avoids the N-serial-HTTP-calls pattern that kills latency on accounts
        with many token balances (Binance testnet ships with 400+ pre-seeded).
        """
        info = self.client.get_account()
        balances = info["balances"]

        stablecoins = {"USDT", "BUSD", "USDC", "TUSD", "DAI"}
        usdt = next((float(b["free"]) + float(b["locked"])
                     for b in balances if b["asset"] == "USDT"), 0.0)

        # One batch call for all prices
        all_tickers = {t["symbol"]: float(t["price"])
                       for t in self.client.get_all_tickers()}

        equity = usdt
        positions = []
        for b in balances:
            asset = b["asset"]
            if asset in stablecoins:
                continue
            qty = float(b["free"]) + float(b["locked"])
            if qty < 1e-8:
                continue
            price = all_tickers.get(asset + "USDT", 0.0)
            if price == 0.0:
                continue
            market_value = qty * price
            if market_value < 1.0:
                continue
            equity += market_value
            positions.append({
                "symbol": asset + "/USDT",
                "qty": qty,
                "avg_entry_price": price,
                "current_price": price,
                "market_value": round(market_value, 2),
                "unrealized_pl": 0.0,
                "unrealized_plpc": 0.0,
                "side": "long",
            })

        account = {
            "equity": round(equity, 2),
            "cash": round(usdt, 2),
            "portfolio_value": round(equity, 2),
            "buying_power": round(usdt, 2),
            "day_trade_count": 0,
        }
        return account, positions

    def get_account(self) -> dict:
        account, _ = self._account_snapshot()
        return account

    def get_positions(self) -> list:
        _, positions = self._account_snapshot()

    def get_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 200) -> pd.DataFrame:
        bsym = to_binance_symbol(symbol)
        interval = TIMEFRAME_MAP.get(timeframe, Client.KLINE_INTERVAL_5MINUTE)
        # Always fetch bars from real Binance — testnet prices are simulated
        klines = self._data_client.get_klines(symbol=bsym, interval=interval, limit=limit)

        rows = []
        for k in klines:
            rows.append({
                "datetime": datetime.utcfromtimestamp(k[0] / 1000),
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            })

        df = pd.DataFrame(rows)
        df = df.sort_values("datetime").reset_index(drop=True)
        return df

    def place_market_order(self, symbol: str, qty: float, side: str,
                           stop_loss_price: float = None,
                           take_profit_price: float = None,
                           strategy_name: str = None) -> dict:
        # BUY orders require a stop loss — hard rule, no exceptions
        if side == "buy" and stop_loss_price is None:
            raise ValueError(
                f"stop_loss_price is required for BUY orders ({symbol}). "
                "No trade without a stop loss."
            )

        bsym = to_binance_symbol(symbol)
        precision = LOT_SIZE_OVERRIDE.get(bsym, 4)

        try:
            if side == "buy":
                ticker = self.client.get_symbol_ticker(symbol=bsym)
                price = float(ticker["price"])
                usdt_amount = round(qty * price, 2)
                order = self.client.order_market_buy(
                    symbol=bsym,
                    quoteOrderQty=usdt_amount,
                )
            else:
                coin_qty = round(qty, precision)
                order = self.client.order_market_sell(
                    symbol=bsym,
                    quantity=coin_qty,
                )

            fills = order.get("fills", [])
            avg_price = (
                sum(float(f["price"]) * float(f["qty"]) for f in fills) /
                sum(float(f["qty"]) for f in fills)
                if fills else 0.0
            )
            filled_qty = float(order.get("executedQty", qty))

            result = {
                "order_id": str(order["orderId"]),
                "symbol": to_display_symbol(order["symbol"]),
                "qty": filled_qty,
                "side": side,
                "status": order["status"].lower(),
                "submitted_at": str(datetime.utcnow()),
                "avg_price": round(avg_price, 4),
                "strategy": strategy_name,
            }

            # Place OCO (stop + take-profit) immediately after a BUY fills
            if side == "buy" and stop_loss_price is not None:
                try:
                    coin_qty = round(filled_qty, precision)
                    if take_profit_price is not None:
                        oco = self.client.order_oco_sell(
                            symbol=bsym,
                            quantity=coin_qty,
                            price=round(take_profit_price, 8),
                            stopPrice=round(stop_loss_price, 8),
                            stopLimitPrice=round(stop_loss_price * 0.995, 8),
                            stopLimitTimeInForce="GTC",
                        )
                        result["oco_order_id"] = str(oco.get("orderListId", ""))
                    else:
                        # No TP — place a plain stop-market order
                        stop_ord = self.client.create_order(
                            symbol=bsym,
                            side="SELL",
                            type="STOP_LOSS",
                            quantity=coin_qty,
                            stopPrice=round(stop_loss_price, 8),
                        )
                        result["stop_order_id"] = str(stop_ord["orderId"])
                except BinanceAPIException as oco_err:
                    import logging
                    logging.getLogger(__name__).error(
                        f"BinanceBroker: OCO/stop order failed for {symbol} after fill: {oco_err.message}. "
                        "Position is UNPROTECTED — manual stop required."
                    )
                    result["stop_order_error"] = oco_err.message

            return result

        except BinanceAPIException as e:
            raise RuntimeError(f"Binance order error: {e.message}") from e

    def close_position(self, symbol: str) -> Optional[dict]:
        bsym = to_binance_symbol(symbol)
        asset = bsym.replace("USDT", "")
        try:
            info = self.client.get_account()
            balance = next(
                (float(b["free"]) for b in info["balances"] if b["asset"] == asset), 0.0
            )
            if balance < 1e-8:
                return {"error": "No balance to sell"}

            precision = LOT_SIZE_OVERRIDE.get(bsym, 4)
            order = self.client.order_market_sell(
                symbol=bsym,
                quantity=round(balance, precision),
            )
            return {"order_id": str(order["orderId"]), "symbol": symbol, "status": "submitted"}
        except BinanceAPIException as e:
            return {"error": e.message}

    def get_orders(self, limit: int = 50) -> list:
        orders = []
        # Pull recent trades across common pairs
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "XRPUSDT",
                   "ADAUSDT", "DOGEUSDT", "LTCUSDT", "AVAXUSDT", "BNBUSDT"]
        for sym in symbols:
            try:
                trades = self.client.get_my_trades(symbol=sym, limit=10)
                for t in trades:
                    orders.append({
                        "order_id": str(t["orderId"]),
                        "symbol": to_display_symbol(sym),
                        "qty": float(t["qty"]),
                        "filled_qty": float(t["qty"]),
                        "side": "buy" if t["isBuyer"] else "sell",
                        "order_type": "market",
                        "status": "filled",
                        "filled_avg_price": float(t["price"]),
                        "submitted_at": str(datetime.utcfromtimestamp(t["time"] / 1000)),
                        "filled_at": str(datetime.utcfromtimestamp(t["time"] / 1000)),
                    })
            except Exception:
                pass
        orders.sort(key=lambda x: x["submitted_at"], reverse=True)
        return orders[:limit]

    def calculate_shares(self, equity: float, position_size_pct: float, current_price: float) -> float:
        dollar_amount = equity * (position_size_pct / 100)
        coins = dollar_amount / current_price
        return round(coins, 6)

    def get_live_price(self, symbol: str) -> float:
        bsym = to_binance_symbol(symbol)
        # Use real-market data client for accurate prices
        ticker = self._data_client.get_symbol_ticker(symbol=bsym)
        return float(ticker["price"])
