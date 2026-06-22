import pandas as pd
from datetime import datetime
from .base import BaseStrategy, Signal


class RSIStrategy(BaseStrategy):
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        period = self.parameters.get("rsi_period", 14)
        oversold = self.parameters.get("oversold_level", 30)
        overbought = self.parameters.get("overbought_level", 70)

        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        current_rsi = float(rsi.iloc[-1])
        prev_rsi = float(rsi.iloc[-2]) if len(rsi) > 1 else current_rsi
        current_close = float(df["close"].iloc[-1])

        if prev_rsi <= oversold and current_rsi > oversold:
            action = "buy"
            confidence = min(1.0, (oversold - min(prev_rsi, current_rsi)) / oversold + 0.5)
        elif prev_rsi >= overbought and current_rsi < overbought:
            action = "sell"
            confidence = min(1.0, (max(prev_rsi, current_rsi) - overbought) / (100 - overbought) + 0.5)
        else:
            action = "hold"
            confidence = 0.0

        return Signal(
            symbol=self.symbol,
            action=action,
            confidence=round(confidence, 2),
            indicators={
                "rsi": round(current_rsi, 2),
                "rsi_prev": round(prev_rsi, 2),
                "oversold": oversold,
                "overbought": overbought,
                "close": current_close,
            },
            timestamp=datetime.utcnow(),
        )
