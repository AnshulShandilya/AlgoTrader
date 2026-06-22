import os as _os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

# Local: sqlite+aiosqlite:///./algo_trader.db
# Railway/cloud: set DATABASE_URL=/data/algotrader.db (volume mount path)
_db_env = _os.environ.get("DATABASE_URL", "")
if _db_env.startswith("/"):
    DATABASE_URL = f"sqlite+aiosqlite:///{_db_env}"
elif _db_env:
    DATABASE_URL = _db_env
else:
    DATABASE_URL = "sqlite+aiosqlite:///./algo_trader.db"

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add columns introduced after initial schema (SQLite has no IF NOT EXISTS for ALTER)
        for col_sql in [
            "ALTER TABLE bot_settings ADD COLUMN watchlist_symbols TEXT DEFAULT 'AAPL,TSLA,NVDA,SPY,QQQ'",
            "ALTER TABLE bot_settings ADD COLUMN risk_per_trade_pct REAL DEFAULT 1.0",
            "ALTER TABLE bot_settings ADD COLUMN daily_loss_limit_pct REAL DEFAULT 3.0",
            "ALTER TABLE trades ADD COLUMN initial_risk REAL",
            "ALTER TABLE trades ADD COLUMN r_multiple REAL",
            "ALTER TABLE trades ADD COLUMN pre_trade_probability REAL",
            "ALTER TABLE trades ADD COLUMN pre_trade_expected_r REAL",
            "ALTER TABLE trades ADD COLUMN setup_score INTEGER",
            "ALTER TABLE trades ADD COLUMN session_date TEXT",
            "ALTER TABLE trades ADD COLUMN journal_notes TEXT",
            "ALTER TABLE bot_settings ADD COLUMN grok_auto_trade INTEGER DEFAULT 0",
            "ALTER TABLE bot_settings ADD COLUMN grok_min_confidence INTEGER DEFAULT 70",
            "ALTER TABLE trades ADD COLUMN trade_type TEXT",
            "ALTER TABLE trades ADD COLUMN trade_logic TEXT",
            "ALTER TABLE trades ADD COLUMN expected_profit REAL",
            "ALTER TABLE trades ADD COLUMN expected_profit_pct REAL",
            "ALTER TABLE trades ADD COLUMN catalyst TEXT",
            "ALTER TABLE trades ADD COLUMN catalyst_source TEXT",
        ]:
            try:
                await conn.execute(__import__("sqlalchemy").text(col_sql))
            except Exception:
                pass  # column already exists
