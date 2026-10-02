import { useEffect, useState } from "react";
import { ROLES } from "./api/modelTiers";
import { fetchTraceWhenReady, type TraceStep, type TraceSummary } from "./api/traces";

const AGENT_LABELS: Record<string, string> = Object.fromEntries(ROLES.map((role) => [role.id, role.label]));

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

interface Node {
  step: TraceStep;
  count: number;
  children: Node[];
}

// The flat, depth-tagged step list -> a tree. Repeated leaf calls under the same parent
// (e.g. 12x get_price_history) collapse into one "x12" row so the list stays short.
function buildTree(steps: TraceStep[]): Node[] {
  const roots: Node[] = [];
  const stack: Node[] = [];
  for (const step of steps) {
    const node: Node = { step: { ...step }, count: 1, children: [] };
    stack.length = step.depth;
    (stack[step.depth - 1]?.children ?? roots).push(node);
    stack[step.depth] = node;
  }
  return mergeRepeats(roots);
}

function mergeRepeats(nodes: Node[]): Node[] {
  const merged: Node[] = [];
  for (const node of nodes) {
    node.children = mergeRepeats(node.children);
    const twin =
      node.children.length === 0 &&
      merged.find((m) => m.children.length === 0 && m.step.kind === node.step.kind && m.step.name === node.step.name);
    if (twin) {
      twin.count += 1;
      twin.step.duration_ms += node.step.duration_ms;
      twin.step.credits += node.step.credits;
      twin.step.tokens += node.step.tokens;
      twin.step.error ||= node.step.error;
    } else {
      merged.push(node);
    }
  }
  return merged;
}

function subtotal(node: Node): { credits: number; tokens: number } {
  return node.children.reduce(
    (sum, child) => {
      const inner = subtotal(child);
      return { credits: sum.credits + inner.credits, tokens: sum.tokens + inner.tokens };
    },
    { credits: node.step.credits, tokens: node.step.tokens }
  );
}

function StepRow({ node }: { node: Node }) {
  const { step, count } = node;
  const { credits, tokens } = subtotal(node);
  const kind = step.kind === "LLM" ? "Model" : step.kind === "AGENT" ? "Agent" : "Tool";
  const row = (
    <>
      <span className="trace-kind">{kind}</span>
      <span className="trace-name">
        {AGENT_LABELS[step.name] ?? step.name}
        {count > 1 && <span className="trace-count"> ×{count}</span>}
      </span>
      <span className="trace-meta">
        {credits > 0 && <b>{credits} cr · </b>}
        {tokens > 0 && `${tokens.toLocaleString()} tok · `}
        {seconds(step.duration_ms)}
      </span>
    </>
  );
  const cls = `trace-step trace-step--${step.kind.toLowerCase()} ${step.error ? "trace-step--error" : ""}`;
  if (node.children.length === 0) return <li className={cls}>{row}</li>;
  return (
    <li>
      <details className="trace-group">
        <summary className={cls}>{row}</summary>
        <ol className="trace-steps">
          {node.children.map((child, index) => (
            <StepRow key={index} node={child} />
          ))}
        </ol>
      </details>
    </li>
  );
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
            {buildTree(trace.steps).map((node, index) => (
              <StepRow key={index} node={node} />
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
