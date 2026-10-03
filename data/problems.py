"""Turns an exception into something a person can act on: what went wrong, and what to
do about it. Shared by the gateway (chat errors, readiness) and the ingest worker
(recorded job status), so the same failure reads the same on every screen.

Matches by class name instead of importing the provider SDKs, because the ingest worker
doesn't install anthropic/openai/strands, and walks the `__cause__`/`__context__` chain
because Strands wraps whatever the model client raised.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Problem:
    code: str
    title: str
    detail: str
    fix: str

    def as_dict(self) -> dict:
        return asdict(self)


def _chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _named(exc: BaseException, *names: str) -> bool:
    return any(cls.__name__ in names for cls in type(exc).__mro__)


def _missing_key(exc: BaseException) -> str | None:
    text = str(exc)
    return text.split(" is not set")[0] if " is not set" in text and text.split(" ")[0].isupper() else None


def describe(exc: BaseException) -> Problem:
    for e in _chain(exc):
        text = str(e).lower()
        if (key := _missing_key(e)) is not None:
            return Problem("model_key_missing", f"{key} is missing", f"The model needs {key}, which isn't set.",
                           f"Add {key} to your .env file, then run: docker compose up -d agent-gateway")
        if _named(e, "SectorsAuthError"):
            # 401 is also what Sectors sends when the key is fine but the plan lacks an
            # endpoint ("SUBSCRIPTION_DOES_NOT_ALLOW", seen live on close/) — blaming the
            # key there sent the user to check a correct .env.
            if "subscription_does_not_allow" in text:
                return Problem("sectors_plan_limit", "Your Sectors plan doesn't include this data",
                               "The API key works, but Sectors says the current subscription doesn't allow this request.",
                               "Check your plan at sectors.app (or contact them) — nothing to change in .env. "
                               "Data from other endpoints keeps working.")
            return Problem("sectors_key_invalid", "Sectors rejected the API key",
                           f"Sectors answered: {str(e)[:200]}",
                           "Check SECTORS_API_KEY in .env (no quotes or spaces), then restart: docker compose up -d")
        if _named(e, "SectorsRateLimitError"):
            return Problem("sectors_rate_limited", "Sectors rate limit reached",
                           "Sectors is receiving too many requests right now.",
                           "Wait a minute and try again. Cached answers still work.")
        if _named(e, "AuthenticationError", "PermissionDeniedError"):
            return Problem("model_key_invalid", "The model provider rejected the API key",
                           "The key is set but the provider answered 401/403.",
                           "Check ANTHROPIC_API_KEY / OPENAI_API_KEY in .env, then restart: docker compose up -d agent-gateway")
        if "credit balance" in text or "insufficient_quota" in text or "exceeded your current quota" in text:
            return Problem("model_out_of_credit", "The model account is out of credit",
                           "The provider refused the request because the account has no balance or quota left.",
                           "Top up the provider account, or switch the chat model to one with a funded key.")
        if _named(e, "RateLimitError"):
            return Problem("model_rate_limited", "The model provider is rate limiting",
                           "Too many requests to the model in a short time.",
                           "Wait a moment and try again.")
        if _named(e, "APIConnectionError", "APITimeoutError", "OverloadedError", "ServiceUnavailableError") or "overloaded" in text:
            return Problem("model_unreachable", "Could not reach the model provider",
                           "The request timed out or the provider is overloaded.",
                           "Try again shortly. If it keeps happening, check your internet connection and the provider's status page.")
        if _named(e, "OperationalError", "InterfaceError"):
            return Problem("database_unavailable", "The database is unavailable",
                           "Postgres refused or dropped the connection.",
                           "Check it is running: docker compose ps postgres  (logs: docker compose logs postgres)")
        if _named(e, "ConnectionError", "TimeoutError") and "redis" in type(e).__module__:
            return Problem("cache_unavailable", "The cache is unavailable",
                           "Valkey refused or dropped the connection.",
                           "Check it is running: docker compose ps valkey  (logs: docker compose logs valkey)")
    return Problem("unexpected_error", "Something went wrong",
                   f"{type(exc).__name__}: {str(exc)[:300]}",
                   "Try again. If it repeats, check the gateway logs: docker compose logs agent-gateway")
