from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
import pandas as pd


@dataclass
class Signal:
    symbol: str
    action: str  # "buy", "sell", "hold"
    confidence: float  # 0.0 to 1.0
    indicators: dict
    timestamp: datetime


class BaseStrategy(ABC):
    def __init__(self, symbol: str, parameters: dict, risk_config: dict):
        self.symbol = symbol
        self.parameters = parameters
        self.risk_config = risk_config

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Given OHLCV dataframe, return a trading signal."""
        pass

    def get_position_size_pct(self) -> float:
        return self.risk_config.get("position_size_pct", 5.0)

    def get_stop_loss_pct(self) -> float:
        return self.risk_config.get("stop_loss_pct", 2.0)

    def get_take_profit_pct(self) -> float:
        return self.risk_config.get("take_profit_pct", 4.0)
