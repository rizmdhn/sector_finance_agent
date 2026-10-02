import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { listPendingApprovals, resolveApproval, type ApprovalDecision, type PendingApproval } from "../api/approvals";
import { createSession, deleteSession, listSessions, sendMessage } from "../api/chat";
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

const ASK_APPROVAL_KEY = "idx-admin-ui.ask-approval.v1";
const APPROVAL_POLL_MS = 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

// Things this system can actually answer, so the empty screen teaches what to ask.
const SUGGESTIONS = [
  "What happened inside LQ45 over the last month?",
  "Show BBCA's cash flow and balance sheet highlights",
  "What is BBCA's free float and local vs foreign ownership?",
  "How many days to exit a Rp 50B position in BBCA?",
];

// On by default: this project exists to control Sectors credit spend.
function readAskApproval(): boolean {
  try {
    return localStorage.getItem(ASK_APPROVAL_KEY) !== "off";
  } catch {
    return true;
  }
}

function lastActivity(session: ChatSession): string {
  return session.messages[session.messages.length - 1]?.createdAt ?? session.createdAt;
}

// Newest first, bucketed by last activity — the sidebar grouping Claude/LibreChat use.
function groupSessions(sessions: ChatSession[]): { label: string; items: ChatSession[] }[] {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const buckets = [
    { label: "Today", min: today.getTime(), items: [] as ChatSession[] },
    { label: "Yesterday", min: today.getTime() - DAY_MS, items: [] as ChatSession[] },
    { label: "Previous 7 days", min: today.getTime() - 7 * DAY_MS, items: [] as ChatSession[] },
    { label: "Older", min: -Infinity, items: [] as ChatSession[] },
  ];
  for (const session of [...sessions].sort((a, b) => lastActivity(b).localeCompare(lastActivity(a)))) {
    const time = new Date(lastActivity(session)).getTime();
    buckets.find((bucket) => time >= bucket.min)?.items.push(session);
  }
  return buckets.filter((bucket) => bucket.items.length > 0);
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
  const [drawerOpen, setDrawerOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      const loaded = await listSessions(userId);
      setSessions(loaded);
      // No active chat on load = the blank "new chat" screen, like Claude/LibreChat;
      // nothing is saved until the first message is sent.
      setActiveId(null);
      setLoading(false);
    }
    load();
  }, [userId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeId, sessions, streamingText, pendingApprovals.length]);

  // Grow the composer with its text (up to a cap), shrink back once it's cleared.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft]);

  // Hand the cursor back after a reply — desktop only, so a phone's keyboard doesn't
  // pop up on every session switch.
  useEffect(() => {
    if (!sending && window.matchMedia("(pointer: fine)").matches) inputRef.current?.focus();
  }, [sending, activeId]);

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

  function handleNewChat() {
    setActiveId(null);
    setDraft("");
    setSendError(null);
    setDrawerOpen(false);
  }

  function handleOpen(id: string) {
    setActiveId(id);
    setSendError(null);
    setDrawerOpen(false);
  }

  function handleDelete(id: string) {
    deleteSession(id);
    setSessions((prev) => prev.filter((session) => session.id !== id));
    if (id === activeId) setActiveId(null);
  }

  async function handleSend(event?: FormEvent) {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;
    setDraft("");
    setSendError(null);

    // The first message of a new chat is what creates the session.
    let sessionId = activeId;
    if (!sessionId) {
      const created = await createSession(userId);
      sessionId = created.id;
      setSessions((prev) => [created, ...prev]);
      setActiveId(created.id);
    }
    const sid: string = sessionId;

    // Optimistic: show the user's own message immediately — sendMessage() below
    // also persists it, but not until the network round-trip finishes, and a
    // real gateway call can take several seconds.
    const optimisticMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
    };
    setSessions((prev) =>
      prev.map((session) =>
        session.id === sid ? { ...session, messages: [...session.messages, optimisticMessage] } : session
      )
    );

    const controller = new AbortController();
    abortRef.current = controller;
    setSending(true);
    setCurrentStep(null);
    setStreamingText("");
    try {
      const updated = await sendMessage(sid, text, setCurrentStep, setStreamingText, controller.signal, askApproval);
      setSessions((prev) => prev.map((session) => (session.id === updated.id ? updated : session)));
    } catch (err) {
      // The user's message is already saved (sendMessage pushes it before the
      // network call) — reload so the bubble shows even though the reply failed.
      setSessions(await listSessions(userId));
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

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter is a newline; ignore Enter while an IME is composing.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void handleSend();
    }
  }

  const groups = groupSessions(sessions);

  return (
    <div className="page page--chat">
      <div className={`chat-shell ${drawerOpen ? "chat-shell--drawer-open" : ""}`}>
        <aside className="chat-sidebar" aria-label="Conversations">
          <button type="button" className="chat-new-btn" onClick={handleNewChat}>
            <span aria-hidden="true">+</span> New chat
          </button>
          <nav className="chat-history">
            {loading && <p className="chat-history-empty">Loading…</p>}
            {!loading && groups.length === 0 && <p className="chat-history-empty">No conversations yet.</p>}
            {groups.map((group) => (
              <section key={group.label}>
                <h2>{group.label}</h2>
                <ul>
                  {group.items.map((session) => (
                    <li
                      key={session.id}
                      className={`chat-history-item ${session.id === activeId ? "chat-history-item--active" : ""}`}
                    >
                      <button
                        type="button"
                        className="chat-history-open"
                        aria-current={session.id === activeId ? "true" : undefined}
                        onClick={() => handleOpen(session.id)}
                      >
                        {session.title}
                      </button>
                      <button
                        type="button"
                        className="chat-history-delete"
                        aria-label={`Delete conversation: ${session.title}`}
                        title="Delete"
                        onClick={() => handleDelete(session.id)}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </nav>
        </aside>
        {drawerOpen && <div className="chat-scrim" onClick={() => setDrawerOpen(false)} />}

        <section className="chat-main">
          <header className="chat-header">
            <button
              type="button"
              className="chat-menu-btn"
              aria-label="Open conversations"
              onClick={() => setDrawerOpen(true)}
            >
              ☰
            </button>
            <span className="chat-header-title">{active?.title ?? "New chat"}</span>
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
              <span className="switch-text switch-text--full">Ask before spending credits</span>
              <span className="switch-text switch-text--short">Ask first</span>
            </label>
          </header>

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
                    Nothing is fetched until you answer. No answer within {approval.timeout_seconds}s counts as "no".
                  </p>
                  <div className="approval-actions">
                    <button type="button" onClick={() => handleDecision(approval, "approve")}>
                      Allow once
                    </button>
                    <button type="button" onClick={() => handleDecision(approval, "approve_session")}>
                      Allow for this chat
                    </button>
                    <button type="button" className="approval-deny" onClick={() => handleDecision(approval, "deny")}>
                      Don't fetch
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="chat-scroll">
            {!active && (
              <div className="chat-welcome">
                <h1>What would you like to know?</h1>
                <p>Ask about an IDX company, an index, or your portfolio.</p>
                <div className="chat-suggestions">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      className="chat-suggestion"
                      onClick={() => {
                        setDraft(suggestion);
                        inputRef.current?.focus();
                      }}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {active && (
              <div className="chat-thread">
                {active.messages.map((message) =>
                  message.role === "user" ? (
                    <div key={message.id} className="msg msg--user">
                      <div className="msg-user-bubble">{message.content}</div>
                    </div>
                  ) : (
                    <div key={message.id} className="msg msg--assistant">
                      <div className="msg-avatar" aria-hidden="true">
                        IDX
                      </div>
                      <div className="msg-body">
                        <Markdown text={message.content} />
                      </div>
                    </div>
                  )
                )}
                {sending && (
                  <div className="msg msg--assistant">
                    <div className="msg-avatar" aria-hidden="true">
                      IDX
                    </div>
                    {streamingText ? (
                      <div className="msg-body msg-body--streaming">
                        <Markdown text={streamingText} />
                      </div>
                    ) : (
                      <div className="msg-body msg-thinking">
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
                  </div>
                )}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          <div className="chat-composer-wrap">
            {sendError && <p className="status-line status-line--error chat-send-error">{sendError}</p>}
            <form className="chat-composer" onSubmit={handleSend}>
              <textarea
                ref={inputRef}
                rows={1}
                placeholder="Ask about a company or index…"
                aria-label="Message"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={handleKeyDown}
                disabled={sending}
              />
              {sending ? (
                <button type="button" className="chat-send-btn chat-send-btn--stop" aria-label="Stop" onClick={handleStop}>
                  ■
                </button>
              ) : (
                <button type="submit" className="chat-send-btn" aria-label="Send" disabled={!draft.trim()}>
                  ↑
                </button>
              )}
            </form>
            <p className="chat-hint">Real model calls — real Sectors credit can be spent. Enter to send, Shift+Enter for a new line.</p>
          </div>
        </section>
      </div>
    </div>
  );
}
