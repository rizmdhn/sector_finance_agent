import { useEffect, useRef, useState, type FormEvent } from "react";
import { createSession, listSessions, sendMessage } from "../api/chat";
import { ApiError } from "../api/client";
import { ROLES } from "../api/modelTiers";
import { Markdown } from "../markdown";
import type { ChatMessage, ChatSession } from "../types";

// Friendly labels for the `step` values gateway/main.py's SSE stream can send —
// the Chief's own tool names (the 4 specialists, from ROLES, plus its 2 memory
// tools, which aren't roles so have no ROLES entry).
const STEP_LABELS: Record<string, string> = {
  ...Object.fromEntries(ROLES.map((role) => [role.id, `Consulting ${role.label}…`])),
  search_memory: "Checking memory…",
  add_memory: "Saving to memory…",
};

function stepLabel(step: string): string {
  return STEP_LABELS[step] ?? `Working (${step})…`;
}

export default function Chat({ userId }: { userId: string }) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [currentStep, setCurrentStep] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState("");
  const [loading, setLoading] = useState(true);
  const [sendError, setSendError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

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
  }, [activeId, sessions, streamingText]);

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
    setSendError(null);

    // Optimistic: show the user's own message immediately — sendMessage() below
    // also persists it, but not until the network round-trip finishes, and a
    // real gateway call can take several seconds. Without this the message the
    // user just sent doesn't appear until the reply does, which reads as "did
    // this even send?" (found live, this is what prompted this fix).
    const optimisticMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
    };
    setSessions((prev) =>
      prev.map((session) =>
        session.id === activeId ? { ...session, messages: [...session.messages, optimisticMessage] } : session
      )
    );

    const controller = new AbortController();
    abortRef.current = controller;
    setSending(true);
    setCurrentStep(null);
    setStreamingText("");
    try {
      const updated = await sendMessage(activeId, text, setCurrentStep, setStreamingText, controller.signal);
      setSessions((prev) => prev.map((session) => (session.id === updated.id ? updated : session)));
    } catch (err) {
      // The user's message is already saved (sendMessage pushes it before the
      // network call) — reload so the bubble shows even though the reply failed.
      const loaded = await listSessions(userId);
      setSessions(loaded);
      if (err instanceof ApiError && err.status === 429) {
        setSendError("Rate limited — wait a moment and try again.");
      } else {
        setSendError("Could not reach the gateway.");
      }
    } finally {
      abortRef.current = null;
      setSending(false);
      setCurrentStep(null);
      setStreamingText("");
    }
  }

  function handleStop() {
    // sendMessage() catches the resulting AbortError itself and resolves
    // normally with whatever text had already streamed in — this just
    // triggers that, it doesn't need its own try/catch.
    abortRef.current?.abort();
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
                    <Markdown text={message.content} />
                  </div>
                ))}
                {sending && streamingText && (
                  <div className="chat-bubble chat-bubble--assistant chat-bubble--streaming">
                    <div className="chat-bubble-role">assistant</div>
                    <Markdown text={streamingText} />
                  </div>
                )}
                {sending && !streamingText && (
                  <div className="chat-bubble chat-bubble--assistant chat-bubble--typing">
                    {currentStep ? (
                      <span className="chat-step-label">{stepLabel(currentStep)}</span>
                    ) : (
                      <>
                        <span className="typing-dot" />
                        <span className="typing-dot" />
                        <span className="typing-dot" />
                      </>
                    )}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>

              {sendError && <p className="status-line status-line--error chat-send-error">{sendError}</p>}

              <form className="chat-input-row" onSubmit={handleSend}>
                <input
                  className="chat-input"
                  type="text"
                  placeholder="Ask about a company, your portfolio, or a market move…"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  disabled={sending}
                />
                {sending ? (
                  <button type="button" className="chat-stop-btn" onClick={handleStop}>
                    Stop
                  </button>
                ) : (
                  <button type="submit" disabled={!draft.trim()}>
                    Send
                  </button>
                )}
              </form>
            </>
          )}
        </section>
      </div>

      <p className="mock-note">
        Real endpoint: gateway/main.py's /v1/chat/completions — a real model call, real credit spent per message.
      </p>
    </div>
  );
}
