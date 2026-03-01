import logging
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("tunefolio.scheduler")

IST = pytz.timezone("Asia/Kolkata")

_scheduler: BackgroundScheduler | None = None


def _run_trade_sync():
    """Wrapper that catches all exceptions so APScheduler never kills the job."""
    try:
        from backend.app.services.trade_sync import sync_trades_from_kite
        result = sync_trades_from_kite()
        logger.info(f"Scheduled trade sync result: {result}")
    except Exception as e:
        logger.error(f"Scheduled trade sync failed: {e}", exc_info=True)


def start_scheduler():
    global _scheduler

    if _scheduler and _scheduler.running:
        logger.warning("Scheduler already running, skipping start")
        return

    _scheduler = BackgroundScheduler(timezone=IST)

    # 8:30 AM IST, Monday-Friday
    _scheduler.add_job(
        _run_trade_sync,
        trigger=CronTrigger(hour=8, minute=30, day_of_week="mon-fri", timezone=IST),
        id="trade_sync_morning",
        name="Trade sync (8:30 AM IST)",
        replace_existing=True,
    )

    # 6:00 PM IST, Monday-Friday
    _scheduler.add_job(
        _run_trade_sync,
        trigger=CronTrigger(hour=18, minute=0, day_of_week="mon-fri", timezone=IST),
        id="trade_sync_evening",
        name="Trade sync (6:00 PM IST)",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Trade sync scheduler started (8:30 AM + 6:00 PM IST, Mon-Fri)")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Trade sync scheduler stopped")
        _scheduler = None


def get_scheduler_status() -> dict:
    """Return info about scheduled jobs and their next run times."""
    if not _scheduler or not _scheduler.running:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })

    return {"running": True, "jobs": jobs}
