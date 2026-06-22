from dotenv import load_dotenv
load_dotenv()  # loads .env before any module reads os.getenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from database import init_db
from routers import strategies, portfolio, trades, market, automation, scanner
from routers import backtest, autopilot as autopilot_router
from routers import events as events_router
from routers import sessions as sessions_router
from routers import grok_stream as grok_stream_router
import situational_awareness as sa_module
import grok_trader as grok_trader_module


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Start scheduler
    from scheduler import start_scheduler
    await start_scheduler()

    # Auto-scan market and spin up top-10 scalpers
    from autosetup import run_auto_setup
    import asyncio
    asyncio.create_task(run_auto_setup())

    # Re-scan every 4 hours to rotate assets
    from apscheduler.triggers.interval import IntervalTrigger
    from scheduler import get_scheduler
    sched = get_scheduler()
    sched.add_job(
        run_auto_setup,
        trigger=IntervalTrigger(hours=4),
        id="auto_rescan",
        name="Market rescan (4h)",
        replace_existing=True,
        coalesce=True,
    )

    yield

    sched = get_scheduler()
    if sched.running:
        sched.shutdown(wait=False)


app = FastAPI(title="AlgoTrader API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(strategies.router)
app.include_router(portfolio.router)
app.include_router(trades.router)
app.include_router(market.router)
app.include_router(automation.router)
app.include_router(scanner.router)
app.include_router(backtest.router)
app.include_router(autopilot_router.router)
app.include_router(events_router.router)
app.include_router(sessions_router.router)
app.include_router(grok_stream_router.router)
if sa_module.router is not None:
    app.include_router(sa_module.router)
if grok_trader_module.router is not None:
    app.include_router(grok_trader_module.router)


@app.get("/health")
async def health():
    from scheduler import get_scheduler_status
    from autosetup import get_last_scan
    return {
        "status": "ok",
        "version": "1.0.0",
        "scheduler": get_scheduler_status(),
        "last_scan": get_last_scan(),
    }
