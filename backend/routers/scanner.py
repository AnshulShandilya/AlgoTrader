from fastapi import APIRouter, BackgroundTasks
from autosetup import run_auto_setup, get_last_scan

router = APIRouter(prefix="/scanner", tags=["scanner"])


@router.get("/last")
async def get_last_scan_results():
    return get_last_scan()


@router.post("/run")
async def trigger_scan(background_tasks: BackgroundTasks):
    """Trigger a full market scan and strategy rebuild in the background."""
    background_tasks.add_task(run_auto_setup, force=True)
    return {"status": "scan_started", "message": "Scanning market — results in ~30 seconds"}


@router.post("/run-sync")
async def trigger_scan_sync():
    """Trigger scan synchronously — waits for result (may take 30s)."""
    result = await run_auto_setup(force=True)
    return result
