import { useEffect, useState } from "react";
import Chat from "./pages/Chat";
import Login from "./pages/Login";
import Memory from "./pages/Memory";
import ModelTiering from "./pages/ModelTiering";
import { checkSession, logout } from "./api/auth";
import "./styles.css";

type Tab = "chat" | "memory" | "model-tiering";

const TABS: { id: Tab; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "memory", label: "Memory" },
  { id: "model-tiering", label: "Model Tiering" },
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
  const [tab, setTab] = useState<Tab>(readTab);
  useEffect(() => {
    location.hash = tab;
  }, [tab]);
  const [userId, setUserIdState] = useState(readUserId);
  const [userDraft, setUserDraft] = useState(userId);

  useEffect(() => {
    checkSession().then(setAuthed);
  }, []);

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
      </main>
    </div>
  );
}
