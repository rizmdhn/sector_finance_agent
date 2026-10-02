"""The approval broker parks a tool thread until a human answers — these run real
threads, since blocking-until-answered is the whole behaviour."""

import threading
import time

from data.credit_gate import SectorsCallDenied
from gateway.approvals import ApprovalBroker


def _ask(broker, results, session="s1", description="company/report/BBCA.JK/"):
    def run():
        try:
            broker.request(session, description, 2)
            results.append("allowed")
        except SectorsCallDenied as exc:
            results.append(str(exc))

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def _pending(broker, session, count=1):
    deadline = time.time() + 2
    while time.time() < deadline:
        pending = broker.list_pending(session)
        if len(pending) == count:
            return pending
        time.sleep(0.01)
    raise AssertionError(f"expected {count} pending, got {broker.list_pending(session)}")


def test_call_waits_until_approved():
    broker, results = ApprovalBroker(timeout_seconds=5), []
    thread = _ask(broker, results)
    (pending,) = _pending(broker, "s1")
    assert (pending["description"], pending["credits"]) == ("company/report/BBCA.JK/", 2)
    assert results == []  # still blocked — nothing proceeds without an answer

    assert broker.resolve(pending["id"], "approve")
    thread.join(2)

    assert results == ["allowed"]
    assert broker.list_pending("s1") == []


def test_deny_blocks_the_call_and_says_no_credit_was_spent():
    broker, results = ApprovalBroker(timeout_seconds=5), []
    thread = _ask(broker, results)
    broker.resolve(_pending(broker, "s1")[0]["id"], "deny")
    thread.join(2)

    assert "declined" in results[0] and "no credits spent" in results[0]


def test_no_answer_times_out_as_a_denial():
    broker, results = ApprovalBroker(timeout_seconds=0.2), []
    _ask(broker, results).join(2)

    assert "No approval arrived" in results[0]
    assert broker.list_pending("s1") == []  # nothing left dangling


def test_approve_for_this_chat_skips_later_prompts_and_releases_parallel_ones():
    broker, results = ApprovalBroker(timeout_seconds=5), []
    first, second = _ask(broker, results), _ask(broker, results)
    pending = _pending(broker, "s1", count=2)

    broker.resolve(pending[0]["id"], "approve_session")
    first.join(2), second.join(2)
    assert results == ["allowed", "allowed"]  # the second parallel call was released too

    broker.request("s1", "later call", 1)  # returns immediately, no prompt
    assert broker.list_pending("s1") == []


def test_approval_is_scoped_to_its_own_session():
    broker, results = ApprovalBroker(timeout_seconds=5), []
    thread = _ask(broker, results, session="alice")

    assert broker.list_pending("bob") == []
    broker.resolve(_pending(broker, "alice")[0]["id"], "approve_session")
    thread.join(2)

    other = []
    waiting = _ask(broker, other, session="bob")  # alice's blanket approval doesn't cover bob
    _pending(broker, "bob")
    broker.resolve(broker.list_pending("bob")[0]["id"], "deny")
    waiting.join(2)
    assert "declined" in other[0]


def test_resolving_something_already_gone_reports_false():
    assert ApprovalBroker().resolve("nope", "approve") is False


def test_allow_this_reply_covers_later_calls_of_that_reply_only_up_to_the_cap():
    broker, results = ApprovalBroker(timeout_seconds=5), []
    gate, other_reply = broker.gate_for("s1"), broker.gate_for("s1")

    def run(g, credits=3):
        try:
            g.check("call", credits)
            results.append("allowed")
        except SectorsCallDenied:
            results.append("denied")

    first = threading.Thread(target=run, args=(gate,))
    first.start()
    broker.resolve(_pending(broker, "s1")[0]["id"], "approve_reply")
    first.join(2)

    for _ in range(2):  # 3 + 3 + 3 = 9 of the 10-credit allowance: no prompt for these
        run(gate)
    assert results == ["allowed"] * 3 and broker.list_pending("s1") == []

    over = threading.Thread(target=run, args=(gate,))  # 12 > 10: asks again
    over.start()
    _pending(broker, "s1")
    nxt = threading.Thread(target=run, args=(other_reply,))  # another reply never inherits it
    nxt.start()
    _pending(broker, "s1", count=2)
    for p in broker.list_pending("s1"):
        broker.resolve(p["id"], "deny")
    over.join(2), nxt.join(2)
