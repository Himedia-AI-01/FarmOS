"""APScheduler setup for background jobs."""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from jobs.update_segments import run as update_segments_run
from app.services.revenue_sync import sync_orders_to_revenue
from app.database import SessionLocal

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def _sync_revenue_job():
    """Job wrapper for revenue sync."""
    db = SessionLocal()
    try:
        count = sync_orders_to_revenue(db)
        if count > 0:
            logger.info(f"Revenue sync: {count} new entries created.")
    except Exception as e:
        logger.error(f"Revenue sync failed: {e}")
        db.rollback()
    finally:
        db.close()


def setup_scheduler() -> AsyncIOScheduler:
    """Configure and return the scheduler with all jobs."""
    # Update customer segments daily at 03:00
    scheduler.add_job(
        update_segments_run,
        trigger=CronTrigger(hour=3, minute=0),
        id="update_segments",
        name="Update customer segments",
        replace_existing=True,
    )

    # Sync revenue every 30 minutes
    scheduler.add_job(
        _sync_revenue_job,
        trigger=IntervalTrigger(minutes=30),
        id="sync_revenue",
        name="Sync orders to revenue entries",
        replace_existing=True,
    )

    return scheduler
