from .rsi import RSIStrategy
from .macd import MACDStrategy
from .ma_crossover import MACrossoverStrategy
from .bollinger import BollingerStrategy
from .momentum import MomentumStrategy
from .scalping import ScalpingStrategy
from .adaptive import AdaptiveStrategy

STRATEGY_TEMPLATES = {
    "rsi": {
        "name": "RSI Mean Reversion",
        "description": "Buys when RSI is oversold, sells when overbought. Best for range-bound markets.",
        "parameters": {
            "rsi_period": {"type": "int", "default": 14, "min": 5, "max": 50, "label": "RSI Period"},
            "oversold_level": {"type": "int", "default": 30, "min": 10, "max": 45, "label": "Oversold Level"},
            "overbought_level": {"type": "int", "default": 70, "min": 55, "max": 90, "label": "Overbought Level"},
            "timeframe": {"type": "select", "default": "1Day", "options": ["1Min", "5Min", "15Min", "1Hour", "1Day"], "label": "Timeframe"},
        },
        "risk_config": {
            "stop_loss_pct": {"type": "float", "default": 2.0, "min": 0.5, "max": 10.0, "label": "Stop Loss %"},
            "take_profit_pct": {"type": "float", "default": 4.0, "min": 1.0, "max": 20.0, "label": "Take Profit %"},
            "position_size_pct": {"type": "float", "default": 2.0, "min": 0.5, "max": 10.0, "label": "Position Size %"},
        },
        "class": RSIStrategy,
    },
    "macd": {
        "name": "MACD Crossover",
        "description": "Trades MACD line crossovers. Best for trending markets.",
        "parameters": {
            "fast_period": {"type": "int", "default": 12, "min": 5, "max": 30, "label": "Fast EMA Period"},
            "slow_period": {"type": "int", "default": 26, "min": 15, "max": 60, "label": "Slow EMA Period"},
            "signal_period": {"type": "int", "default": 9, "min": 3, "max": 20, "label": "Signal Period"},
            "timeframe": {"type": "select", "default": "1Day", "options": ["15Min", "1Hour", "1Day"], "label": "Timeframe"},
        },
        "risk_config": {
            "stop_loss_pct": {"type": "float", "default": 3.0, "min": 0.5, "max": 10.0, "label": "Stop Loss %"},
            "take_profit_pct": {"type": "float", "default": 6.0, "min": 1.0, "max": 20.0, "label": "Take Profit %"},
            "position_size_pct": {"type": "float", "default": 2.0, "min": 0.5, "max": 10.0, "label": "Position Size %"},
        },
        "class": MACDStrategy,
    },
    "ma_crossover": {
        "name": "Moving Average Crossover",
        "description": "Golden cross / death cross strategy. Simple and effective trend follower.",
        "parameters": {
            "fast_ma": {"type": "int", "default": 20, "min": 5, "max": 100, "label": "Fast MA Period"},
            "slow_ma": {"type": "int", "default": 50, "min": 20, "max": 200, "label": "Slow MA Period"},
            "ma_type": {"type": "select", "default": "EMA", "options": ["SMA", "EMA"], "label": "MA Type"},
            "timeframe": {"type": "select", "default": "1Day", "options": ["1Hour", "1Day"], "label": "Timeframe"},
        },
        "risk_config": {
            "stop_loss_pct": {"type": "float", "default": 3.0, "min": 0.5, "max": 10.0, "label": "Stop Loss %"},
            "take_profit_pct": {"type": "float", "default": 8.0, "min": 1.0, "max": 30.0, "label": "Take Profit %"},
            "position_size_pct": {"type": "float", "default": 2.0, "min": 0.5, "max": 10.0, "label": "Position Size %"},
        },
        "class": MACrossoverStrategy,
    },
    "bollinger": {
        "name": "Bollinger Band Squeeze",
        "description": "Buys at lower band, sells at upper band. Great for mean-reverting assets.",
        "parameters": {
            "period": {"type": "int", "default": 20, "min": 10, "max": 50, "label": "BB Period"},
            "std_dev": {"type": "float", "default": 2.0, "min": 1.0, "max": 3.5, "label": "Std Deviations"},
            "timeframe": {"type": "select", "default": "1Day", "options": ["15Min", "1Hour", "1Day"], "label": "Timeframe"},
        },
        "risk_config": {
            "stop_loss_pct": {"type": "float", "default": 2.5, "min": 0.5, "max": 10.0, "label": "Stop Loss %"},
            "take_profit_pct": {"type": "float", "default": 5.0, "min": 1.0, "max": 20.0, "label": "Take Profit %"},
            "position_size_pct": {"type": "float", "default": 2.0, "min": 0.5, "max": 10.0, "label": "Position Size %"},
        },
        "class": BollingerStrategy,
    },
    "momentum": {
        "name": "Price Momentum",
        "description": "Buys assets with strong recent momentum. Works best in trending markets.",
        "parameters": {
            "lookback_period": {"type": "int", "default": 20, "min": 5, "max": 60, "label": "Lookback Period"},
            "momentum_threshold": {"type": "float", "default": 5.0, "min": 1.0, "max": 20.0, "label": "Momentum Threshold %"},
            "volume_filter": {"type": "bool", "default": True, "label": "Require Volume Confirmation"},
            "timeframe": {"type": "select", "default": "1Day", "options": ["1Hour", "1Day"], "label": "Timeframe"},
        },
        "risk_config": {
            "stop_loss_pct": {"type": "float", "default": 4.0, "min": 0.5, "max": 10.0, "label": "Stop Loss %"},
            "take_profit_pct": {"type": "float", "default": 10.0, "min": 1.0, "max": 30.0, "label": "Take Profit %"},
            "position_size_pct": {"type": "float", "default": 2.0, "min": 0.5, "max": 10.0, "label": "Position Size %"},
        },
        "class": MomentumStrategy,
    },
    "scalping": {
        "name": "Auto Scalper",
        "description": "Fast 5Min EMA+RSI+Volume scalper. Auto-assigned to top market movers.",
        "parameters": {
            "fast_ema": {"type": "int", "default": 9, "min": 5, "max": 20, "label": "Fast EMA"},
            "slow_ema": {"type": "int", "default": 21, "min": 10, "max": 50, "label": "Slow EMA"},
            "rsi_period": {"type": "int", "default": 14, "min": 7, "max": 21, "label": "RSI Period"},
            "rsi_buy_level": {"type": "int", "default": 45, "min": 30, "max": 55, "label": "RSI Buy Level"},
            "rsi_sell_level": {"type": "int", "default": 55, "min": 45, "max": 70, "label": "RSI Sell Level"},
            "vol_multiplier": {"type": "float", "default": 1.3, "min": 1.0, "max": 3.0, "label": "Min Volume Ratio"},
            "timeframe": {"type": "select", "default": "5Min", "options": ["1Min", "5Min", "15Min"], "label": "Timeframe"},
        },
        "risk_config": {
            "stop_loss_pct": {"type": "float", "default": 0.3, "min": 0.1, "max": 2.0, "label": "Stop Loss %"},
            "take_profit_pct": {"type": "float", "default": 0.5, "min": 0.2, "max": 3.0, "label": "Take Profit %"},
            "position_size_pct": {"type": "float", "default": 2.0, "min": 0.5, "max": 5.0, "label": "Position Size %"},
        },
        "class": ScalpingStrategy,
    },
    "adaptive": {
        "name": "Adaptive (All-Weather)",
        "description": "Auto-detects bull / bear / sideways / crash and switches signal logic. Targets 1–2 trades/week in any market condition.",
        "parameters": {
            "fast_ema":           {"type": "int",   "default": 9,    "min": 3,    "max": 30,   "label": "Fast EMA"},
            "slow_ema":           {"type": "int",   "default": 21,   "min": 10,   "max": 60,   "label": "Slow EMA"},
            "rsi_period":         {"type": "int",   "default": 14,   "min": 7,    "max": 21,   "label": "RSI Period"},
            "hurst_lookback":     {"type": "int",   "default": 60,   "min": 30,   "max": 120,  "label": "Hurst Lookback (bars)"},
            "vol_lookback":       {"type": "int",   "default": 60,   "min": 20,   "max": 120,  "label": "Vol Lookback (bars)"},
            "trend_hurst_min":    {"type": "float", "default": 0.55, "min": 0.50, "max": 0.70, "label": "Trending if H >"},
            "sideways_hurst_max": {"type": "float", "default": 0.45, "min": 0.30, "max": 0.50, "label": "Sideways if H <"},
            "crash_vol_pct":      {"type": "float", "default": 85.0, "min": 60.0, "max": 99.0, "label": "Crash if Vol% >"},
            "rsi_oversold":       {"type": "float", "default": 30.0, "min": 15.0, "max": 45.0, "label": "RSI Oversold (sideways buy)"},
            "rsi_overbought":     {"type": "float", "default": 70.0, "min": 55.0, "max": 85.0, "label": "RSI Overbought (sideways sell)"},
            "rsi_trend_long":     {"type": "float", "default": 45.0, "min": 30.0, "max": 55.0, "label": "RSI Min for Trend Long"},
            "rsi_trend_short":    {"type": "float", "default": 55.0, "min": 45.0, "max": 70.0, "label": "RSI Max for Trend Short"},
            "crash_allow_short":  {"type": "bool",  "default": True,              "label": "Short in Crash Regime"},
            "bb_period":          {"type": "int",   "default": 20,   "min": 10,   "max": 50,   "label": "Bollinger Period (sideways)"},
            "timeframe":          {"type": "select","default": "1Day","options": ["15Min", "1Hour", "1Day"], "label": "Timeframe"},
        },
        "risk_config": {
            "stop_loss_pct":     {"type": "float", "default": 1.5,  "min": 0.5,  "max": 5.0,  "label": "Stop Loss %"},
            "take_profit_pct":   {"type": "float", "default": 3.0,  "min": 1.0,  "max": 15.0, "label": "Take Profit %"},
            "position_size_pct": {"type": "float", "default": 5.0,  "min": 1.0,  "max": 20.0, "label": "Position Size %"},
        },
        "class": AdaptiveStrategy,
    },
}


def get_strategy(template: str, parameters: dict, risk_config: dict, symbol: str):
    if template not in STRATEGY_TEMPLATES:
        raise ValueError(f"Unknown strategy template: {template}")
    cls = STRATEGY_TEMPLATES[template]["class"]
    return cls(symbol=symbol, parameters=parameters, risk_config=risk_config)
