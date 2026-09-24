"""One-off generator: reads the compliance-eval annotations evals/phoenix_evals.py
already logged to Phoenix and renders them as a static HTML dashboard — 3 pass-rate
tiles (one per rule in evals/phoenix_evals.py's CLASSIFIERS), a "flagged by the
judge" callout for anything that failed, and every graded conversation's full judge
explanations behind a <details> disclosure.

This is NOT a live-updating dashboard and is not meant to run on a schedule — rerun
it (after rerunning phoenix_evals.py against fresh conversations) whenever you want
an updated snapshot; the html file it writes is a point-in-time report, timestamped
in its own footer. See PROGRESS.md item 24 for why this exists and the honest
caveats about sample size (this project ran it once, against 5 deliberately chosen
conversations — a snapshot, not a statistically meaningful long-run pass rate).

Usage:
    python evals/build_dashboard.py --span-ids <id1> <id2> ... [--out FILE]
    # or, to grab the N most recently annotated conversations automatically:
    python evals/build_dashboard.py --limit 5 [--out FILE]
"""

import argparse
import html
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_PHOENIX_URL = "http://localhost:6006"
DEFAULT_PROJECT = "idx-agent-gateway"
DEFAULT_OUT = Path(__file__).resolve().parent / "eval_dashboard.html"

# Mirrors evals/phoenix_evals.py's CLASSIFIERS — kept as a separate dict here
# (rather than imported) because this file also needs each classifier's PASS/FAIL
# label sets and a short human-readable rule statement, which phoenix_evals.py has
# no reason to carry itself.
CLASSIFIER_META = {
    "cites_evidence_date": {
        "title": "Cites evidence date",
        "rule": "Every specific figure should carry a fiscal year or as-of date.",
        "good": {"dated", "not_applicable"},
        "bad": {"undated"},
        "label_text": {"dated": "Dated", "undated": "Undated", "not_applicable": "N/A — no figures cited"},
    },
    "no_investment_recommendation": {
        "title": "No investment recommendation",
        "rule": "Describes findings; never tells the user what to do with their money.",
        "good": {"describes_findings_only"},
        "bad": {"gives_recommendation"},
        "label_text": {
            "describes_findings_only": "Describes findings only",
            "gives_recommendation": "Gives recommendation",
        },
    },
    "discloses_missing_data": {
        "title": "Discloses missing data",
        "rule": "States a real gap or limitation plainly rather than glossing over it.",
        "good": {"discloses_clearly", "not_applicable"},
        "bad": {"glosses_over"},
        "label_text": {
            "discloses_clearly": "Discloses clearly",
            "glosses_over": "Glosses over",
            "not_applicable": "N/A — nothing to disclose",
        },
    },
}
CLASSIFIER_ORDER = list(CLASSIFIER_META)


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def _load(phoenix_url: str, project: str, span_ids: list[str] | None, limit: int):
    from phoenix.client import Client

    client = Client(base_url=phoenix_url)
    spans_df = client.spans.get_spans_dataframe(project_name=project, root_spans_only=True, limit=1000)
    spans_df = spans_df[spans_df["name"] == "chat_completion"].copy()
    if spans_df.empty:
        return {}, []

    ann = client.spans.get_span_annotations_dataframe(spans_dataframe=spans_df, project_identifier=project)
    if ann.empty:
        return {}, []

    if span_ids:
        ann = ann[ann.index.isin(span_ids)]
    else:
        # Most recently created annotations first, capped to `limit` distinct
        # conversations (each conversation has one row per classifier).
        recent_order = ann.sort_values("created_at", ascending=False).index.unique()[:limit]
        ann = ann[ann.index.isin(recent_order)]

    by_span: dict[str, dict] = defaultdict(dict)
    order: list[str] = []
    for span_id, row in ann.sort_values("created_at").iterrows():
        if span_id not in by_span:
            order.append(span_id)
        by_span[span_id][row["annotation_name"]] = {
            "label": row["result.label"],
            "explanation": row["result.explanation"],
            "question": spans_df.loc[span_id, "attributes.input.value"],
        }
    return by_span, order


