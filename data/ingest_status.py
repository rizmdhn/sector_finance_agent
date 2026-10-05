"""What the last run of each ingest job did, kept in Valkey so the gateway (a different
container) can show it in the UI instead of leaving "setting up…" spinning forever when
a job has actually failed. Short-lived on purpose: it is a status light, not a log."""

import hashlib
import os
from datetime import datetime, timezone

from data.cache import Cache
from data.problems import describe

STATUS_TTL_SECONDS = 7 * 24 * 60 * 60


def key_fingerprint() -> str:
    """Short hash of the Sectors key in use, so a recorded failure can be told apart from
    one that happened under an older key. Not reversible; safe to store."""
    return hashlib.sha256(os.environ.get("SECTORS_API_KEY", "").encode()).hexdigest()[:8]


def _key(job: str) -> str:
    return f"ingest:status:{job}"


def record(cache: Cache | None, job: str, state: str, exc: BaseException | None = None, note: str = "") -> None:
    """state: "ok" | "retrying" | "failed". Never raises — a status write must not break a job."""
    if cache is None:
        return
    try:
        cache.set(
            _key(job),
            {
                "state": state,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "note": note,
                "key": key_fingerprint(),
                "problem": describe(exc).as_dict() if exc is not None else None,
            },
            ttl=STATUS_TTL_SECONDS,
        )
    except Exception:
        pass


def read(cache: Cache, job: str) -> dict | None:
    """A failure recorded under a different Sectors key is stale — the key was changed
    since — so it is dropped instead of leaving a banner that no new run can clear until
    the next scheduled poll."""
    status = cache.get(_key(job))
    if status and status["state"] == "failed" and status.get("key") != key_fingerprint():
        return None
    return status
