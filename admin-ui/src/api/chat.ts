// Real backend — gateway/main.py's /v1/chat/completions (OpenAI-compatible SSE
// stream). Sessions themselves are still a local index (localStorage,
// `ChatSession[]`) — there's no "list my sessions" endpoint on the gateway, only
// a session's message history lives server-side (Valkey, via
// gateway/roles/orchestrator.py's session_manager). Each local session's `id`
// doubles as the `X-Session-Id` header: gateway/main.py treats an id it hasn't
// seen as a new session (seeds it with the full message array we send) and one
// it has seen as continuing (trims server-side to just the newest turn) — so the
// client never needs to branch on "is this a new session", just always send the
// full local transcript and the same id every time.
//
// Streaming (not stream:false) specifically so step-tracing works: gateway/
// main.py's _stream_response emits a non-standard `delta.step` field — which
// top-level specialist/tool the Chief is currently calling — interleaved with
// the normal `delta.content` text chunks. Confirmed live (see PROGRESS.md): a
// real question showed one `step` chunk ~7s before any content arrived, exactly
// the gap the old non-streaming request left blank.

import { ApiError } from "./client";
import type { ChatMessage, ChatSession } from "../types";

const STORAGE_KEY = "idx-admin-ui.chat-sessions.v1";
const SESSION_HEADER = "X-Session-Id";
// Cheap tier by default — Model Tiering (per user, see PROGRESS.md item 38)
// still applies on top of this via each role's own Postgres override; this is
// only the fallback model, same as admin-ui's own tiering defaults.
const CHAT_MODEL = "idx-analyst-claude";

interface CompletionChunk {
  choices: { delta: { content?: string; step?: string } }[];
}

function loadSessions(): ChatSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as ChatSession[];
  } catch {
    return [];
  }
}

function saveSessions(sessions: ChatSession[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  } catch {
    // Private browsing / storage disabled — the session list just won't persist.
  }
}

export async function listSessions(userId: string): Promise<ChatSession[]> {
  return loadSessions()
    .filter((s) => s.userId === userId)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export async function createSession(userId: string): Promise<ChatSession> {
  const sessions = loadSessions();
  const session: ChatSession = {
    id: crypto.randomUUID(),
    userId,
    title: "New session",
    createdAt: new Date().toISOString(),
    messages: [],
  };
  sessions.push(session);
  saveSessions(sessions);
  return session;
}

export async function sendMessage(
  sessionId: string,
  content: string,
  onStep?: (step: string) => void,
  onDelta?: (text: string) => void
): Promise<ChatSession> {
  const sessions = loadSessions();
  const session = sessions.find((s) => s.id === sessionId);
  if (!session) throw new Error(`unknown session: ${sessionId}`);

  const userMessage: ChatMessage = {
    id: crypto.randomUUID(),
    role: "user",
    content,
    createdAt: new Date().toISOString(),
  };
  session.messages.push(userMessage);
  if (session.title === "New session") {
    session.title = content.slice(0, 40) + (content.length > 40 ? "…" : "");
  }
  saveSessions(sessions);

  // Errors (rate limit, auth, a genuine model/tool failure) are re-thrown as-is
  // after this point — the user's own message stays saved either way, it was
  // real and already sent; only the assistant reply is missing on failure.
  const response = await fetch("/api/v1/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json", [SESSION_HEADER]: sessionId },
    body: JSON.stringify({
      model: CHAT_MODEL,
      stream: true,
      user: session.userId,
      messages: session.messages.map((m) => ({ role: m.role, content: m.content })),
    }),
  });
  if (!response.ok || !response.body) {
    const body = await response.text().catch(() => "");
    throw new ApiError(response.status, body || response.statusText);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let text = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const line = event.trim();
      if (!line.startsWith("data: ")) continue;
      const raw = line.slice("data: ".length);
      if (raw === "[DONE]") continue;
      const chunk = JSON.parse(raw) as CompletionChunk;
      const delta = chunk.choices[0]?.delta ?? {};
      if (delta.step) onStep?.(delta.step);
      if (delta.content) {
        text += delta.content;
        onDelta?.(text);
      }
    }
  }

  const assistantMessage: ChatMessage = {
    id: crypto.randomUUID(),
    role: "assistant",
    content: text || "(empty response)",
    createdAt: new Date().toISOString(),
  };
  session.messages.push(assistantMessage);
  saveSessions(sessions);

  return { ...session };
}
