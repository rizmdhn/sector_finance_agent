# admin-ui

This project's own user-facing panel in front of the agent gateway — not just an
admin tool. Vite + React + TypeScript, behind a real login, three screens:

## Auth

Single admin password (`ADMIN_PASSWORD` in `.env`), session-cookie based —
`gateway/main.py`'s `/v1/auth/login`/`logout`/`verify`. nginx's `auth_request`
gates the entire `/api/` proxy on a valid session before anything reaches the
gateway (`nginx.conf.template`) — not just a client-side route guard, which
alone would leave the API directly reachable. Rate-limited at 5 attempts/min via
the same limiter `/v1/chat/completions` already uses. One operator, not a users
table — see `gateway/main.py`'s `login()` docstring and PROGRESS.md item 31 for
why, and what a real multi-user version would need instead.

- **Chat** — session-style conversation UI. **Mock** (user's explicit choice,
  wired to the real gateway later) — see `src/api/chat.ts`.
- **Memory** — real. Lists, adds, and deletes a user's long-term memory facts via
  `gateway/main.py`'s `/v1/memory` endpoints (backed by Postgres' `user_memory`
  table) — the same store the Chief's `search_memory`/`add_memory` tools read and
  write mid-conversation. See `src/api/memory.ts`.
- **Model Tiering** — real UI, mock config. Assign each of the 5 agent roles a
  cost tier and see which real model it resolves to. Backed by a local mock
  (`src/api/modelTiers.ts`) since there's no admin API for `ROLE_TIERS` yet — see
  that file's header comment.

## Run it

**Docker (bundled with the rest of the stack):**

```bash
docker compose up -d
```

Serves on `http://localhost:5173`. nginx (`Dockerfile` + `nginx.conf.template`)
serves the built static app and reverse-proxies `/api/` to the `agent-gateway`
container, injecting the bearer key server-side from `$IDX_GATEWAY_KEY` — the
browser never sees the key and there's no CORS to configure (same-origin from
its perspective). Verified end-to-end (`docker compose build && up`): add/list/
delete against real Postgres through the full container chain, and the proxy
self-heals (via nginx's dynamic DNS resolution, `resolver 127.0.0.11`) if
`agent-gateway` gets recreated with a new container IP without restarting
`admin-ui`.

**Local dev (hot reload):**

```bash
cp .env.example .env   # set IDX_GATEWAY_KEY to match the gateway you're running
npm install
npm run dev
```

`vite.config.ts`'s dev proxy does the same job as nginx does in Docker — forwards
`/api` to `GATEWAY_URL` (default `http://localhost:8000`) with the bearer key
injected, so the app behaves identically in dev and in the container.

## Scripts

- `npm run dev` — local dev server with HMR
- `npm run build` — typecheck (`tsc -b`) + production build
- `npm run lint` — oxlint

## Keeping things in sync

`ROLES`/`MODELS` in `src/api/modelTiers.ts` mirror
`gateway/roles/orchestrator.py`'s `ROLE_TIERS` keys and `models.yaml`'s entries
by hand — there's no shared schema yet. Update both sides together, or better,
replace the mock with a real endpoint that serves the registry directly.
