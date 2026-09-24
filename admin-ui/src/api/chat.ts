// TEMPORARY mock — the user explicitly chose to build the chat screen against
// mock data first and wire it to the real gateway (/v1/chat/completions) later,
// same as src/api/modelTiers.ts. Nothing here calls the gateway.
//
// The one thing this mock is trying to demonstrate honestly is *session shape*:
// multiple named sessions, each with its own persisted message history, switching
// between them without losing anything — mirroring what
// gateway/roles/orchestrator.py's RepositorySessionManager + ValkeySessionRepository
// actually do server-side (a session id groups a conversation's history; a
// continuing session only sends the newest turn, not the whole array again — see
// gateway/main.py). This mock reproduces the *user-visible effect* of that
// (history persists per session across a reload) using localStorage, not the
// mechanism itself.
//
// The assistant "reply" is a fixed canned response, not a real model call — no
// attempt is made to fake real analysis. It exists so the chat UI has something
// to render, not to demo answer quality.
//
// Sessions are scoped by userId (stamped on create, filtered in listSessions) so
// switching the "User" field in the nav actually shows a different person's
// sessions — mirroring Memory's real per-user scoping (gateway/main.py's
// /v1/memory?user=), even though this store itself is still local/mock.
// sendMessage doesn't filter by user: a session id is already a UUID, unique
// enough on its own that a second user-id check would just be redundant.

import type { ChatMessage, ChatSession } from "../types";

const STORAGE_KEY = "idx-admin-ui.chat-sessions.v1";
const MOCK_REPLY_DELAY_MS = 600;

const MOCK_REPLY =
  "(mock reply — this screen isn't wired to the real gateway yet, see src/api/chat.ts). " +
  "In the real system this would come from the Chief Portfolio Intelligence Orchestrator, " +
  "which may delegate to a specialist and check memory for anything saved about you first.";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
    // Private browsing / storage disabled — mock just won't persist.
  }
}

export async function listSessions(userId: string): Promise<ChatSession[]> {
  await sleep(150);
  return loadSessions()
    .filter((s) => s.userId === userId)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export async function createSession(userId: string): Promise<ChatSession> {
  await sleep(150);
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

export async function sendMessage(sessionId: string, content: string): Promise<ChatSession> {
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

  await sleep(MOCK_REPLY_DELAY_MS);

  const assistantMessage: ChatMessage = {
    id: crypto.randomUUID(),
    role: "assistant",
    content: MOCK_REPLY,
    createdAt: new Date().toISOString(),
  };
  session.messages.push(assistantMessage);
  saveSessions(sessions);

  return { ...session };
}
