import pandas as pd
from datetime import datetime
from .base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        lookback = self.parameters.get("lookback_period", 20)
        threshold = self.parameters.get("momentum_threshold", 5.0)
        volume_filter = self.parameters.get("volume_filter", True)

        close_now = float(df["close"].iloc[-1])
        close_prev = float(df["close"].iloc[-lookback]) if len(df) > lookback else float(df["close"].iloc[0])
        momentum_pct = (close_now - close_prev) / close_prev * 100

        volume_confirmed = True
        avg_volume = float(df["volume"].rolling(lookback).mean().iloc[-1])
        current_volume = float(df["volume"].iloc[-1])
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

        if volume_filter:
            volume_confirmed = volume_ratio >= 1.2

        roc_5 = float((df["close"].pct_change(5) * 100).iloc[-1]) if len(df) > 5 else 0

        if momentum_pct >= threshold and volume_confirmed and roc_5 > 0:
            action = "buy"
            confidence = min(1.0, momentum_pct / (threshold * 2))
        elif momentum_pct <= -threshold and volume_confirmed and roc_5 < 0:
            action = "sell"
            confidence = min(1.0, abs(momentum_pct) / (threshold * 2))
        else:
            action = "hold"
            confidence = 0.0

        return Signal(
            symbol=self.symbol,
            action=action,
            confidence=round(confidence, 2),
            indicators={
                "momentum_pct": round(momentum_pct, 2),
                "threshold": threshold,
                "volume_ratio": round(volume_ratio, 2),
                "volume_confirmed": volume_confirmed,
                "roc_5d": round(roc_5, 2),
                "close": round(close_now, 2),
            },
            timestamp=datetime.utcnow(),
        )
