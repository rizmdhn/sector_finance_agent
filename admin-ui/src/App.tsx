import { useEffect, useState } from "react";
import Chat from "./pages/Chat";
import Evals from "./pages/Evals";
import Login from "./pages/Login";
import Memory from "./pages/Memory";
import ModelTiering from "./pages/ModelTiering";
import { checkSession, logout } from "./api/auth";
import { getReadiness, type ReadinessState } from "./api/readiness";
import "./styles.css";

const READINESS_POLL_MS = 5000;

const READINESS_LABEL: Record<keyof Omit<ReadinessState, "ready">, string> = {
  symbol_master_ready: "Ticker list",
  price_data_ready: "Price history",
  model_key_ready: "Model API key",
};

type Tab = "chat" | "memory" | "model-tiering" | "evals";

const TABS: { id: Tab; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "memory", label: "Memory" },
  { id: "model-tiering", label: "Model Tiering" },
  { id: "evals", label: "Evals" },
];

const USER_ID_KEY = "idx-admin-ui.user-id.v1";
const DEFAULT_USER_ID = "demo-user";

function readTab(): Tab {
  const hash = location.hash.slice(1);
  return TABS.some((t) => t.id === hash) ? (hash as Tab) : "chat";
}

function readUserId(): string {
  try {
    return localStorage.getItem(USER_ID_KEY) || DEFAULT_USER_ID;
  } catch {
    return DEFAULT_USER_ID;
  }
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [readiness, setReadiness] = useState<ReadinessState | null>(null);
  const [tab, setTab] = useState<Tab>(readTab);
  useEffect(() => {
    location.hash = tab;
  }, [tab]);
  const [userId, setUserIdState] = useState(readUserId);
  const [userDraft, setUserDraft] = useState(userId);

  useEffect(() => {
    checkSession().then(setAuthed);
  }, []);

  // Poll until the fresh-deploy data seed (ingest/scheduler.py) has landed,
  // rather than letting the user into a Chat screen where every ticker looks
  // "unknown" — gated behind login since readiness itself requires auth.
  useEffect(() => {
    if (!authed) return;
    let cancelled = false;
    async function poll() {
      try {
        const state = await getReadiness();
        if (!cancelled) setReadiness(state);
      } catch {
        // transient — keep the last known state and retry on the next tick
      }
    }
    poll();
    const timer = setInterval(poll, READINESS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [authed]);

  function setUserId(next: string) {
    const trimmed = next.trim() || DEFAULT_USER_ID;
    setUserIdState(trimmed);
    try {
      localStorage.setItem(USER_ID_KEY, trimmed);
    } catch {
      // Private browsing / storage disabled — the choice just won't persist.
    }
  }

  async function handleLogout() {
    await logout();
    setAuthed(false);
  }

  if (authed === null) {
    return <div className="auth-loading">Loading…</div>;
  }

  if (!authed) {
    return <Login onSuccess={() => setAuthed(true)} />;
  }

  if (!readiness || !readiness.ready) {
    return (
      <div className="setup-screen">
        <div className="setup-card">
          <h1>Setting up your data…</h1>
          <p>
            First-time setup loads the ticker list and recent prices in the background. This page will update
            automatically — no need to refresh.
          </p>
          <ul className="setup-checklist">
            {(Object.keys(READINESS_LABEL) as (keyof typeof READINESS_LABEL)[]).map((key) => {
              const done = readiness?.[key] ?? false;
              return (
                <li key={key} className={done ? "setup-item setup-item--done" : "setup-item"}>
                  <span className="setup-item-dot" />
                  {READINESS_LABEL[key]}
                  {key === "model_key_ready" && !done && (
                    <span className="setup-item-note"> — add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env</span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    );
  }

  return (
    <div className="shell">
      <nav className="top-nav">
        <div className="top-nav-brand">IDX Portfolio Intelligence</div>
        <div className="top-nav-tabs">
          {TABS.map((entry) => (
            <button
              key={entry.id}
              className={`top-nav-tab ${tab === entry.id ? "top-nav-tab--active" : ""}`}
              onClick={() => setTab(entry.id)}
            >
              {entry.label}
            </button>
          ))}
        </div>
        <form
          className="user-switcher"
          onSubmit={(event) => {
            event.preventDefault();
            setUserId(userDraft);
          }}
        >
          <label htmlFor="user-id-input">User</label>
          <input
            id="user-id-input"
            value={userDraft}
            onChange={(event) => setUserDraft(event.target.value)}
            onBlur={() => setUserId(userDraft)}
          />
        </form>
        <button className="logout-btn" onClick={handleLogout}>
          Sign out
        </button>
      </nav>

      <main className="shell-main">
        {tab === "chat" && <Chat userId={userId} />}
        {tab === "memory" && <Memory userId={userId} />}
        {tab === "model-tiering" && <ModelTiering userId={userId} />}
        {tab === "evals" && <Evals userId={userId} />}
      </main>
    </div>
  );
}
