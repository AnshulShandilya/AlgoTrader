"""
Scalping strategy — 5Min EMA + RSI + Volume confluence + HTF trend filter.

Entry (two modes, controlled by rsi_mode parameter):
  momentum (default):
    BUY  — EMA9 > EMA21, RSI 48–82 and rising (3-bar slope), volume > threshold
    SELL — EMA9 < EMA21, RSI 18–52 and falling, volume > threshold

Higher-timeframe filter (Elder Triple Screen):
  When htf_df is provided (15Min or 1H bars), a BUY is only allowed when the
  HTF EMA9 > EMA21 (uptrend on the larger canvas). A SELL is only allowed when
  HTF EMA9 < EMA21. This prevents buying at the top of an exhausted daily run.
  htf_min_separation controls the minimum EMA spread % required on the HTF;
  defaults to 0.02% to reject near-flat crossovers.
"""
import pandas as pd
from datetime import datetime
from .base import BaseStrategy, Signal


class ScalpingStrategy(BaseStrategy):
    def generate_signal(self, df: pd.DataFrame, htf_df=None) -> Signal:  # htf_df: Optional[pd.DataFrame]
        rsi_period    = self.parameters.get("rsi_period", 14)
        fast_ema      = self.parameters.get("fast_ema", 9)
        slow_ema      = self.parameters.get("slow_ema", 21)
        vol_multiplier = self.parameters.get("vol_multiplier", 0.7)
        rsi_buy       = self.parameters.get("rsi_buy_level", 45)
        rsi_sell      = self.parameters.get("rsi_sell_level", 55)
        rsi_mode      = self.parameters.get("rsi_mode", "momentum")  # "momentum" | "crossover"

        close  = df["close"]
        volume = df["volume"]

        ema_fast = close.ewm(span=fast_ema, adjust=False).mean()
        ema_slow = close.ewm(span=slow_ema, adjust=False).mean()

        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(rsi_period).mean()
        loss  = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs    = gain / loss.replace(0, 1e-10)
        rsi   = 100 - (100 / (1 + rs))

        # Use the previous *completed* bar for volume check.
        # The current bar is still forming — its volume is always a fraction of a full bar.
        avg_vol   = volume.rolling(20).mean()
        vol_ratio = float(volume.iloc[-2]) / (float(avg_vol.iloc[-2]) or 1.0)

        ema_f_now  = float(ema_fast.iloc[-1])
        ema_s_now  = float(ema_slow.iloc[-1])
        rsi_now    = float(rsi.iloc[-1])
        rsi_prev   = float(rsi.iloc[-2]) if len(rsi) > 1 else rsi_now
        close_now  = float(close.iloc[-1])

        bullish_trend = ema_f_now > ema_s_now
        bearish_trend = ema_f_now < ema_s_now
        vol_ok        = vol_ratio >= vol_multiplier

        if rsi_mode == "crossover":
            # Original crossover logic — fires only on exact RSI level crossing
            buy_signal  = bullish_trend and rsi_prev < rsi_buy  and rsi_now >= rsi_buy  and vol_ok
            sell_signal = bearish_trend and rsi_prev > rsi_sell and rsi_now <= rsi_sell and vol_ok
        else:
            # Momentum mode — trend + RSI health zone + volume.
            # Uses 3-bar RSI slope so single-tick jitter doesn't kill valid entries.
            # BUY:  bullish EMA, RSI 48–82 (healthy momentum, not extreme overbought), vol ok
            # SELL: bearish EMA, RSI 18–52 (healthy downside momentum), vol ok
            rsi_3bar_ago = float(rsi.iloc[-4]) if len(rsi) >= 4 else rsi_prev
            rsi_trend_up   = rsi_now > rsi_3bar_ago   # rising over last 3 bars
            rsi_trend_down = rsi_now < rsi_3bar_ago
            buy_signal  = bullish_trend and (48 <= rsi_now <= 82) and rsi_trend_up  and vol_ok
            sell_signal = bearish_trend and (18 <= rsi_now <= 52) and rsi_trend_down and vol_ok

        ema_spread = abs(ema_f_now - ema_s_now) / (ema_s_now or 1.0) * 100

        if buy_signal:
            action     = "buy"
            confidence = min(1.0, (vol_ratio / 3) * 0.5 + min(ema_spread * 10, 0.5))
        elif sell_signal:
            action     = "sell"
            confidence = min(1.0, (vol_ratio / 3) * 0.5 + min(ema_spread * 10, 0.5))
        else:
            action     = "hold"
            confidence = 0.0

        # ── Higher-timeframe filter (Elder Triple Screen) ─────────────────────
        # Only trade in the direction the bigger picture supports.
        # A 5Min BUY at the top of an exhausted daily run is a losing trade.
        htf_trend = "unknown"
        htf_separation = 0.0
        htf_blocked = False
        htf_min_sep = self.parameters.get("htf_min_separation", 0.02)  # % minimum EMA spread

        if htf_df is not None and len(htf_df) >= 21 and action != "hold":
            htf_close = htf_df["close"].astype(float)
            htf_ema_fast = htf_close.ewm(span=fast_ema, adjust=False).mean()
            htf_ema_slow = htf_close.ewm(span=slow_ema, adjust=False).mean()
            htf_f = float(htf_ema_fast.iloc[-1])
            htf_s = float(htf_ema_slow.iloc[-1])
            htf_separation = (htf_f - htf_s) / (htf_s or 1.0) * 100  # signed %

            if htf_f > htf_s:
                htf_trend = "bullish"
            elif htf_f < htf_s:
                htf_trend = "bearish"
            else:
                htf_trend = "neutral"

            # Block if signal direction contradicts HTF trend
            if action == "buy" and (htf_trend != "bullish" or htf_separation < htf_min_sep):
                htf_blocked = True
            elif action == "sell" and (htf_trend != "bearish" or htf_separation > -htf_min_sep):
                htf_blocked = True

            if htf_blocked:
                action     = "hold"
                confidence = 0.0

        return Signal(
            symbol=self.symbol,
            action=action,
            confidence=round(confidence, 2),
            indicators={
                f"ema{fast_ema}":   round(ema_f_now, 4),
                f"ema{slow_ema}":   round(ema_s_now, 4),
                "rsi":              round(rsi_now, 1),
                "rsi_prev":         round(rsi_prev, 1),
                "vol_ratio":        round(vol_ratio, 2),
                "trend":            "bullish" if bullish_trend else "bearish",
                "rsi_mode":         rsi_mode,
                "close":            round(close_now, 4),
                "htf_trend":        htf_trend,
                "htf_ema_sep_pct":  round(htf_separation, 4),
                "htf_blocked":      htf_blocked,
            },
            timestamp=datetime.utcnow(),
        )
