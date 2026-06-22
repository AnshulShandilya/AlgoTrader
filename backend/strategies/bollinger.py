import pandas as pd
from datetime import datetime
from .base import BaseStrategy, Signal


class BollingerStrategy(BaseStrategy):
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        period = self.parameters.get("period", 20)
        std_dev = self.parameters.get("std_dev", 2.0)

        sma = df["close"].rolling(period).mean()
        std = df["close"].rolling(period).std()
        upper = sma + std_dev * std
        lower = sma - std_dev * std

        close_now = float(df["close"].iloc[-1])
        close_prev = float(df["close"].iloc[-2])
        upper_now = float(upper.iloc[-1])
        lower_now = float(lower.iloc[-1])
        sma_now = float(sma.iloc[-1])
        upper_prev = float(upper.iloc[-2])
        lower_prev = float(lower.iloc[-2])

        band_width = (upper_now - lower_now) / sma_now * 100
        pct_b = (close_now - lower_now) / (upper_now - lower_now) if upper_now != lower_now else 0.5

        if close_prev <= lower_prev and close_now > lower_now:
            action = "buy"
            confidence = min(1.0, (1 - pct_b) + 0.3)
        elif close_prev >= upper_prev and close_now < upper_now:
            action = "sell"
            confidence = min(1.0, pct_b + 0.3)
        else:
            action = "hold"
            confidence = 0.0

        return Signal(
            symbol=self.symbol,
            action=action,
            confidence=round(confidence, 2),
            indicators={
                "upper_band": round(upper_now, 2),
                "middle_band": round(sma_now, 2),
                "lower_band": round(lower_now, 2),
                "band_width_pct": round(band_width, 2),
                "pct_b": round(pct_b, 3),
                "close": round(close_now, 2),
            },
            timestamp=datetime.utcnow(),
        )
