import { useEffect, useRef, useState, type FormEvent } from "react";
import { createSession, listSessions, sendMessage } from "../api/chat";
import type { ChatSession } from "../types";

export default function Chat({ userId }: { userId: string }) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      const loaded = await listSessions(userId);
      setSessions(loaded);
      setActiveId(loaded[0]?.id ?? null);
      setLoading(false);
    }
    load();
  }, [userId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeId, sessions]);

  const active = sessions.find((session) => session.id === activeId) ?? null;

  async function handleNewSession() {
    const session = await createSession(userId);
    setSessions((prev) => [session, ...prev]);
    setActiveId(session.id);
  }

  async function handleSend(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim() || !activeId) return;
    const text = draft.trim();
    setDraft("");
    setSending(true);
    try {
      const updated = await sendMessage(activeId, text);
      setSessions((prev) => prev.map((session) => (session.id === updated.id ? updated : session)));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="page page--chat">
      <header className="page-header">
        <h1>Chat</h1>
        <p className="subtitle">
          Chatting as <strong>{userId}</strong>. Each session on the left keeps its own history, like a real
          conversation with the Chief would (session id scopes history server-side — see
          gateway/roles/orchestrator.py).
        </p>
      </header>

      <div className="chat-layout">
        <aside className="session-list">
          <button className="session-new-btn" onClick={handleNewSession}>
            + New session
          </button>
          {loading && <p className="status-line">Loading…</p>}
          {!loading && sessions.length === 0 && <p className="empty-note">No sessions yet.</p>}
          <ul>
            {sessions.map((session) => (
              <li key={session.id}>
                <button
                  className={`session-item ${session.id === activeId ? "session-item--active" : ""}`}
                  onClick={() => setActiveId(session.id)}
                >
                  <span className="session-title">{session.title}</span>
                  <span className="session-count">{session.messages.length}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <section className="chat-panel">
          {!active && !loading && (
            <div className="chat-empty">
              <p>No session selected.</p>
              <button className="session-new-btn" onClick={handleNewSession}>
                Start one
              </button>
            </div>
          )}

          {active && (
            <>
              <div className="chat-messages">
                {active.messages.length === 0 && <p className="empty-note">Say something to start this session.</p>}
                {active.messages.map((message) => (
                  <div key={message.id} className={`chat-bubble chat-bubble--${message.role}`}>
                    <div className="chat-bubble-role">{message.role === "user" ? userId : "assistant"}</div>
                    <p>{message.content}</p>
                  </div>
                ))}
                {sending && <div className="chat-bubble chat-bubble--assistant chat-bubble--typing">…</div>}
                <div ref={bottomRef} />
              </div>

              <form className="chat-input-row" onSubmit={handleSend}>
                <input
                  className="chat-input"
                  type="text"
                  placeholder="Ask about a company, your portfolio, or a market move…"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  disabled={sending}
                />
                <button type="submit" disabled={sending || !draft.trim()}>
                  Send
                </button>
              </form>
            </>
          )}
        </section>
      </div>

      <p className="mock-note">Mock replies only — not wired to /v1/chat/completions yet. See src/api/chat.ts.</p>
    </div>
  );
}
