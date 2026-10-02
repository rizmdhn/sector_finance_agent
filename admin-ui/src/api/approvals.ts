// Real backend — gateway/main.py's /v1/admin/approvals. While a chat message is in
// flight, a tool that is about to make a REAL Sectors call (a cache miss, so it
// spends credit) waits server-side for a yes/no; the Chat screen polls for those
// here and posts the answer. The decision is stored server-side and only this
// authenticated call can set it — the model can't approve its own call.

import { apiFetch } from "./client";

export interface PendingApproval {
  id: string;
  description: string;
  credits: number;
  waiting_seconds: number;
  timeout_seconds: number;
}

export type ApprovalDecision = "approve" | "approve_session" | "deny";

export async function listPendingApprovals(sessionId: string): Promise<PendingApproval[]> {
  const result = await apiFetch<{ pending: PendingApproval[] }>(
    `/v1/admin/approvals?session=${encodeURIComponent(sessionId)}`
  );
  return result.pending;
}

export async function resolveApproval(id: string, decision: ApprovalDecision): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/v1/admin/approvals/${id}`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });
}
