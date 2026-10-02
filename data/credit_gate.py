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
