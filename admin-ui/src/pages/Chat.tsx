import { useEffect, useRef, useState, type FormEvent } from "react";
import { createSession, listSessions, sendMessage } from "../api/chat";
import { listPendingApprovals, resolveApproval, type ApprovalDecision, type PendingApproval } from "../api/approvals";
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

const ASK_APPROVAL_KEY = "idx-admin-ui.ask-approval.v1";
const APPROVAL_POLL_MS = 1000;

// On by default: this project exists to control Sectors credit spend.
function readAskApproval(): boolean {
  try {
    return localStorage.getItem(ASK_APPROVAL_KEY) !== "off";
  } catch {
    return true;
  }
}

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
  const [askApproval, setAskApproval] = useState(readAskApproval);
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([]);
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

  // While a message is in flight, a tool may be parked waiting for a yes/no on a real
  // Sectors call (gateway/approvals.py) — poll for it. The session id is the chat's
  // own id, the same value sent as X-Session-Id.
  useEffect(() => {
    if (!sending || !activeId) {
      setPendingApprovals([]);
      return;
    }
    let cancelled = false;
    async function poll() {
      try {
        const pending = await listPendingApprovals(activeId as string);
        if (!cancelled) setPendingApprovals(pending);
      } catch {
        // transient — keep what's shown and retry on the next tick
      }
    }
    poll();
    const timer = setInterval(poll, APPROVAL_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sending, activeId]);

  async function handleDecision(approval: PendingApproval, decision: ApprovalDecision) {
    setPendingApprovals((prev) => prev.filter((p) => p.id !== approval.id));
    try {
      await resolveApproval(approval.id, decision);
    } catch {
      // 404 = it already timed out or was answered elsewhere; the next poll clears it
    }
  }

  function handleToggleAskApproval(next: boolean) {
    setAskApproval(next);
    try {
      localStorage.setItem(ASK_APPROVAL_KEY, next ? "on" : "off");
    } catch {
      // Private browsing / storage disabled — the choice just won't persist.
    }
  }

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
      const updated = await sendMessage(
        activeId,
        text,
        setCurrentStep,
        setStreamingText,
        controller.signal,
        askApproval
      );
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
              <div className="chat-toolbar">
                <span className="chat-toolbar-title">{active.title}</span>
                <label
                  className="switch"
                  title={sending ? "Can't change while a reply is in progress" : "Pause and ask before any Sectors call that costs credit"}
                >
                  <input
                    type="checkbox"
                    role="switch"
                    checked={askApproval}
                    onChange={(event) => handleToggleAskApproval(event.target.checked)}
                    disabled={sending}
                  />
                  <span className="switch-track" aria-hidden="true" />
                  <span>Ask before spending credits</span>
                </label>
              </div>
              {pendingApprovals.length > 0 && (
                <div className="approval-stack">
                  {pendingApprovals.map((approval) => (
                    <div key={approval.id} className="approval-card" role="alertdialog" aria-label="Approve Sectors API call">
                      <div className="approval-title">
                        Permission needed — this uses about {approval.credits} Sectors credit
                        {approval.credits === 1 ? "" : "s"}
                      </div>
                      <code className="approval-call">{approval.description}</code>
                      <p className="approval-note">
                        Nothing is fetched until you answer. No answer within {approval.timeout_seconds}s counts as
                        "no".
                      </p>
                      <div className="approval-actions">
                        <button type="button" onClick={() => handleDecision(approval, "approve")}>
                          Allow once
                        </button>
                        <button type="button" onClick={() => handleDecision(approval, "approve_session")}>
                          Allow for this chat
                        </button>
                        <button
                          type="button"
                          className="approval-deny"
                          onClick={() => handleDecision(approval, "deny")}
                        >
                          Don't fetch
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
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
                    {pendingApprovals.length > 0 ? (
                      <span className="chat-step-label">Waiting for your approval…</span>
                    ) : currentStep ? (
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
