import { useEffect, useState } from "react";
import { ROLES } from "./api/modelTiers";
import { fetchTraceWhenReady, type TraceSummary } from "./api/traces";

const AGENT_LABELS: Record<string, string> = Object.fromEntries(ROLES.map((role) => [role.id, role.label]));

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

// Right-hand drawer: what happened inside one reply — agents consulted, tools called,
// model calls, and the Sectors credits spent.
export default function TracePanel({ traceId, onClose }: { traceId: string; onClose: () => void }) {
  const [trace, setTrace] = useState<TraceSummary | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    setTrace(undefined);
    fetchTraceWhenReady(traceId).then((result) => !cancelled && setTrace(result));
    return () => {
      cancelled = true;
    };
  }, [traceId]);

  return (
    <aside className="trace-panel" aria-label="Reply trace">
      <header className="trace-header">
        <span>How this answer was made</span>
        <button type="button" className="trace-close" aria-label="Close trace" onClick={onClose}>
          ×
        </button>
      </header>
      {trace === undefined && <p className="trace-note">Loading trace… it can take a few seconds to arrive.</p>}
      {trace === null && <p className="trace-note">Trace not available yet. Is Phoenix running? Close and reopen to retry.</p>}
      {trace && (
        <>
          <dl className="trace-totals">
            <div>
              <dt>Credits</dt>
              <dd>{trace.credits}</dd>
            </div>
            <div>
              <dt>Time</dt>
              <dd>{seconds(trace.duration_ms)}</dd>
            </div>
            <div>
              <dt>Tokens</dt>
              <dd>{trace.tokens.toLocaleString()}</dd>
            </div>
          </dl>
          <ol className="trace-steps">
            {trace.steps.map((step, index) => (
              <li
                key={index}
                className={`trace-step trace-step--${step.kind.toLowerCase()} ${step.error ? "trace-step--error" : ""}`}
                style={{ paddingLeft: step.depth * 16 }}
              >
                <span className="trace-kind">{step.kind === "LLM" ? "Model" : step.kind === "AGENT" ? "Agent" : "Tool"}</span>
                <span className="trace-name">{AGENT_LABELS[step.name] ?? step.name}</span>
                <span className="trace-meta">
                  {step.credits > 0 && <b>{step.credits} cr · </b>}
                  {step.tokens > 0 && `${step.tokens.toLocaleString()} tok · `}
                  {seconds(step.duration_ms)}
                </span>
              </li>
            ))}
          </ol>
          {trace.credits === 0 && <p className="trace-note">No Sectors credits spent — everything came from cache or stored data.</p>}
          {trace.phoenix_url && (
            <a className="trace-link" href={trace.phoenix_url} target="_blank" rel="noreferrer">
              Open in Phoenix
            </a>
          )}
        </>
      )}
    </aside>
  );
}
