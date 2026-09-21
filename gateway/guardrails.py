"""Guardrails: disclaimers and per-user rate limits.

Tools themselves are read-only by construction (gateway/tools/*.py has no write tool),
so there is nothing to enforce there. See idx_agent_infrastructure_diagrams_md.md
section 12.
"""

from data.cache import Cache

NOT_FINANCIAL_ADVICE_NOTICE = (
    "\n\n_This is informational only, not financial advice. "
    "Always do your own research before investing._"
)

DEFAULT_RATE_LIMIT_PER_MINUTE = 20


class RateLimitExceededError(Exception):
    pass


def enforce_rate_limit(
    cache: Cache, user_id: str, limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE
) -> None:
    """Fixed-window counter per user per minute.

    A second layer behind the proxy's own per-user rate limit
    (idx_agent_infrastructure_diagrams_md.md section 12) — this one protects the
    LLM/tool budget specifically, not raw HTTP throughput.
    """
    key = f"ratelimit:{user_id}:{_current_minute_window()}"
    count = cache.incr_with_expiry(key, ttl=60)
    if count > limit_per_minute:
        raise RateLimitExceededError(f"rate limit exceeded for user {user_id}")


def _current_minute_window() -> int:
    import time

    return int(time.time() // 60)


def attach_disclaimer(answer: str) -> str:
    return f"{answer}{NOT_FINANCIAL_ADVICE_NOTICE}"
