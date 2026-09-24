// Real backend — gateway/main.py's /v1/auth/* endpoints, session-cookie based
// (httpOnly, set/cleared by the gateway itself — no token for this code to
// handle directly). See that module's login()/verify_session() docstrings for
// why this exists: without it, admin-ui had no login at all.

import { apiFetch, ApiError } from "./client";

export async function login(password: string): Promise<void> {
  await apiFetch<{ ok: boolean }>("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export async function logout(): Promise<void> {
  await apiFetch<{ ok: boolean }>("/v1/auth/logout", { method: "POST" });
}

export async function checkSession(): Promise<boolean> {
  try {
    await apiFetch<{ ok: boolean }>("/v1/auth/verify");
    return true;
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return false;
    throw err;
  }
}
