"""Human approval for credit-spending Sectors calls.

A request that opts in (admin-ui sends `X-Approval-Mode: ask`) gets a gate installed
(gateway/main.py). When a tool is about to make a REAL Sectors call, the gate parks
that tool's thread here as a pending approval; admin-ui polls for it, shows it in the
chat, and POSTs the decision. The decision lives server-side and only an
authenticated HTTP call can set it, so the model has no way to approve itself — an
approval path through the chat text ("yes, go ahead") would let it.

ponytail: in-memory, so this only works with a single gateway process (what
`uvicorn gateway.main:app` runs today). Move pending/allowed to Valkey if the gateway
is ever scaled to several workers.
"""

import threading
import time
import uuid

from data.credit_gate import SectorsCallDenied

APPROVAL_TIMEOUT_SECONDS = 120
# "Allow this reply" lets one chat reply spend up to this many more credits without asking
# again, so a broad question (an index, many tickers) is one prompt, not dozens. A cap, not
# unlimited, because a model can fan out far more calls than the user pictured.
REPLY_ALLOWANCE_CREDITS = 10
DECISIONS = ("approve", "approve_reply", "approve_session", "deny")


class _Pending:
    def __init__(self, gate: "_SessionGate", session_id: str, description: str, credits: int):
        self.gate = gate
        self.id = uuid.uuid4().hex
        self.session_id = session_id
        self.description = description
        self.credits = credits
        self.created = time.monotonic()
        self.event = threading.Event()
        self.decision: str | None = None


class _SessionGate:
    def __init__(self, broker: "ApprovalBroker", session_id: str):
        self._broker, self._session_id = broker, session_id
        self.allowance = 0  # credits left from an "Allow this reply" answer; one gate = one reply

    def check(self, description: str, credits: int) -> None:
        self._broker.request(self._session_id, description, credits, self)


class ApprovalBroker:
    def __init__(self, timeout_seconds: float = APPROVAL_TIMEOUT_SECONDS):
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._allowed_sessions: set[str] = set()

    def gate_for(self, session_id: str) -> _SessionGate:
        return _SessionGate(self, session_id)

    def request(self, session_id: str, description: str, credits: int, gate: "_SessionGate | None" = None) -> None:
        """Blocks the calling (tool) thread until answered. Returns to allow the call,
        raises SectorsCallDenied otherwise — including when nobody answers in time."""
        with self._lock:
            if session_id in self._allowed_sessions:
                return
            if gate is not None and gate.allowance >= credits:
                gate.allowance -= credits
                return
            pending = _Pending(gate, session_id, description, credits)
            self._pending[pending.id] = pending

        pending.event.wait(self._timeout)

        with self._lock:
            self._pending.pop(pending.id, None)
            decision = pending.decision
            if decision == "approve_session":
                self._allowed_sessions.add(session_id)
        if decision in ("approve", "approve_reply", "approve_session"):
            return
        if decision is None:
            raise SectorsCallDenied(
                f"No approval arrived within {int(self._timeout)}s, so the Sectors call was not made "
                "(no credits spent). Tell the user what data you couldn't fetch and why."
            )
        raise SectorsCallDenied(
            "The user declined this Sectors API call (no credits spent). Do not retry it; "
            "tell the user what data you couldn't fetch."
        )

    def list_pending(self, session_id: str) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            return [
                {
                    "id": p.id,
                    "description": p.description,
                    "credits": p.credits,
                    "waiting_seconds": int(now - p.created),
                    "timeout_seconds": int(self._timeout),
                }
                for p in self._pending.values()
                if p.session_id == session_id
            ]

    def resolve(self, approval_id: str, decision: str) -> bool:
        """False if it's already gone (answered, or timed out). "Approve for this chat"
        (or "this reply", up to its credit cap) also releases every other call of that session currently waiting."""
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None:
                return False
            pending.decision = decision
            pending.event.set()
            if decision == "approve_session":
                for other in self._pending.values():
                    if other.session_id == pending.session_id and other.decision is None:
                        other.decision = "approve"
                        other.event.set()
            elif decision == "approve_reply":
                pending.gate.allowance = REPLY_ALLOWANCE_CREDITS - pending.credits
                for other in self._pending.values():
                    if other.gate is pending.gate and other.decision is None and other.credits <= pending.gate.allowance:
                        pending.gate.allowance -= other.credits
                        other.decision = "approve"
                        other.event.set()
            return True
