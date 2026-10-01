"""APScheduler entrypoint for the ingest worker.

See idx_agent_infrastructure_diagrams_md.md section 7.
"""

import logging
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from data.deps import get_cache, get_client, get_db
from data.sectors_client import SectorsRateLimitError
from ingest.jobs import symbol_master, universe_close

logger = logging.getLogger(__name__)

# quarterly_dates is deliberately not scheduled: its job now raises NotImplementedError
# on every run (see ingest/jobs/quarterly_dates.py) since no working bulk endpoint was
# found for it. Re-add it here once that job is redesigned or the endpoint is found.

TIMEZONE = "Asia/Jakarta"

# Real gap found live (2026-10-01): a bootstrap job that hit a 429 was left to wait
# for its NEXT NATURAL schedule (Monday 3am / the 16-19h WIB poll) before retrying —
# on a fresh install that can be hours away, leaving admin-ui's "setting up" banner
# stuck the whole time even though the miss was a one-off burst, not a real outage.
# This is a one-time startup path, so a few short blocking retries here (not inside
# the job functions themselves, which stay simple and are also reused by the normal
# cron schedule) are worth it instead of waiting on the clock.
BOOTSTRAP_RETRY_DELAYS_SECONDS = [15, 60, 180]


def _run_bootstrap_job(name: str, job) -> None:
    for attempt, delay in enumerate([0, *BOOTSTRAP_RETRY_DELAYS_SECONDS]):
        if delay:
            logger.warning("%s bootstrap hit Sectors' rate limit — retrying in %ss", name, delay)
            time.sleep(delay)
        try:
            job()
            return
        except SectorsRateLimitError:
            if attempt == len(BOOTSTRAP_RETRY_DELAYS_SECONDS):
                logger.warning(
                    "%s bootstrap still rate-limited after %d retries — will land on its "
                    "next regular schedule instead of crashing the worker",
                    name,
                    len(BOOTSTRAP_RETRY_DELAYS_SECONDS),
                )


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    db, cache, client = get_db(), get_cache(), get_client()

    # Idempotent (CREATE TABLE IF NOT EXISTS throughout) — covers a fresh database
    # on a new machine without the README's manual `init-db` step, including the
    # case where this worker starts before agent-gateway ever has.
    db.init_schema()

    # symbol_master only otherwise runs Monday 3am WIB (below) — on a fresh database
    # (new clone/pull, first `docker compose up`) that leaves every ticker lookup
    # failing with "unknown symbol" until then. This is the one genuinely essential
    # bootstrap call: ~10 calls (one screener sweep), and nothing works without it.
    #
    # universe_close is deliberately NOT bootstrapped eagerly here anymore (it used
    # to be, for up to ~99 extra calls on a fresh install) — real cost feedback
    # (2026-10-01): this project exists specifically to control Sectors credit
    # spend, and price data was already confirmed non-essential (every price-
    # dependent tool degrades to UNAVAILABLE gracefully, see gateway/main.py's
    # get_readiness() docstring) — spending a large burst on it at every fresh boot
    # fights the project's own purpose for a feature nothing actually blocks on. It
    # arrives for free on the very next normal 16-19h WIB poll below instead.
    if not db.has_symbol_master():
        _run_bootstrap_job("symbol_master", lambda: symbol_master.run_essential(db, client))

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
