// Real HTTP client for the gateway's OpenAI-compatible API (gateway/main.py).
//
// Requests go to `/api/...`, not the gateway directly — same-origin, so the
// browser needs no CORS handling and never needs to know the gateway's bearer
// key. In production (docker-compose) nginx (admin-ui/Dockerfile,
// admin-ui/nginx.conf.template) reverse-proxies `/api/` to the agent-gateway
// container and injects `Authorization` itself from `$IDX_GATEWAY_KEY`. In dev
// (`npm run dev`) vite.config.ts's server.proxy does the same against a gateway
// running on localhost:8000 — see admin-ui/.env.example for the dev-only env var
// that needs.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new ApiError(response.status, body || response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
