// What went wrong and what to do about it — the gateway's data/problems.py shape, plus
// the cases only the browser can see (gateway down, session expired).

export interface Problem {
  code: string;
  title: string;
  detail: string;
  fix: string;
  blocking?: boolean;
}

export class ProblemError extends Error {
  problem: Problem;
  constructor(problem: Problem) {
    super(problem.title);
    this.problem = problem;
  }
}

export const GATEWAY_DOWN: Problem = {
  code: "gateway_unreachable",
  title: "Can't reach the gateway",
  detail: "The admin UI couldn't get an answer from the agent gateway. It may be stopped, restarting, or crashed on startup.",
  fix: "Check it is running: docker compose ps agent-gateway — and read why it stopped: docker compose logs agent-gateway",
};

// A failed gateway response -> a Problem. The gateway sends {"error": Problem} for chat
// failures and FastAPI's {"detail": "..."} for plain ones; anything else (nginx's own 502
// HTML page when the gateway is down) means the gateway itself isn't answering.
export function problemFromResponse(status: number, body: string): Problem {
  try {
    const parsed = JSON.parse(body);
    if (parsed?.error?.code) return parsed.error as Problem;
    if (typeof parsed?.detail === "string") {
      return status === 429
        ? { code: "rate_limited", title: "Too many messages", detail: parsed.detail, fix: "Wait a moment and try again." }
        : { code: `http_${status}`, title: "The request was rejected", detail: parsed.detail, fix: "Try again. If it repeats, check: docker compose logs agent-gateway" };
    }
  } catch {
    // not JSON
  }
  return status >= 502 && status <= 504
    ? GATEWAY_DOWN
    : { code: `http_${status}`, title: "Something went wrong", detail: body.slice(0, 200) || `HTTP ${status}`, fix: "Try again. If it repeats, check: docker compose logs agent-gateway" };
}
