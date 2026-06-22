from datetime import datetime
from sqlalchemy import String, Float, Boolean, Integer, DateTime, JSON, Text, Enum
from sqlalchemy.orm import Mapped, mapped_column
from database import Base
import enum


class StrategyStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    stopped = "stopped"


class EventType(str, enum.Enum):
    trade_open    = "trade_open"
    trade_close   = "trade_close"
    signal        = "signal"
    risk_alert    = "risk_alert"
    scan          = "scan"
    autopilot     = "autopilot"
    system        = "system"


class EventSeverity(str, enum.Enum):
    info    = "info"
    success = "success"
    warning = "warning"
    error   = "error"


class EventLog(Base):
    __tablename__ = "event_log"

    id:       Mapped[int]      = mapped_column(Integer, primary_key=True, index=True)
    type:     Mapped[str]      = mapped_column(String(30))
    severity: Mapped[str]      = mapped_column(String(20), default="info")
    title:    Mapped[str]      = mapped_column(String(200))
    body:     Mapped[str]      = mapped_column(Text, nullable=True)
    symbol:   Mapped[str]      = mapped_column(String(20), nullable=True)
    pnl:      Mapped[float]    = mapped_column(Float, nullable=True)
    meta:     Mapped[dict]     = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TradeStatus(str, enum.Enum):
    open = "open"
    closed = "closed"
    cancelled = "cancelled"


class TradeSide(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    template: Mapped[str] = mapped_column(String(50))  # rsi, macd, ma_crossover, bollinger, momentum
    symbol: Mapped[str] = mapped_column(String(20))
    parameters: Mapped[dict] = mapped_column(JSON)
    risk_config: Mapped[dict] = mapped_column(JSON)
    status: Mapped[StrategyStatus] = mapped_column(Enum(StrategyStatus), default=StrategyStatus.paused)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    winning_trades: Mapped[int] = mapped_column(Integer, default=0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    strategy_id: Mapped[int] = mapped_column(Integer, nullable=True)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[TradeSide] = mapped_column(Enum(TradeSide))
    qty: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float, nullable=True)
    stop_loss_price: Mapped[float] = mapped_column(Float, nullable=True)
    take_profit_price: Mapped[float] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(50), nullable=True)  # "signal" | "stop_loss" | "take_profit"
    pnl: Mapped[float] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float] = mapped_column(Float, nullable=True)
    initial_risk: Mapped[float] = mapped_column(Float, nullable=True)       # |entry-stop| × qty in $
    r_multiple: Mapped[float] = mapped_column(Float, nullable=True)         # pnl / initial_risk
    pre_trade_probability: Mapped[float] = mapped_column(Float, nullable=True)  # 0-100 score at entry
    pre_trade_expected_r: Mapped[float] = mapped_column(Float, nullable=True)   # expected R at entry
    setup_score: Mapped[int] = mapped_column(Integer, nullable=True)            # 0-100 composite quality
    session_date: Mapped[str] = mapped_column(String(10), nullable=True)        # "2026-06-21" UTC date
    journal_notes: Mapped[str] = mapped_column(Text, nullable=True)
    # Trade classification & reasoning
    trade_type: Mapped[str] = mapped_column(String(20), nullable=True)          # "scalping" | "day_trading" | "swing_trading"
    trade_logic: Mapped[str] = mapped_column(Text, nullable=True)               # full reasoning from Grok / strategy
    expected_profit: Mapped[float] = mapped_column(Float, nullable=True)        # $ expected at target price
    expected_profit_pct: Mapped[float] = mapped_column(Float, nullable=True)    # % expected at target price
    catalyst: Mapped[str] = mapped_column(Text, nullable=True)                  # one-line catalyst description
    catalyst_source: Mapped[str] = mapped_column(String(500), nullable=True)    # source URL
    status: Mapped[TradeStatus] = mapped_column(Enum(TradeStatus), default=TradeStatus.open)
    alpaca_order_id: Mapped[str] = mapped_column(String(100), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)


class DailySession(Base):
    """
    One record per trading day. Captures pre-session conditions and
    end-of-day outcomes so the system can learn which setups work.
    """
    __tablename__ = "daily_sessions"

    id:               Mapped[int]   = mapped_column(Integer, primary_key=True, index=True)
    date:             Mapped[str]   = mapped_column(String(10), unique=True, index=True)  # "2026-06-21"
    start_equity:     Mapped[float] = mapped_column(Float, nullable=True)
    end_equity:       Mapped[float] = mapped_column(Float, nullable=True)
    session_pnl:      Mapped[float] = mapped_column(Float, default=0.0)
    trades_taken:     Mapped[int]   = mapped_column(Integer, default=0)
    wins:             Mapped[int]   = mapped_column(Integer, default=0)
    losses:           Mapped[int]   = mapped_column(Integer, default=0)
    avg_r:            Mapped[float] = mapped_column(Float, nullable=True)
    avg_probability:  Mapped[float] = mapped_column(Float, nullable=True)  # avg pre-trade score taken
    predicted_wins:   Mapped[int]   = mapped_column(Integer, default=0)    # trades where prob >= 60%
    actual_wins_high_prob: Mapped[int] = mapped_column(Integer, default=0) # wins among high-prob trades
    regime:           Mapped[str]   = mapped_column(String(20), nullable=True)   # "trending" / "ranging"
    pre_session_bias: Mapped[str]   = mapped_column(String(20), nullable=True)   # "bullish" / "bearish" / "neutral"
    market_conditions: Mapped[dict] = mapped_column(JSON, nullable=True)         # VIX, breadth, etc.
    notes:            Mapped[str]   = mapped_column(Text, nullable=True)
    status:           Mapped[str]   = mapped_column(String(20), default="open")  # "open" | "closed"
    created_at:       Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at:        Mapped[datetime] = mapped_column(DateTime, nullable=True)


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # Alpaca
    alpaca_api_key: Mapped[str] = mapped_column(String(200), nullable=True)
    alpaca_secret_key: Mapped[str] = mapped_column(String(200), nullable=True)
    paper_trading: Mapped[bool] = mapped_column(Boolean, default=True)
    # Binance Testnet (takes priority when set)
    binance_api_key: Mapped[str] = mapped_column(String(200), nullable=True)
    binance_secret_key: Mapped[str] = mapped_column(String(200), nullable=True)
    binance_testnet: Mapped[bool] = mapped_column(Boolean, default=True)
    # Risk
    max_portfolio_risk_pct: Mapped[float] = mapped_column(Float, default=2.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=10.0)
    max_open_trades: Mapped[int] = mapped_column(Integer, default=10)
    max_daily_trades: Mapped[int] = mapped_column(Integer, default=20)
    default_position_size_pct: Mapped[float] = mapped_column(Float, default=2.0)
    risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=1.0)       # % of equity to risk per trade
    daily_loss_limit_pct: Mapped[float] = mapped_column(Float, default=3.0)     # pause all if day P&L < -X%
    # Dashboard watchlist (comma-separated symbols)
    watchlist_symbols: Mapped[str] = mapped_column(String(500), default="AAPL,TSLA,NVDA,SPY,QQQ")
    # Grok autonomous trading
    grok_auto_trade: Mapped[bool] = mapped_column(Boolean, default=False)       # master on/off for Grok auto-execution
    grok_min_confidence: Mapped[int] = mapped_column(Integer, default=70)       # min Grok confidence % to auto-trade
