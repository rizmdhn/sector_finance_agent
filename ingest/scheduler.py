"""APScheduler entrypoint for the ingest worker.

See idx_agent_infrastructure_diagrams_md.md section 7.
"""

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from data.deps import get_cache, get_client, get_db
from ingest.jobs import symbol_master, universe_close

# quarterly_dates is deliberately not scheduled: its job now raises NotImplementedError
# on every run (see ingest/jobs/quarterly_dates.py) since no working bulk endpoint was
# found for it. Re-add it here once that job is redesigned or the endpoint is found.

TIMEZONE = "Asia/Jakarta"


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    db, cache, client = get_db(), get_cache(), get_client()

    scheduler.add_job(
        symbol_master.run,
        CronTrigger(day_of_week="mon", hour=3, minute=0, timezone=TIMEZONE),
        args=[db, cache, client],
        id="symbol_master_weekly",
    )

    # IDX closes ~16:00-16:15 WIB; poll every 5 minutes until today's date lands.
    # TODO: confirm when daily data actually lands after close (plan doc section 8).
    scheduler.add_job(
        universe_close.run,
        CronTrigger(hour="16-19", minute="*/5", timezone=TIMEZONE),
        args=[db, cache, client],
        id="universe_close_poll",
    )

    return scheduler


if __name__ == "__main__":
    build_scheduler().start()
