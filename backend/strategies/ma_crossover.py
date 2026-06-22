import pandas as pd
from datetime import datetime
from .base import BaseStrategy, Signal


class MACrossoverStrategy(BaseStrategy):
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        fast_p = self.parameters.get("fast_ma", 20)
        slow_p = self.parameters.get("slow_ma", 50)
        ma_type = self.parameters.get("ma_type", "EMA")

        if ma_type == "EMA":
            fast_ma = df["close"].ewm(span=fast_p, adjust=False).mean()
            slow_ma = df["close"].ewm(span=slow_p, adjust=False).mean()
        else:
            fast_ma = df["close"].rolling(fast_p).mean()
            slow_ma = df["close"].rolling(slow_p).mean()

        fast_now = float(fast_ma.iloc[-1])
        slow_now = float(slow_ma.iloc[-1])
        fast_prev = float(fast_ma.iloc[-2])
        slow_prev = float(slow_ma.iloc[-2])

        spread_pct = abs(fast_now - slow_now) / slow_now * 100

        if fast_prev <= slow_prev and fast_now > slow_now:
            action = "buy"
            confidence = min(1.0, spread_pct / 2)
        elif fast_prev >= slow_prev and fast_now < slow_now:
            action = "sell"
            confidence = min(1.0, spread_pct / 2)
        else:
            action = "hold"
            confidence = 0.0

        return Signal(
            symbol=self.symbol,
            action=action,
            confidence=round(confidence, 2),
            indicators={
                f"fast_{ma_type}_{fast_p}": round(fast_now, 2),
                f"slow_{ma_type}_{slow_p}": round(slow_now, 2),
                "spread_pct": round(spread_pct, 3),
                "close": round(float(df["close"].iloc[-1]), 2),
                "trend": "bullish" if fast_now > slow_now else "bearish",
            },
            timestamp=datetime.utcnow(),
        )
