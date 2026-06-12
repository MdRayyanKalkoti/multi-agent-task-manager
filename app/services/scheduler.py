"""APScheduler jobs: daily digest + hourly overdue sweep, driven by the Reminder Agent."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.agents.reminder_agent import reminder_agent
from app.config import get_settings
from app.logger import get_logger
from app.services.telegram_bot import telegram_service

logger = get_logger("services.scheduler")

scheduler = AsyncIOScheduler()


async def _daily_digest_job() -> None:
    if telegram_service.app is None:
        logger.info("Daily digest skipped: Telegram bot not running.")
        return
    await reminder_agent.send_daily_digest(telegram_service.send)


async def _overdue_sweep_job() -> None:
    if telegram_service.app is None:
        return
    await reminder_agent.send_overdue_alerts(telegram_service.send)


def start_scheduler() -> None:
    settings = get_settings()
    scheduler.configure(timezone=settings.timezone)
    scheduler.add_job(
        _daily_digest_job,
        CronTrigger(hour=settings.daily_reminder_hour, minute=settings.daily_reminder_minute),
        id="daily_digest",
        replace_existing=True,
    )
    scheduler.add_job(
        _overdue_sweep_job,
        IntervalTrigger(hours=1),
        id="overdue_sweep",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started (digest %02d:%02d %s, overdue sweep hourly).",
        settings.daily_reminder_hour, settings.daily_reminder_minute, settings.timezone,
    )


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
