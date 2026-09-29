// Real backend — gateway/main.py's /v1/admin/evals endpoints, backed by
// Postgres (data/schema.sql's eval_runs). A run keeps going server-side
// (BackgroundTask, gateway/eval_runner.py) even if the tab that started it
// closes — src/pages/Evals.tsx polls GET .../evals/{id} for progress.

import { apiFetch } from "./client";
import type { EvalRun } from "../types";

export function listEvalRuns(): Promise<{ data: EvalRun[] }> {
  return apiFetch<{ data: EvalRun[] }>("/v1/admin/evals");
}

export function getEvalRun(id: number): Promise<EvalRun> {
  return apiFetch<EvalRun>(`/v1/admin/evals/${id}`);
}

export function createEvalRun(userId: string, model: string, limit: number): Promise<{ id: number }> {
  return apiFetch<{ id: number }>("/v1/admin/evals", {
    method: "POST",
    body: JSON.stringify({ user: userId, model, limit }),
  });
}

export function cancelEvalRun(id: number): Promise<{ id: number; cancel_requested: boolean }> {
  return apiFetch<{ id: number; cancel_requested: boolean }>(`/v1/admin/evals/${id}/cancel`, { method: "POST" });
}
