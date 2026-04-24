import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

def start_scheduler():
    """
    Register all background jobs and start the AsyncIOScheduler.
    Runs natively in the FastAPI event loop, replacing Celery and Redis.
    """
    # Import agent functions here to avoid circular imports
    from app.agents.standup_agent import trigger_standup_for_all, post_standup_summary
    from app.agents.celebration_agent import check_and_post_celebrations
    from app.agents.reminder_agent import check_and_fire_reminders

    # 1. Trigger morning standup (Mon-Fri)
    scheduler.add_job(
        trigger_standup_for_all,
        CronTrigger(
            day_of_week="mon-fri",
            hour=settings.standup_cron_hour,
            minute=settings.standup_cron_minute,
            timezone="Asia/Kolkata",
        ),
        id="morning_standup",
        replace_existing=True,
    )

    # 2. Post standup summary (Mon-Fri)
    scheduler.add_job(
        post_standup_summary,
        CronTrigger(
            day_of_week="mon-fri",
            hour=settings.standup_summary_hour,
            minute=settings.standup_summary_minute,
            timezone="Asia/Kolkata",
        ),
        id="standup_summary",
        replace_existing=True,
    )

    # 3. Daily celebration check (Mon-Fri at 9:00 AM)
    scheduler.add_job(
        check_and_post_celebrations,
        CronTrigger(
            day_of_week="mon-fri",
            hour=9,
            minute=0,
            timezone="Asia/Kolkata",
        ),
        id="daily_celebrations",
        replace_existing=True,
    )

    # 4. Check for due reminders (Every 10 seconds)
    scheduler.add_job(
        check_and_fire_reminders,
        IntervalTrigger(seconds=10),
        id="check_reminders",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler started with 4 registered jobs")

def stop_scheduler():
    """Stop the scheduler on app shutdown."""
    scheduler.shutdown()
    logger.info("APScheduler stopped")
