"""Background LLM-as-judge eval runs, triggered from admin-ui's Evals page.

Reuses evals/phoenix_evals.py's own compliance classifiers and trace-loading
(imported, not re-defined) so this can't drift from the CLI version of the same
eval. The only new thing here is running it as a FastAPI BackgroundTask with
per-conversation progress written to Postgres (data/schema.sql's eval_runs),
instead of evaluate_dataframe's one-shot batch call, so admin-ui can poll a
progress bar and the run survives the requesting tab being closed.
"""

import logging
import os

from data.db import Database
from evals.phoenix_evals import CLASSIFIERS, DEFAULT_PROJECT, DEFAULT_PHOENIX_URL, _load_conversations
from phoenix.evals import LLM, create_classifier

logger = logging.getLogger(__name__)


def _pass_rate(tally: dict[str, int], pass_label: str) -> float | None:
    """Fraction of graded (non not_applicable) conversations that hit the rule's
    pass_label — the number admin-ui shows as that classifier's "accuracy". None
    when every graded conversation came back not_applicable (nothing to score)."""
    graded = sum(count for label, count in tally.items() if label != "not_applicable")
    if graded == 0:
        return None
    return tally.get(pass_label, 0) / graded


def run_eval(db: Database, run_id: int, judge_provider: str, judge_model: str, limit: int) -> None:
    try:
        phoenix_url = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", DEFAULT_PHOENIX_URL)
        client, df = _load_conversations(phoenix_url, DEFAULT_PROJECT, limit)
        if df.empty:
            db.finish_eval_run(run_id, {"conversations": 0, "classifiers": {}})
            return

        db.start_eval_run(run_id, total=len(df))
        judge = LLM(provider=judge_provider, model=judge_model)
        evaluators = {
            name: create_classifier(name=name, llm=judge, prompt_template=cfg["prompt_template"], choices=cfg["choices"])
            for name, cfg in CLASSIFIERS.items()
        }

        tallies = {name: {} for name in CLASSIFIERS}
        annotations = []
        completed = 0
        cancelled = False
        for _, row in df.iterrows():
            for name, evaluator in evaluators.items():
                for score in evaluator.evaluate({"output": row["output"]}):
                    tallies[name][score.label] = tallies[name].get(score.label, 0) + 1
                    annotations.append(
                        {
                            "name": name,
                            "annotator_kind": "LLM",
                            "span_id": row["context.span_id"],
                            "result": {"label": score.label, "score": score.score, "explanation": score.explanation},
                        }
                    )
            completed += 1
            db.progress_eval_run(run_id, completed)

            # Checked once per conversation, not per judge call — a run can only
            # stop between conversations, not mid-flight on an LLM call already
            # sent (see cancel_eval_run's docstring for why).
            if db.is_eval_run_cancelled(run_id):
                cancelled = True
                break

        if annotations:
            client.spans.log_span_annotations(span_annotations=annotations)

        summary = {
            "conversations": completed,
            "classifiers": {
                name: {
                    "tally": tally,
                    "pass_label": CLASSIFIERS[name]["pass_label"],
                    "pass_rate": _pass_rate(tally, CLASSIFIERS[name]["pass_label"]),
                }
                for name, tally in tallies.items()
            },
        }
        db.finish_eval_run(run_id, summary)
        if cancelled:
            db.cancel_eval_run(run_id)  # overwrites finish_eval_run's 'done' status; summary already saved
    except Exception as exc:
        logger.exception("eval run %s failed", run_id)
        db.fail_eval_run(run_id, str(exc))
