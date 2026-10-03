import { useEffect, useRef, useState } from "react";
import Chat from "./pages/Chat";
import Evals from "./pages/Evals";
import Login from "./pages/Login";
import Memory from "./pages/Memory";
import ModelTiering from "./pages/ModelTiering";
import { checkSession, logout } from "./api/auth";
import { GATEWAY_DOWN } from "./api/problems";
import { getReadiness, type ReadinessState } from "./api/readiness";
import ProblemPanel from "./ProblemPanel";
import "./styles.css";

const READINESS_POLL_MS = 5000;
// One missed poll is a blip; this many in a row means the gateway is really not answering.
const GATEWAY_DOWN_AFTER_FAILURES = 2;

// price_data_ready is intentionally excluded here — gateway/main.py's
// get_readiness() doesn't require it for `ready` either. Every price-dependent
// tool already degrades to UNAVAILABLE gracefully when price_daily is empty
// (same as the project's usual missing-data handling), so it's shown as a
// background-loading banner in the main shell instead of a blocking gate.
const BLOCKING_READINESS_LABEL: Record<"symbol_master_ready" | "model_key_ready", string> = {
  symbol_master_ready: "Ticker list",
  model_key_ready: "Model API key",
};

type Tab = "chat" | "memory" | "model-tiering" | "evals";

const TABS: { id: Tab; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "memory", label: "Memory" },
  { id: "model-tiering", label: "Model Tiering" },
  { id: "evals", label: "Evals" },
];

const THEME_KEY = "idx-admin-ui.theme.v1";
type Theme = "light" | "dark";

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function readThemeChoice(): Theme | null {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    return stored === "light" || stored === "dark" ? stored : null;
  } catch {
    return null;
  }
}

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
  // null = never chosen, follow the OS; the choice is applied to <html> by index.html on
  // load and here on change.
  const [themeChoice, setThemeChoice] = useState<Theme | null>(readThemeChoice);
  const theme: Theme = themeChoice ?? systemTheme();
  const [readiness, setReadiness] = useState<ReadinessState | null>(null);
  const [gatewayDown, setGatewayDown] = useState(false);
  const pollFailures = useRef(0);
  const [tab, setTab] = useState<Tab>(readTab);
  useEffect(() => {
    location.hash = tab;
  }, [tab]);
  const [userId, setUserIdState] = useState(readUserId);
  const [userDraft, setUserDraft] = useState(userId);

  function verifySession() {
    checkSession()
      .then((ok) => {
        setGatewayDown(false);
        setAuthed(ok);
      })
      .catch(() => setGatewayDown(true));
  }

  useEffect(verifySession, []);

  // Poll until the fresh-deploy data seed (ingest/scheduler.py) has landed,
  // rather than letting the user into a Chat screen where every ticker looks
  // "unknown" — gated behind login since readiness itself requires auth.
  useEffect(() => {
    if (!authed) return;
    let cancelled = false;
    async function poll() {
      try {
        const state = await getReadiness();
        pollFailures.current = 0;
        if (!cancelled) {
          setReadiness(state);
          setGatewayDown(false);
        }
      } catch {
        // A single failure is transient — keep the last state and retry on the next
        // tick; several in a row means the gateway is down, say so.
        pollFailures.current += 1;
        if (!cancelled && pollFailures.current >= GATEWAY_DOWN_AFTER_FAILURES) setGatewayDown(true);
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

  function handleToggleTheme() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setThemeChoice(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // Private browsing / storage disabled — the choice just won't persist.
    }
  }

  async function handleLogout() {
    await logout();
    setAuthed(false);
  }

  if (gatewayDown) {
    return (
      <div className="setup-screen">
        <div className="setup-card">
          <ProblemPanel problem={GATEWAY_DOWN} retryLabel="Check again" onRetry={verifySession} />
        </div>
      </div>
    );
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
            First-time setup loads the ticker list in the background. This page will update automatically — no need
            to refresh.
          </p>
          <ul className="setup-checklist">
            {(Object.keys(BLOCKING_READINESS_LABEL) as (keyof typeof BLOCKING_READINESS_LABEL)[]).map((key) => {
              const done = readiness?.[key] ?? false;
              return (
                <li key={key} className={done ? "setup-item setup-item--done" : "setup-item"}>
                  <span className="setup-item-dot" />
                  {BLOCKING_READINESS_LABEL[key]}
                </li>
              );
            })}
          </ul>
          {readiness?.ingest_note && <p className="setup-note">{readiness.ingest_note}</p>}
          {readiness?.problems
            .filter((problem) => problem.blocking)
            .map((problem) => <ProblemPanel key={problem.code} problem={problem} />)}
        </div>
      </div>
    );
  }

  return (
    <div className="shell">
      {readiness.problems
        .filter((problem) => !problem.blocking)
        .map((problem) => (
          <div key={problem.code} className="problem-banner" role="alert">
            <b>{problem.title}.</b> {problem.fix}
          </div>
        ))}
      {!readiness.price_data_ready && (
        <div className="price-data-banner">
          Recent price history hasn't landed yet — it's pulled once a day after market close (~16:00-19:00 WIB), not
          eagerly on startup, to keep Sectors API credit usage minimal. Portfolio, liquidity and returns figures will
          show as unavailable until then; everything else (company reports, ownership, fundamentals) works now.
        </div>
      )}
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
        <button
          type="button"
          className="logout-btn theme-btn"
          onClick={handleToggleTheme}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
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
