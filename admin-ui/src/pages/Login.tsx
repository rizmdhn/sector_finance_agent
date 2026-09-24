import { useState, type FormEvent } from "react";
import { login } from "../api/auth";
import { ApiError } from "../api/client";

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(password);
      onSuccess();
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError("Too many attempts — wait a minute and try again.");
      } else {
        setError("Wrong password.");
      }
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="login-mark" aria-hidden="true">
          IDX
        </div>
        <h1>Portfolio Intelligence</h1>
        <p className="subtitle">Sign in to view chat, memory, and model tiering.</p>
        <input
          type="password"
          className="login-input"
          placeholder="Password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoFocus
        />
        <button className="login-submit" type="submit" disabled={submitting || !password}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
        {error && <p className="status-line status-line--error">{error}</p>}
      </form>
    </div>
  );
}
