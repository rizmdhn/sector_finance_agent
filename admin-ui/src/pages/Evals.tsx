import { useEffect, useState } from "react";
import { cancelEvalRun, createEvalRun, getEvalRun, listEvalRuns } from "../api/evals";
import { getModelTiering } from "../api/modelTiers";
import type { EvalClassifierResult, EvalRun, ModelInfo } from "../types";

const POLL_MS = 2000;
const ACTIVE_STATUSES = new Set(["pending", "running"]);

// Human-readable framing for evals/phoenix_evals.py's CLASSIFIERS — keyed by
// the same classifier names so a mismatch is obvious rather than silent.
const CLASSIFIER_INFO: Record<string, { title: string; blurb: string }> = {
  cites_evidence_date: {
    title: "Cites a date for its figures",
    blurb: "Every specific number should carry a fiscal year or as-of date.",
  },
  no_investment_recommendation: {
    title: "Avoids giving buy/sell advice",
    blurb: "Describes findings and lets the user decide — never tells them what to do.",
  },
  discloses_missing_data: {
    title: "Discloses missing data",
    blurb: "States plainly when something couldn't be fully assessed, instead of glossing over it.",
  },
};

function ProgressBar({ run }: { run: EvalRun }) {
  const pct = run.total > 0 ? Math.round((run.completed / run.total) * 100) : 0;
  return (
    <div className="eval-progress" title={`${run.completed} / ${run.total}`}>
      <div className="eval-progress-bar" style={{ width: `${pct}%` }} />
    </div>
  );
}

function StatusBadge({ run }: { run: EvalRun }) {
  const label = run.status === "running" && run.cancel_requested ? "cancelling…" : run.status;
  return <span className={`eval-status eval-status--${run.status}`}>{label}</span>;
}

function accuracyClass(rate: number): string {
  if (rate >= 0.9) return "eval-accuracy--good";
  if (rate >= 0.6) return "eval-accuracy--mid";
  return "eval-accuracy--bad";
}

function ClassifierRow({ name, result }: { name: string; result: EvalClassifierResult }) {
  const info = CLASSIFIER_INFO[name] ?? { title: name, blurb: "" };
  const graded = Object.entries(result.tally).reduce(
    (sum, [label, count]) => sum + (label === "not_applicable" ? 0 : count),
    0
  );
  return (
    <div className="eval-summary-row">
      <div className="eval-summary-heading">
        <span className="eval-summary-name">{info.title}</span>
        {info.blurb && <span className="eval-summary-blurb">{info.blurb}</span>}
      </div>
      {result.pass_rate === null ? (
        <span className="eval-accuracy eval-accuracy--na">not applicable to any graded answer</span>
      ) : (
        <span className={`eval-accuracy ${accuracyClass(result.pass_rate)}`}>
          {Math.round(result.pass_rate * 100)}% accurate
          <span className="eval-accuracy-detail">
            {" "}
            ({result.tally[result.pass_label] ?? 0} / {graded} answers followed this rule)
          </span>
        </span>
      )}
    </div>
  );
}

function SummaryTable({ summary }: { summary: EvalRun["summary"] }) {
  if (!summary || summary.conversations === 0) {
    return <p className="empty-note">No traced conversations were graded.</p>;
  }
  return (
    <div className="eval-summary">
      <p className="eval-summary-count">{summary.conversations} conversation(s) graded</p>
      {Object.entries(summary.classifiers).map(([name, result]) => (
        <ClassifierRow key={name} name={name} result={result} />
      ))}
    </div>
  );
}

export default function Evals({ userId }: { userId: string }) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [judgeModel, setJudgeModel] = useState("");
  const [limit, setLimit] = useState(50);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [cancellingIds, setCancellingIds] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const [tiering, evalList] = await Promise.all([getModelTiering(userId), listEvalRuns()]);
        if (cancelled) return;
        const usable = tiering.models.filter((m) => m.usable);
        setModels(usable);
        setJudgeModel((prev) => prev || usable[0]?.name || "");
        setRuns(evalList.data);
      } catch {
        if (!cancelled) setError("Could not load evals.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [userId]);

  // Poll only the runs still in flight — a run's own row updates in place, so a
  // progress bar and final summary just appear without the whole list flashing.
  useEffect(() => {
    const activeIds = runs.filter((r) => ACTIVE_STATUSES.has(r.status)).map((r) => r.id);
    if (activeIds.length === 0) return;
    const timer = setInterval(async () => {
      const updates = await Promise.all(activeIds.map((id) => getEvalRun(id).catch(() => null)));
      setRuns((prev) => {
        const byId = new Map(updates.filter((u): u is EvalRun => u !== null).map((u) => [u.id, u]));
        return prev.map((r) => byId.get(r.id) ?? r);
      });
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [runs]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!judgeModel) return;
    setSubmitting(true);
    setError(null);
    try {
      const { id } = await createEvalRun(userId, judgeModel, limit);
      const run = await getEvalRun(id);
      setRuns((prev) => [run, ...prev]);
    } catch {
      setError("Could not start eval run.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCancel(id: number) {
    setCancellingIds((prev) => new Set(prev).add(id));
    try {
      await cancelEvalRun(id);
      const run = await getEvalRun(id);
      setRuns((prev) => prev.map((r) => (r.id === id ? run : r)));
    } catch {
      setError("Could not cancel that run.");
    } finally {
      setCancellingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>Evals</h1>
        <p className="subtitle">
          Run the compliance LLM-as-judge eval (evals/phoenix_evals.py) over recent real conversations. Pick the
          judge model, start it, and check back later — it keeps running in the background.
        </p>
      </header>

      {loading && <p className="status-line">Loading…</p>}
      {error && <p className="status-line status-line--error">{error}</p>}

      {!loading && (
        <>
          <form className="eval-form" onSubmit={handleSubmit}>
            <label>
              Judge model
              <select value={judgeModel} onChange={(e) => setJudgeModel(e.target.value)}>
                {models.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name} ({m.provider})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Conversations to grade
              <input
                type="number"
                min={1}
                max={500}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value) || 1)}
              />
            </label>
            <button type="submit" disabled={submitting || !judgeModel}>
              {submitting ? "Starting…" : "Run eval"}
            </button>
          </form>

          <div className="eval-run-list">
            {runs.length === 0 && <p className="empty-note">No eval runs yet.</p>}
            {runs.map((run) => {
              const active = ACTIVE_STATUSES.has(run.status);
              return (
                <article key={run.id} className="eval-run-card">
                  <div className="eval-run-head">
                    <span className="eval-run-title">
                      Run #{run.id} — {run.judge_model}
                    </span>
                    <div className="eval-run-head-right">
                      <StatusBadge run={run} />
                      {active && !run.cancel_requested && (
                        <button
                          type="button"
                          className="eval-cancel-btn"
                          onClick={() => handleCancel(run.id)}
                          disabled={cancellingIds.has(run.id)}
                        >
                          Cancel
                        </button>
                      )}
                    </div>
                  </div>
                  <p className="eval-run-meta">
                    started by {run.user_id} · {new Date(run.created_at).toLocaleString()}
                  </p>
                  {active && (
                    <>
                      <ProgressBar run={run} />
                      <p className="eval-run-meta">
                        {run.completed} / {run.total || "…"}
                      </p>
                    </>
                  )}
                  {(run.status === "done" || run.status === "cancelled") && <SummaryTable summary={run.summary} />}
                  {run.status === "error" && <p className="status-line status-line--error">{run.error}</p>}
                </article>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
