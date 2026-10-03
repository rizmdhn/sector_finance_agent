"""APScheduler entrypoint for the ingest worker.

See idx_agent_infrastructure_diagrams_md.md section 7.
"""

import logging
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from data import ingest_status
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


def _tracked(cache, name: str, job):
    """Wraps a scheduled job so its outcome shows up in the UI (data/ingest_status.py).
    Re-raises, so APScheduler still logs the failure as before."""

    def run(*args):
        try:
            job(*args)
        except Exception as exc:
            ingest_status.record(cache, name, "failed", exc)
            raise
        ingest_status.record(cache, name, "ok")

    return run


def _run_bootstrap_job(name: str, job, cache=None) -> None:
    for attempt, delay in enumerate([0, *BOOTSTRAP_RETRY_DELAYS_SECONDS]):
        if delay:
            logger.warning("%s bootstrap hit Sectors' rate limit — retrying in %ss", name, delay)
            ingest_status.record(cache, name, "retrying", note=f"Rate limited by Sectors — retrying in {delay}s")
            time.sleep(delay)
        try:
            job()
            ingest_status.record(cache, name, "ok")
            return
        except SectorsRateLimitError as exc:
            if attempt == len(BOOTSTRAP_RETRY_DELAYS_SECONDS):
                ingest_status.record(cache, name, "failed", exc)
                logger.warning(
                    "%s bootstrap still rate-limited after %d retries — will land on its "
                    "next regular schedule instead of crashing the worker",
                    name,
                    len(BOOTSTRAP_RETRY_DELAYS_SECONDS),
                )
        except Exception as exc:
            ingest_status.record(cache, name, "failed", exc)
            # Not a rate limit (e.g. an empty sweep, a DB error): retrying won't help
            # and crashing the worker would just restart-loop it. Log the traceback
            # and carry on — _verify_landed() below reports the resulting state.
            logger.exception("%s bootstrap failed", name)
            return


def _verify_landed(db) -> None:
    """Confirms the bootstrap actually left rows in Postgres, instead of trusting that
    "no exception" means "data persisted" — found live on a second machine: credits
    spent, ticker list never appeared."""
    counts = db.table_counts()
    if counts["symbol_master"]:
        logger.info("symbol_master bootstrap OK — Postgres now holds %d symbols", counts["symbol_master"])
    else:
        logger.error(
            "symbol_master bootstrap finished but Postgres holds 0 symbols — check the log "
            "above, then run `docker compose exec ingest-worker python -m ingest.cli status`"
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
        _run_bootstrap_job("symbol_master", lambda: symbol_master.run_essential(db, client, cache), cache)
        _verify_landed(db)

    scheduler.add_job(
        _tracked(cache, "symbol_master", symbol_master.run),
        CronTrigger(day_of_week="mon", hour=3, minute=0, timezone=TIMEZONE),
        args=[db, cache, client],
        id="symbol_master_weekly",
    )

    # IDX closes ~16:00-16:15 WIB; poll every 5 minutes until today's date lands.
    # TODO: confirm when daily data actually lands after close (plan doc section 8).
    scheduler.add_job(
        _tracked(cache, "universe_close", universe_close.run),
        CronTrigger(hour="16-19", minute="*/5", timezone=TIMEZONE),
        args=[db, cache, client],
        id="universe_close_poll",
    )

    return scheduler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build_scheduler().start()