def _label_for(by_span: dict, span_id: str) -> str:
    """Short display label for a conversation row — the question itself, since
    there's no separate human-assigned name for these conversations."""
    for clf in CLASSIFIER_ORDER:
        if clf in by_span[span_id]:
            return by_span[span_id][clf]["question"]
    return span_id


def build(by_span: dict, order: list[str]) -> str:
    summary = {}
    for clf in CLASSIFIER_ORDER:
        meta = CLASSIFIER_META[clf]
        graded = [sid for sid in order if clf in by_span[sid]]
        good = sum(1 for sid in graded if by_span[sid][clf]["label"] in meta["good"])
        total = len(graded)
        summary[clf] = {"good": good, "total": total, "pct": round(100 * good / total) if total else 0}

    flagged = []
    for sid in order:
        for clf in CLASSIFIER_ORDER:
            rec = by_span[sid].get(clf)
            if rec and rec["label"] in CLASSIFIER_META[clf]["bad"]:
                flagged.append({"span_id": sid, "classifier": clf, "rec": rec})

    def chip(clf, rec):
        meta = CLASSIFIER_META[clf]
        label = rec["label"]
        cls = "good" if label in meta["good"] else ("bad" if label in meta["bad"] else "neutral")
        return f'<span class="chip chip-{cls}">{esc(meta["label_text"].get(label, label))}</span>'

    rows_html = []
    for sid in order:
        q = esc(_label_for(by_span, sid))
        present = [clf for clf in CLASSIFIER_ORDER if clf in by_span[sid]]
        chips = "".join(chip(clf, by_span[sid][clf]) for clf in present)
        details = "".join(
            f'<div class="explain-row"><span class="explain-label">{esc(CLASSIFIER_META[clf]["title"])}</span>'
            f'<p>{esc(by_span[sid][clf]["explanation"])}</p></div>'
            for clf in present
        )
        rows_html.append(f"""
    <details class="convo">
      <summary>
        <span class="convo-q">&ldquo;{q}&rdquo;</span>
        <span class="convo-chips">{chips}</span>
      </summary>
      <div class="convo-detail">{details}</div>
    </details>""")

    flagged_html = ""
    if flagged:
        items = []
        for f in flagged:
            meta = CLASSIFIER_META[f["classifier"]]
            explanation = f["rec"]["explanation"]
            short = explanation[:280] + ("…" if len(explanation) > 280 else "")
            items.append(
                f'<li><strong>{esc(_label_for(by_span, f["span_id"]))[:80]}</strong> — '
                f'{esc(meta["title"])}: <span class="flag-label">{esc(meta["label_text"][f["rec"]["label"]])}</span>'
                f"<p>{esc(short)}</p></li>"
            )
        flagged_html = f"""
    <section class="flagged">
      <h2>Flagged by the judge</h2>
      <ul>{''.join(items)}</ul>
    </section>"""

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary_tiles = "".join(
        f"""
    <div class="tile tile-{'good' if summary[clf]['pct'] >= 80 else ('warn' if summary[clf]['pct'] >= 50 else 'bad')}">
      <div class="tile-pct">{summary[clf]['pct']}%</div>
      <div class="tile-title">{esc(CLASSIFIER_META[clf]['title'])}</div>
      <div class="tile-frac">{summary[clf]['good']} of {summary[clf]['total']} conversations</div>
      <div class="tile-rule">{esc(CLASSIFIER_META[clf]['rule'])}</div>
    </div>"""
        for clf in CLASSIFIER_ORDER
    )

    return f"""<title>Compliance Eval</title>
<style>
:root {{
  --bg: #F1F3EF; --surface: #FFFFFF; --ink: #142420; --ink-soft: #4B5F59; --line: #D8DED8;
  --accent: #0F6B5C; --accent-soft: #E4F0EC; --gold: #B7791F;
  --good: #2F855A; --good-bg: #E4F3EA; --warn: #B7791F; --warn-bg: #FBF0DD; --bad: #B0362B; --bad-bg: #FBE7E4;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #0E1613; --surface: #16211D; --ink: #EAF2EE; --ink-soft: #9FB3AC; --line: #2A3A34;
    --accent: #4FBFA8; --accent-soft: #16342C; --gold: #E0A94A;
    --good: #52C77E; --good-bg: #163024; --warn: #E0A94A; --warn-bg: #332608; --bad: #F1685A; --bad-bg: #3A1714;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #0E1613; --surface: #16211D; --ink: #EAF2EE; --ink-soft: #9FB3AC; --line: #2A3A34;
  --accent: #4FBFA8; --accent-soft: #16342C; --gold: #E0A94A;
  --good: #52C77E; --good-bg: #163024; --warn: #E0A94A; --warn-bg: #332608; --bad: #F1685A; --bad-bg: #3A1714;
}}
* {{ box-sizing: border-box; }}
body {{ background: var(--bg); color: var(--ink); font-family: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif; padding-inline: 20px; padding-block: 40px 64px; }}
.wrap {{ max-width: 880px; margin-inline: auto; }}
.eyebrow {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .78rem; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); margin: 0 0 10px; }}
h1 {{ font-family: "Fraunces", Georgia, serif; font-optical-sizing: auto; font-weight: 600; font-size: clamp(1.9rem, 4vw, 2.6rem); line-height: 1.12; margin: 0 0 8px; text-wrap: balance; }}
.subhead {{ color: var(--ink-soft); font-size: 1.02rem; max-width: 62ch; margin: 0 0 6px; }}
.meta {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .82rem; color: var(--ink-soft); display: flex; gap: 18px; flex-wrap: wrap; margin: 18px 0 36px; padding-block: 14px; border-block: 1px solid var(--line); }}
.meta b {{ color: var(--ink); font-weight: 600; }}
.tiles {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 40px; }}
@media (max-width: 620px) {{ .tiles {{ grid-template-columns: 1fr; }} }}
.tile {{ background: var(--surface); border: 1px solid var(--line); border-bottom: 3px solid var(--line); border-radius: 10px; padding: 18px 18px 16px; }}
.tile-good {{ border-bottom-color: var(--good); }} .tile-warn {{ border-bottom-color: var(--warn); }} .tile-bad {{ border-bottom-color: var(--bad); }}
.tile-pct {{ font-family: "Fraunces", Georgia, serif; font-weight: 600; font-size: 2.4rem; font-variant-numeric: tabular-nums; line-height: 1; }}
.tile-good .tile-pct {{ color: var(--good); }} .tile-warn .tile-pct {{ color: var(--warn); }} .tile-bad .tile-pct {{ color: var(--bad); }}
.tile-title {{ font-weight: 600; margin-top: 8px; font-size: .95rem; }}
.tile-frac {{ color: var(--ink-soft); font-size: .82rem; font-family: "IBM Plex Mono", monospace; margin-top: 2px; }}
.tile-rule {{ color: var(--ink-soft); font-size: .82rem; margin-top: 10px; line-height: 1.4; }}
.flagged {{ background: var(--warn-bg); border: 1px solid var(--warn); border-radius: 10px; padding: 18px 20px 20px; margin-bottom: 40px; }}
.flagged h2 {{ font-family: "Fraunces", Georgia, serif; font-size: 1.15rem; margin: 0 0 12px; color: var(--ink); }}
.flagged ul {{ margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 12px; }}
.flagged li p {{ margin: 4px 0 0; color: var(--ink-soft); font-size: .9rem; line-height: 1.5; }}
.flag-label {{ font-family: "IBM Plex Mono", monospace; color: var(--warn); font-weight: 600; }}
h2.section-title {{ font-family: "Fraunces", Georgia, serif; font-size: 1.15rem; margin: 0 0 14px; }}
.convo {{ border-top: 1px solid var(--line); }}
.convo:last-of-type {{ border-bottom: 1px solid var(--line); }}
.convo summary {{ cursor: pointer; list-style: none; padding-block: 14px; display: grid; grid-template-columns: 1fr auto; gap: 16px; align-items: baseline; }}
.convo summary::-webkit-details-marker {{ display: none; }}
@media (max-width: 700px) {{ .convo summary {{ grid-template-columns: 1fr; gap: 6px; }} }}
.convo-q {{ color: var(--ink-soft); font-size: .92rem; }}
.convo-chips {{ display: flex; gap: 6px; flex-wrap: wrap; }}
.chip {{ font-family: "IBM Plex Mono", monospace; font-size: .72rem; padding: 3px 8px; border-radius: 999px; white-space: nowrap; }}
.chip-good {{ background: var(--good-bg); color: var(--good); }} .chip-bad {{ background: var(--bad-bg); color: var(--bad); }} .chip-neutral {{ background: var(--accent-soft); color: var(--ink-soft); }}
.convo-detail {{ padding: 4px 0 20px; display: flex; flex-direction: column; gap: 14px; }}
.explain-row {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; }}
.explain-label {{ font-family: "IBM Plex Mono", monospace; font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; color: var(--ink-soft); }}
.explain-row p {{ margin: 6px 0 0; font-size: .88rem; line-height: 1.55; color: var(--ink); }}
footer {{ margin-top: 48px; padding-top: 18px; border-top: 1px solid var(--line); color: var(--ink-soft); font-size: .8rem; line-height: 1.6; }}
footer code {{ font-family: "IBM Plex Mono", monospace; background: var(--accent-soft); padding: 1px 5px; border-radius: 4px; color: var(--ink); }}
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<div class="wrap">
  <p class="eyebrow">Portfolio Intelligence &middot; Compliance Evaluation</p>
  <h1>Is the agent following its own rules?</h1>
  <p class="subhead">An LLM judge grades every real answer against three rules from this system's own business requirements &mdash; not a generic hallucination or toxicity check.</p>
  <div class="meta">
    <span>Judge: <b>claude&#8209;haiku&#8209;4&#8209;5</b></span>
    <span>Conversations: <b>{len(order)}</b></span>
    <span>Generated: <b>{generated_at}</b></span>
  </div>
  <div class="tiles">{summary_tiles}
  </div>
{flagged_html}
  <h2 class="section-title">Every conversation, every rule</h2>
{''.join(rows_html)}
  <footer>
    Each rule is graded independently by an LLM judge (<code>evals/phoenix_evals.py</code>) reading only the
    final answer text &mdash; the same text the user received, disclaimer included. Results are logged back to
    Arize Phoenix as span annotations (<code>{DEFAULT_PROJECT}</code> project) for anyone with access to the
    trace UI to audit independently. <b>N/A</b> means the rule didn't apply to that answer (e.g. no figures
    were cited, so there was nothing to date) &mdash; it never counts as a pass or a failure.
  </footer>
</div>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phoenix-url", default=DEFAULT_PHOENIX_URL)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--span-ids", nargs="*", default=None, help="Specific conversations to include")
    parser.add_argument("--limit", type=int, default=5, help="Most recently annotated conversations, if --span-ids omitted")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    os.environ.setdefault("PHOENIX_COLLECTOR_ENDPOINT", args.phoenix_url)
    by_span, order = _load(args.phoenix_url, args.project, args.span_ids, args.limit)
    if not order:
        print(f"No annotated conversations found in project '{args.project}'. Run evals/phoenix_evals.py first.")
        return

    Path(args.out).write_text(build(by_span, order))
    print(f"wrote {args.out} ({len(order)} conversations)")


if __name__ == "__main__":
    main()
