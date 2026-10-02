"""Lets a caller veto a real, credit-spending Sectors call before it is made.

The gateway installs a gate for the current request (a ContextVar, so it follows the
agent into tool threads and nested specialist agents — checked), and
SectorsClient._get consults it right before the HTTP request. Cache hits never reach
that point, so only genuinely new calls ask. With no gate installed (the ingest
worker, scripts, CLI) nothing changes.
"""

import contextvars
from typing import Protocol


class SectorsCallDenied(Exception):
    """The user declined (or never answered) — the call was NOT made, no credit spent."""


class CreditGate(Protocol):
    def check(self, description: str, credits: int) -> None:
        """Return to allow the call; raise SectorsCallDenied to block it."""


_gate: contextvars.ContextVar[CreditGate | None] = contextvars.ContextVar("sectors_credit_gate", default=None)


def set_gate(gate: CreditGate | None) -> None:
    _gate.set(gate)


def current_gate() -> CreditGate | None:
    return _gate.get()


def record_spend(description: str, credits: int) -> None:
    """Note a REAL (non-cached) Sectors call on the current trace span, so the UI's trace
    view can total credits per reply. A no-op where OpenTelemetry isn't installed."""
    try:
        from opentelemetry import trace
    except ImportError:
        return
    trace.get_current_span().add_event("sectors_call", {"call": description, "credits": credits})
