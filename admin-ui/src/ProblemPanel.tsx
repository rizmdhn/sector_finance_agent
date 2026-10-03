import type { Problem } from "./api/problems";

// One error look for the whole app: what happened, what to do, and a way to try again.
export default function ProblemPanel({
  problem,
  onRetry,
  retryLabel = "Try again",
}: {
  problem: Problem;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="problem-card" role="alert">
      <div className="problem-title">{problem.title}</div>
      <p className="problem-detail">{problem.detail}</p>
      <div className="problem-fix">
        <span>What to do</span>
        <code>{problem.fix}</code>
      </div>
      {onRetry && (
        <button type="button" className="problem-retry" onClick={onRetry}>
          {retryLabel}
        </button>
      )}
    </div>
  );
}
