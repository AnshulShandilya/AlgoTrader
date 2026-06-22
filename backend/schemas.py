from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime
from models import StrategyStatus, TradeStatus, TradeSide


class StrategyCreate(BaseModel):
    name: str
    template: str
    symbol: str
    parameters: dict[str, Any]
    risk_config: dict[str, Any]


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None
    risk_config: Optional[dict[str, Any]] = None
    status: Optional[StrategyStatus] = None


class StrategyOut(BaseModel):
    id: int
    name: str
    template: str
    symbol: str
    parameters: dict[str, Any]
    risk_config: dict[str, Any]
    status: StrategyStatus
    created_at: datetime
    total_trades: int
    winning_trades: int
    total_pnl: float
    win_rate: float = 0.0

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_winrate(cls, obj):
        data = cls.model_validate(obj)
        data.win_rate = round((obj.winning_trades / obj.total_trades * 100) if obj.total_trades > 0 else 0, 1)
        return data


class TradeOut(BaseModel):
    id: int
    strategy_id: Optional[int]
    strategy_name: Optional[str]
    symbol: str
    side: TradeSide
    qty: float
    entry_price: Optional[float]
    stop_loss_price: Optional[float]
    take_profit_price: Optional[float]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    pnl: Optional[float]
    pnl_pct: Optional[float]
    initial_risk: Optional[float]
    r_multiple: Optional[float]
    pre_trade_probability: Optional[float]
    pre_trade_expected_r: Optional[float]
    setup_score: Optional[int]
    session_date: Optional[str]
    journal_notes: Optional[str]
    trade_type: Optional[str]
    trade_logic: Optional[str]
    expected_profit: Optional[float]
    expected_profit_pct: Optional[float]
    catalyst: Optional[str]
    catalyst_source: Optional[str]
    status: TradeStatus
    alpaca_order_id: Optional[str]
    opened_at: datetime
    closed_at: Optional[datetime]
    notes: Optional[str]

    model_config = {"from_attributes": True}


class BotSettingsSchema(BaseModel):
    # Alpaca
    alpaca_api_key: Optional[str] = None
    alpaca_secret_key: Optional[str] = None
    paper_trading: bool = True
    # Binance Testnet
    binance_api_key: Optional[str] = None
    binance_secret_key: Optional[str] = None
    binance_testnet: bool = True
    # Risk
    max_portfolio_risk_pct: float = 2.0
    max_drawdown_pct: float = 10.0
    max_open_trades: int = 10
    max_daily_trades: int = 20
    default_position_size_pct: float = 2.0
    risk_per_trade_pct: float = 1.0
    daily_loss_limit_pct: float = 3.0
    watchlist_symbols: str = "AAPL,TSLA,NVDA,SPY,QQQ"
    # Grok autonomous trading
    grok_auto_trade: bool = False
    grok_min_confidence: int = 70


class PortfolioSnapshot(BaseModel):
    equity: float
    cash: float
    portfolio_value: float
    day_pnl: float
    day_pnl_pct: float
    total_pnl: float
    buying_power: float
    positions: list[dict]


class MarketDataPoint(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class SignalResult(BaseModel):
    symbol: str
    signal: str  # buy, sell, hold
    confidence: float
    indicators: dict[str, Any]
    timestamp: datetime
