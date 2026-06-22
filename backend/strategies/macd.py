import pandas as pd
from datetime import datetime
from .base import BaseStrategy, Signal


class MACDStrategy(BaseStrategy):
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        fast = self.parameters.get("fast_period", 12)
        slow = self.parameters.get("slow_period", 26)
        signal_p = self.parameters.get("signal_period", 9)

        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal_p, adjust=False).mean()
        histogram = macd_line - signal_line

        macd_now = float(macd_line.iloc[-1])
        signal_now = float(signal_line.iloc[-1])
        macd_prev = float(macd_line.iloc[-2])
        signal_prev = float(signal_line.iloc[-2])
        hist_now = float(histogram.iloc[-1])

        if macd_prev < signal_prev and macd_now > signal_now:
            action = "buy"
            confidence = min(1.0, abs(hist_now) / (abs(macd_now) + 1e-10) * 2)
        elif macd_prev > signal_prev and macd_now < signal_now:
            action = "sell"
            confidence = min(1.0, abs(hist_now) / (abs(macd_now) + 1e-10) * 2)
        else:
            action = "hold"
            confidence = 0.0

        return Signal(
            symbol=self.symbol,
            action=action,
            confidence=round(confidence, 2),
            indicators={
                "macd": round(macd_now, 4),
                "signal": round(signal_now, 4),
                "histogram": round(hist_now, 4),
                "close": round(float(df["close"].iloc[-1]), 2),
            },
            timestamp=datetime.utcnow(),
        )
