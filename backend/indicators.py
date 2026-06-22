"""
Professional Indicator Engine — 20 indicators used by top 0.1% traders.

All functions accept a pandas DataFrame with columns:
  datetime, open, high, low, close, volume
and return a dict of indicator values + a composite score (0-100).

Indicator roster:
  Trend       : EMA Stack (9/21/50/200), Ichimoku Cloud, Supertrend,
                Parabolic SAR, ADX
  Momentum    : RSI(14), MACD, Stochastic %K/%D, Williams %R, CCI, ROC
  Volume/Flow : OBV trend, MFI, Volume Ratio (vs 20-bar avg)
  Volatility  : ATR%, Bollinger Band Width, Donchian Channel position
  Structure   : Hurst Exponent, Fibonacci proximity
"""
import math
import numpy as np
import pandas as pd
from typing import Dict, Any


# ─── helpers ──────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-10)
    return 100 - 100 / (1 + rs)

def _scalar(s) -> float:
    """Safely extract last scalar from a Series or plain value."""
    if isinstance(s, pd.Series):
        return float(s.iloc[-1])
    return float(s)


# ─── 20 Indicators ────────────────────────────────────────────────────────────

def compute_all(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Compute all 20 indicators on the supplied OHLCV DataFrame.
    Returns a flat dict with every indicator value AND a composite score.
    Requires at least 60 bars (daily recommended).
    """
    if df is None or len(df) < 50:
        return {"score": 0.0, "error": "insufficient_bars"}

    close  = df["close"].astype(float)
    high   = df["high"].astype(float)
    low    = df["low"].astype(float)
    volume = df["volume"].astype(float)
    n      = len(close)

    out: Dict[str, Any] = {}

    # ── 1. RSI (14) ─────────────────────────────────────────────────────────
    rsi_series = _rsi(close, 14)
    rsi = _scalar(rsi_series)
    out["rsi"] = round(rsi, 1)

    # ── 2. MACD (12/26/9) ───────────────────────────────────────────────────
    macd_line   = _ema(close, 12) - _ema(close, 26)
    signal_line = _ema(macd_line, 9)
    macd_hist   = macd_line - signal_line
    out["macd"]        = round(_scalar(macd_line), 4)
    out["macd_signal"] = round(_scalar(signal_line), 4)
    out["macd_hist"]   = round(_scalar(macd_hist), 4)
    macd_bullish = _scalar(macd_hist) > 0

    # ── 3. Bollinger Bands (20, 2σ) ─────────────────────────────────────────
    bb_mid   = close.rolling(20).mean()
    bb_std   = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_width = (_scalar(bb_upper) - _scalar(bb_lower)) / _scalar(bb_mid) * 100  # %
    bb_pct   = (_scalar(close) - _scalar(bb_lower)) / \
               max(_scalar(bb_upper) - _scalar(bb_lower), 1e-10)   # 0=at lower, 1=at upper
    out["bb_upper"] = round(_scalar(bb_upper), 4)
    out["bb_mid"]   = round(_scalar(bb_mid), 4)
    out["bb_lower"] = round(_scalar(bb_lower), 4)
    out["bb_width"] = round(bb_width, 2)    # wider = more volatile
    out["bb_pct"]   = round(bb_pct, 3)     # position within band

    # ── 4. ATR% (14) ────────────────────────────────────────────────────────
    atr     = _atr(high, low, close, 14)
    atr_pct = _scalar(atr) / _scalar(close) * 100
    out["atr"]     = round(_scalar(atr), 4)
    out["atr_pct"] = round(atr_pct, 3)

    # ── 5. EMA Stack (9 / 21 / 50 / 200) ───────────────────────────────────
    ema9   = _ema(close, 9)
    ema21  = _ema(close, 21)
    ema50  = _ema(close, 50)
    ema200 = _ema(close, 200) if n >= 200 else _ema(close, min(n-1, 150))
    e9, e21, e50, e200 = (_scalar(ema9), _scalar(ema21),
                          _scalar(ema50), _scalar(ema200))
    price = _scalar(close)
    # Perfect bullish stack: price > e9 > e21 > e50 > e200
    ema_stack_score = sum([
        price > e9,   # 1
        e9    > e21,  # 2
        e21   > e50,  # 3
        e50   > e200, # 4
    ])  # 0-4; 4 = full bull stack
    out["ema9"]   = round(e9, 4)
    out["ema21"]  = round(e21, 4)
    out["ema50"]  = round(e50, 4)
    out["ema200"] = round(e200, 4)
    out["ema_stack"] = ema_stack_score          # 0-4
    out["ema_trend"] = (
        "strong_bull" if ema_stack_score == 4 else
        "bull"        if ema_stack_score == 3 else
        "neutral"     if ema_stack_score == 2 else
        "bear"        if ema_stack_score == 1 else
        "strong_bear"
    )

    # ── 6. ADX (14) — trend strength ────────────────────────────────────────
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_raw    = pd.concat([high - low, (high - close.shift()).abs(),
                           (low  - close.shift()).abs()], axis=1).max(axis=1)
    atr14     = tr_raw.rolling(14).mean()
    plus_di   = 100 * (pd.Series(plus_dm, index=close.index).rolling(14).mean()  / atr14.replace(0, 1e-10))
    minus_di  = 100 * (pd.Series(minus_dm, index=close.index).rolling(14).mean() / atr14.replace(0, 1e-10))
    dx        = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10))
    adx       = dx.rolling(14).mean()
    adx_val   = _scalar(adx)
    pdi_val   = _scalar(plus_di)
    mdi_val   = _scalar(minus_di)
    out["adx"]      = round(adx_val, 1)    # >25 = trending; >40 = strong
    out["plus_di"]  = round(pdi_val, 1)
    out["minus_di"] = round(mdi_val, 1)

    # ── 7. Stochastic %K / %D (14,3) ────────────────────────────────────────
    low14  = low.rolling(14).min()
    high14 = high.rolling(14).max()
    stoch_k = 100 * (close - low14) / (high14 - low14).replace(0, 1e-10)
    stoch_d = stoch_k.rolling(3).mean()
    sk, sd  = _scalar(stoch_k), _scalar(stoch_d)
    out["stoch_k"] = round(sk, 1)
    out["stoch_d"] = round(sd, 1)

    # ── 8. Williams %R (14) ─────────────────────────────────────────────────
    willr = -100 * (high14 - close) / (high14 - low14).replace(0, 1e-10)
    out["williams_r"] = round(_scalar(willr), 1)    # -100 to 0; < -80 oversold, > -20 overbought

    # ── 9. CCI (20) — Commodity Channel Index ───────────────────────────────
    tp      = (high + low + close) / 3
    cci_ma  = tp.rolling(20).mean()
    cci_md  = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    cci     = (tp - cci_ma) / (0.015 * cci_md.replace(0, 1e-10))
    out["cci"] = round(_scalar(cci), 1)   # >100 overbought, <-100 oversold

    # ── 10. ROC — Rate of Change (10-bar) ───────────────────────────────────
    roc = close.pct_change(10) * 100
    out["roc"] = round(_scalar(roc), 2)

    # ── 11. OBV — On Balance Volume (trend) ─────────────────────────────────
    obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    # OBV trend: compare current OBV to 10-bar EMA of OBV
    obv_ema   = _ema(obv, 10)
    obv_bull  = _scalar(obv) > _scalar(obv_ema)
    out["obv"]      = round(_scalar(obv), 0)
    out["obv_trend"] = "rising" if obv_bull else "falling"

    # ── 12. MFI — Money Flow Index (14) ─────────────────────────────────────
    typical    = (high + low + close) / 3
    raw_flow   = typical * volume
    pos_flow   = raw_flow.where(typical > typical.shift(), 0.0).rolling(14).sum()
    neg_flow   = raw_flow.where(typical < typical.shift(), 0.0).rolling(14).sum()
    mfi_ratio  = pos_flow / neg_flow.replace(0, 1e-10)
    mfi        = 100 - 100 / (1 + mfi_ratio)
    out["mfi"] = round(_scalar(mfi), 1)   # >80 overbought, <20 oversold

    # ── 13. Volume Ratio (vs 20-bar avg) ────────────────────────────────────
    vol_avg   = volume.rolling(20).mean()
    vol_ratio = _scalar(volume) / max(_scalar(vol_avg), 1e-10)
    out["volume_ratio"] = round(vol_ratio, 2)

    # ── 14. Ichimoku Cloud (9/26/52) ────────────────────────────────────────
    tenkan  = (high.rolling(9).max()  + low.rolling(9).min())  / 2
    kijun   = (high.rolling(26).max() + low.rolling(26).min()) / 2
    span_a  = ((tenkan + kijun) / 2)
    span_b  = ((high.rolling(52).max() + low.rolling(52).min()) / 2) if n >= 52 else kijun
    tk_val, kj_val = _scalar(tenkan), _scalar(kijun)
    sa_val, sb_val = _scalar(span_a),  _scalar(span_b)
    price_above_cloud = price > max(sa_val, sb_val)
    price_below_cloud = price < min(sa_val, sb_val)
    cloud_color = "green" if sa_val > sb_val else "red"
    out["ichimoku_tenkan"]       = round(tk_val, 4)
    out["ichimoku_kijun"]        = round(kj_val, 4)
    out["ichimoku_span_a"]       = round(sa_val, 4)
    out["ichimoku_span_b"]       = round(sb_val, 4)
    out["ichimoku_above_cloud"]  = price_above_cloud
    out["ichimoku_cloud_color"]  = cloud_color

    # ── 15. Supertrend (ATR ×3, period=10) ──────────────────────────────────
    st_period, st_mult = 10, 3.0
    atr10   = _atr(high, low, close, st_period)
    mid     = (high + low) / 2
    upper_b = mid + st_mult * atr10
    lower_b = mid - st_mult * atr10
    supertrend = pd.Series(np.nan, index=close.index)
    direction  = pd.Series(1, index=close.index)
    for i in range(1, n):
        prev_upper = float(upper_b.iloc[i-1]) if not np.isnan(upper_b.iloc[i-1]) else float(upper_b.iloc[i])
        prev_lower = float(lower_b.iloc[i-1]) if not np.isnan(lower_b.iloc[i-1]) else float(lower_b.iloc[i])
        cur_upper  = float(upper_b.iloc[i])
        cur_lower  = float(lower_b.iloc[i])
        cur_close  = float(close.iloc[i])
        upper_b.iloc[i] = min(cur_upper, prev_upper) if cur_close <= prev_upper else cur_upper
        lower_b.iloc[i] = max(cur_lower, prev_lower) if cur_close >= prev_lower else cur_lower
        prev_dir = int(direction.iloc[i-1])
        if prev_dir == 1 and cur_close < float(lower_b.iloc[i]):
            direction.iloc[i] = -1
        elif prev_dir == -1 and cur_close > float(upper_b.iloc[i]):
            direction.iloc[i] = 1
        else:
            direction.iloc[i] = prev_dir
        supertrend.iloc[i] = float(lower_b.iloc[i]) if direction.iloc[i] == 1 else float(upper_b.iloc[i])
    st_dir = int(direction.iloc[-1])
    out["supertrend"]           = round(_scalar(supertrend), 4)
    out["supertrend_direction"] = "bullish" if st_dir == 1 else "bearish"

    # ── 16. Parabolic SAR ───────────────────────────────────────────────────
    af, max_af, step = 0.02, 0.2, 0.02
    sar_vals = [float(low.iloc[0])]
    ep        = float(high.iloc[0])
    bull      = True
    for i in range(1, n):
        prev_sar = sar_vals[-1]
        hi, lo   = float(high.iloc[i]), float(low.iloc[i])
        if bull:
            sar = prev_sar + af * (ep - prev_sar)
            sar = min(sar, float(low.iloc[i-1]), lo if i >= 2 else lo)
            if lo < sar:
                bull, sar, ep, af = False, ep, lo, 0.02
            else:
                if hi > ep:
                    ep, af = hi, min(af + step, max_af)
        else:
            sar = prev_sar + af * (ep - prev_sar)
            sar = max(sar, float(high.iloc[i-1]), hi if i >= 2 else hi)
            if hi > sar:
                bull, sar, ep, af = True, ep, hi, 0.02
            else:
                if lo < ep:
                    ep, af = lo, min(af + step, max_af)
        sar_vals.append(sar)
    out["sar"]           = round(sar_vals[-1], 4)
    out["sar_direction"] = "bullish" if bull else "bearish"

    # ── 17. Donchian Channel (20) — breakout detection ───────────────────────
    don_high = high.rolling(20).max()
    don_low  = low.rolling(20).min()
    don_pct  = (price - _scalar(don_low)) / max(_scalar(don_high) - _scalar(don_low), 1e-10)
    out["donchian_high"] = round(_scalar(don_high), 4)
    out["donchian_low"]  = round(_scalar(don_low), 4)
    out["donchian_pct"]  = round(don_pct, 3)   # 0=at low, 1=at high

    # ── 18. Fibonacci proximity ──────────────────────────────────────────────
    # Use 52-week (or available) high/low for key fib levels
    fib_period = min(252, n)
    fib_high   = float(high.iloc[-fib_period:].max())
    fib_low    = float(low.iloc[-fib_period:].min())
    fib_range  = fib_high - fib_low
    fib_levels = {
        "0.236": fib_high - 0.236 * fib_range,
        "0.382": fib_high - 0.382 * fib_range,
        "0.500": fib_high - 0.500 * fib_range,
        "0.618": fib_high - 0.618 * fib_range,
        "0.786": fib_high - 0.786 * fib_range,
    }
    # How close is price to its nearest Fibonacci level? (0=exact, 1=far)
    fib_dists = {k: abs(price - v) / max(fib_range, 1e-10) for k, v in fib_levels.items()}
    nearest_fib, fib_dist = min(fib_dists.items(), key=lambda x: x[1])
    out["fib_nearest_level"] = nearest_fib
    out["fib_distance_pct"]  = round(fib_dist * 100, 2)  # % of range
    out["fib_236"] = round(fib_levels["0.236"], 4)
    out["fib_382"] = round(fib_levels["0.382"], 4)
    out["fib_618"] = round(fib_levels["0.618"], 4)

    # ── 19. Hurst Exponent ──────────────────────────────────────────────────
    # Measures market memory: H>0.55 trending, H<0.45 mean-reverting
    def _hurst(prices: np.ndarray) -> float:
        if len(prices) < 20:
            return 0.5
        lags, stds = [], []
        for lag in range(2, min(21, len(prices) // 2)):
            diff = prices[lag:] - prices[:-lag]
            if len(diff) < 2:
                continue
            std = float(np.std(diff))
            if std > 0:
                lags.append(math.log(lag))
                stds.append(math.log(std))
        if len(lags) < 3:
            return 0.5
        slope, _ = np.polyfit(lags, stds, 1)
        return float(np.clip(slope, 0.0, 1.0))

    hurst_val = _hurst(close.values[-60:] if n >= 60 else close.values)
    out["hurst"] = round(hurst_val, 3)
    out["market_regime"] = (
        "trending"      if hurst_val > 0.55 else
        "mean_reverting" if hurst_val < 0.45 else
        "random"
    )

    # ── 20. VWAP (rolling 20-bar) ───────────────────────────────────────────
    tp_vwap  = (high + low + close) / 3
    vwap_raw = (tp_vwap * volume).rolling(20).sum() / volume.rolling(20).sum().replace(0, 1e-10)
    vwap_val = _scalar(vwap_raw)
    out["vwap"]          = round(vwap_val, 4)
    out["price_vs_vwap"] = round((price - vwap_val) / max(vwap_val, 1e-10) * 100, 2)  # % above/below

    # ── Composite Scoring (0-100) ────────────────────────────────────────────
    #
    # Six pillars — each scored 0-100 then weighted:
    #   Trend strength   (28%)
    #   Momentum quality (22%)
    #   Volume/flow      (18%)
    #   Volatility setup (14%)
    #   Breakout signal  (10%)
    #   Market structure  (8%)
    #
    # Each pillar: higher = better setup for entering a trade NOW.

    # ── Pillar 1: Trend Strength (28%) ──────────────────────────────────────
    # EMA stack 0-4 → 0-100
    ema_score = ema_stack_score / 4 * 100
    # ADX: 0=no trend, 25=trend, 40=strong, 60=very strong
    adx_score = min(100, max(0, (adx_val - 15) * 3.5))
    # Ichimoku: above green cloud=100, in cloud=50, below red=0
    ichi_score = 100 if (price_above_cloud and cloud_color == "green") else \
                 75  if (price_above_cloud and cloud_color == "red")  else \
                 25  if (price_below_cloud and cloud_color == "green") else \
                 0
    # Supertrend
    st_score = 100 if st_dir == 1 else 0
    # SAR
    sar_score = 100 if bull else 0
    trend_score = (ema_score * 0.35 + adx_score * 0.30 +
                   ichi_score * 0.15 + st_score * 0.12 + sar_score * 0.08)

    # ── Pillar 2: Momentum Quality (22%) ────────────────────────────────────
    # RSI: sweet spot 40-65 (room to run without reversal risk)
    if 40 <= rsi <= 65:
        rsi_s = 100
    elif 30 <= rsi < 40 or 65 < rsi <= 75:
        rsi_s = 70
    elif rsi < 25 or rsi > 80:
        rsi_s = 20
    else:
        rsi_s = 50
    # MACD histogram positive and growing
    macd_s = 100 if macd_bullish else 30
    # Stochastic: ideal 20-80 range, not extreme
    if 20 <= sk <= 80:
        stoch_s = 85
    elif sk < 10 or sk > 90:
        stoch_s = 25
    else:
        stoch_s = 60
    # Williams %R: between -80 and -20 = mid-range = healthy
    wr = _scalar(willr)
    willr_s = 85 if -75 <= wr <= -25 else 40
    # CCI: -100 to +100 = normal; outside = extreme
    cci_v = _scalar(cci)
    cci_s = 80 if -100 <= cci_v <= 100 else (100 if 100 < cci_v <= 200 else 20)
    # ROC: positive momentum > 2% gets bonus
    roc_v = _scalar(roc)
    roc_s = min(100, max(0, 50 + roc_v * 5))
    momentum_score = (rsi_s * 0.30 + macd_s * 0.25 + stoch_s * 0.18 +
                      willr_s * 0.12 + cci_s * 0.08 + roc_s * 0.07)

    # ── Pillar 3: Volume / Institutional Flow (18%) ──────────────────────────
    # OBV rising with price = accumulation
    obv_s = 100 if obv_bull else 30
    # MFI: 40-70 = healthy; >80 = overbought; <20 = oversold
    mfi_v = _scalar(mfi)
    mfi_s = 90 if 40 <= mfi_v <= 70 else (70 if 20 < mfi_v < 40 or 70 < mfi_v < 80 else 20)
    # Volume ratio vs avg: 1.2x-3x ideal
    vr_s = min(100, vol_ratio * 40) if vol_ratio >= 1.0 else vol_ratio * 30
    volume_score = obv_s * 0.40 + mfi_s * 0.35 + vr_s * 0.25

    # ── Pillar 4: Volatility Setup (14%) ────────────────────────────────────
    # ATR%: 0.3-4% sweet spot for daily
    if 0.3 <= atr_pct <= 4.0:
        atr_s = min(100, atr_pct * 25)
    elif atr_pct < 0.1:
        atr_s = 0   # dead market
    else:
        atr_s = max(0, 100 - (atr_pct - 4) * 15)   # penalty for extreme vol
    # BB width: narrow = squeeze (breakout incoming) = high score
    # Typical BB width 2-8%; below 2% = squeeze; above 10% = too wide
    bbw_s = 100 if bb_width < 2.5 else (80 if bb_width < 5 else (50 if bb_width < 8 else 20))
    volatility_score = atr_s * 0.60 + bbw_s * 0.40

    # ── Pillar 5: Breakout Signal (10%) ─────────────────────────────────────
    # Donchian: near top (>0.8) or near bottom (<0.2) = potential breakout
    don_s = 100 if don_pct > 0.85 else (80 if don_pct > 0.70 else
            (75 if don_pct < 0.15 else (60 if don_pct < 0.30 else 40)))
    # Fibonacci: price near key fib = high confluence
    fib_s = max(0, 100 - fib_dist * 200)   # 0 dist = 100, 0.5 = 0
    breakout_score = don_s * 0.55 + fib_s * 0.45

    # ── Pillar 6: Market Structure (8%) ─────────────────────────────────────
    # Hurst: trending (>0.55) or mean-reverting (<0.45) both useful; random = bad
    hurst_s = 90 if hurst_val > 0.55 else (80 if hurst_val < 0.45 else 40)
    # VWAP: price above VWAP = bullish institutional bias
    pvwap = _scalar(out["price_vs_vwap"])
    vwap_s = 90 if pvwap > 0 else 40
    structure_score = hurst_s * 0.55 + vwap_s * 0.45

    # ── Composite ────────────────────────────────────────────────────────────
    composite = (
        trend_score      * 0.28 +
        momentum_score   * 0.22 +
        volume_score     * 0.18 +
        volatility_score * 0.14 +
        breakout_score   * 0.10 +
        structure_score  * 0.08
    )

    out["score"]            = round(composite, 1)
    out["trend_score"]      = round(trend_score, 1)
    out["momentum_score"]   = round(momentum_score, 1)
    out["volume_score"]     = round(volume_score, 1)
    out["volatility_score"] = round(volatility_score, 1)
    out["breakout_score"]   = round(breakout_score, 1)
    out["structure_score"]  = round(structure_score, 1)
    out["close"]            = round(price, 4)

    # Overall directional bias (for UI display)
    bullish_votes = sum([
        ema_stack_score >= 3,
        macd_bullish,
        rsi > 50,
        obv_bull,
        st_dir == 1,
        bull,               # SAR
        price_above_cloud,
        mfi_v > 50,
        pvwap > 0,
        sk > 50,
    ])
    out["bias"] = (
        "strong_bull" if bullish_votes >= 8 else
        "bull"        if bullish_votes >= 6 else
        "neutral"     if bullish_votes >= 4 else
        "bear"        if bullish_votes >= 2 else
        "strong_bear"
    )
    out["bullish_votes"]  = bullish_votes    # out of 10
    out["bearish_votes"]  = 10 - bullish_votes

    return out
