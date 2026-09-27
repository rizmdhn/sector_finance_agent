"""Background AgentCore-style memory extraction, run after a conversation
finishes — see data/memory_llm.py for the actual extraction call and
data/memory_store.py for how extracted facts get written (through the same
consolidation path as any other memory write, so a fact the background pass
extracts and one the Chief's own add_memory tool writes are indistinguishable
afterward).

Scheduled via a Starlette `BackgroundTask` (gateway/main.py), which runs only
after the response has already been fully sent — this can never add latency to
the user's own reply. Gated entirely behind /v1/memory/settings' auto_extraction
flag: this is a full extra LLM call per conversation on top of whatever the
conversation itself already spent, real cost this project's credit-consciousness
says should be opt-in per user, not automatic.
"""

from data.db import Database
from data.memory_llm import extract_from_transcript
from data.memory_store import write_memory, write_summary


async def maybe_extract(db: Database, user_id: str, session_id: str, question: str, result: dict) -> None:
    """`result` is the same mutable dict gateway/main.py passed into
    `_stream_response`/attached to the JSON response — read here rather than
    taking `answer` directly so this can be scheduled as a BackgroundTask before
    a streamed answer is actually known; by the time this runs (after the
    response body is fully sent), result["answer"] is populated.
    """
    if not db.get_memory_settings(user_id)["auto_extraction"]:
        return
    answer = result.get("answer", "")
    if not answer:
        return

    extracted = extract_from_transcript(f"User: {question}\nAssistant: {answer}")
    for fact in extracted.get("facts") or []:
        write_memory(db, user_id, fact)
    for preference in extracted.get("preferences") or []:
        write_memory(db, user_id, preference, {"kind": "preference"})
    summary = extracted.get("summary")
    if summary:
        write_summary(db, user_id, session_id, summary)
