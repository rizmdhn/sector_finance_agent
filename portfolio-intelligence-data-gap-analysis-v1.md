# Portfolio Intelligence — Data Coverage Gap Analysis

Version 1 · Prepared against `portfolio-intelligence-business-requirements-v1.1.md`
Data source under review: Sectors API (IDX), as catalogued in
`sectors_idx_ingest_cache_plan_md.md`.

## Purpose

Section 1 of the business requirements asks that "requirements that cannot be
supported by the available data should be documented with the specific gap, its
analytical impact, and a proposed reduction in scope for business review." This
document is that review artifact. It does not propose technical architecture — that
remains the solution architect's responsibility per the same section.

## Method and a caveat on confidence

This analysis cross-references the business requirements against the endpoint
catalogue already assembled for this project (`sectors_idx_ingest_cache_plan_md.md`),
which itself is built from endpoint *index descriptions*, not full field-level
documentation. Several items below are flagged `UNVERIFIED` rather than `GAP` because
the honest answer is "the endpoint category exists, but we have not confirmed the
exact fields it returns." Per the business doc's own instruction in section 5 —
"confirm the actual fields, coverage, dates, and definitions before committing to a
feature" — these need a direct check against Sectors' field-level API docs before
being treated as either supported or unsupported.

## Coverage summary: "Must for the first demonstration" (business doc section 9)

| Capability | Status |
|---|---|
| Research a company without holdings | Supported |
| Accept a watchlist or proposed/actual portfolio | Supported (positions/cash are user-supplied by design) |
| Calculate weights, name/sector concentration | Supported |
| Calculate liquidity where supported | Supported |
| Identify material changes | Supported |
| Provide dated evidence | Supported, with one verification item (see G4) |
| Apply independent review | Not a data question — an internal process requirement |
| Explain missing data | Supported *only if* the gaps below are surfaced honestly rather than silently dropped |

Everything required for the minimum demonstration is achievable with Sectors as the
sole data source, provided the two real gaps below (G1, G2) are handled the way the
business doc already anticipates: disclosed as coverage limitations, not fabricated.

## Gaps

### G1 — Controlling-group / corporate-group mapping

**Requirement affected:** Section 3 ("issuer, sector, controlling-group, and common
economic exposures"), section 4 (Portfolio Risk Lead: "controlling-group weight"),
Appendix B (controlling-group weight thresholds), Appendix C ("several holdings share
a controller").

**Gap:** Sectors' Shareholders Composition endpoint returns major shareholders *per
company*. There is no endpoint that returns a pre-built cross-company group taxonomy
(e.g. "these N tickers all roll up to Group X"). Building that requires either manual
curation or a separate data source; it does not exist as a queryable field.

**Analytical impact:** Controlling-group weight (Appendix B threshold) and the
"several holdings share a controller" acceptance case (Appendix C) cannot be computed
automatically for the full IDX universe. Group exposure can only be shown for
holdings an analyst has manually mapped.

**Proposed scope reduction:** Hand-curate a small mapping table for the demonstration
universe (a handful of well-known conglomerates covers most realistic demo
portfolios). For any holding outside that table, report ownership from Shareholders
Composition directly and disclose "group mapping unavailable for this holding" rather
than omitting the exposure category. This matches the business doc's own instruction
in section 9: "if group mapping... is unavailable, show the limitation rather than
fabricate coverage."

### G2 — Macro inputs (FX, interest rates)

**Requirement affected:** Section 5 ("macro inputs require the same discipline"),
Appendix A scenario/statistical risk section (rupiah-depreciation example).

**Gap:** Sectors is an IDX equities data API. It has no FX rate, interest rate, or
other macroeconomic series.

**Analytical impact:** Any scenario that needs a live macro input (e.g. "how does a
7% rupiah move affect this portfolio today") cannot be sourced from Sectors.

**Proposed scope reduction:** None needed beyond what the business doc already
specifies — section 5 explicitly allows treating macro numbers as a labeled scenario
assumption rather than a retrieved fact ("assume a 10% rupiah depreciation is a valid
scenario input; it does not establish how any particular stock will respond"). No
separate macro data source is in scope for this build unless a business owner adds
one.

### G3 — Cost-of-capital inputs for DCF (WACC, cost of equity, risk-free rate)

**Requirement affected:** Appendix A, Valuation section (FCFF/FCFE terminal value
formulas).

**Gap:** Sectors can plausibly supply the raw financial-statement line items a DCF
needs (EBIT, D&A, capex, working-capital changes) via Company Report / Quarterly
Financials, subject to G4 below. It cannot supply WACC or cost of equity — these are
analytical judgments (risk-free rate + equity risk premium + beta), not a data-vendor
field for any provider.

**Analytical impact:** None for this release — full forecast-driven DCF is already
listed in the business doc's "Later" priority tier (section 9), not a demonstration
requirement.

**Proposed scope reduction:** None needed now. Flagging so it is not mistaken for a
Sectors data gap when DCF work eventually starts — it is a modeling-assumption gap
that exists regardless of data provider.

### G4 — `RESOLVED (available)`: financial statement field granularity and bank-specific templates

**Requirement affected:** Section 3 ("choose the financial template by business
model... banks need asset-quality, funding, profitability, and capital measures"),
Appendix A Fundamentals section (bank-specific ratios: NIM, NPL, coverage,
loan-to-deposit, cost-to-income, capital adequacy).

**Status:** Confirmed live against `company/report/{symbol}/` (2026-09-24, real BBCA
call). The real section list is `overview, valuation, future, peers, financials,
dividend, management, ownership`. Bank-specific ratios (NIM, NPL, loan-to-deposit,
capital adequacy, CASA) exist as named fields inside the `financials` section's
`historical_financial_ratio` array, one entry per fiscal year — not approximated from
generic line items. This was the last remaining unknown from the original ⚠ in
`sectors_idx_ingest_cache_plan_md.md` section 8.

**Analytical impact:** None — bank-specific fundamentals are supported as intended by
Appendix A. Still outstanding: confirming the exact convention (definition, reporting
basis) behind each named ratio before treating it as directly comparable across
issuers, per Appendix A's own warning against "presenting [a ratio] as comparable when
its convention is unknown" — that is a labeling/provenance task for whoever wires
`analysis/fundamentals.py`'s bank-specific functions to this field, not a further data
gap.

**Status of the wiring itself:** `financials` was fetched for BBCA (1 credit, one
call, one symbol) and `analysis/fundamentals.py` plus `analysis/valuation.py`'s
raw-component functions are now wired to it via
`data/analysis_bridge.py::fundamentals_snapshot`. Two field-mapping choices were
validated by cross-checking the computed ratios against Sectors' own precomputed
`historical_financial_ratio` for BBCA: loan-to-deposit uses `net_loan` (not
`gross_loan`) as its numerator, and NIM uses `non_loan_earning_assets` alone (not
summed with loans) as its denominator — both reproduce Sectors' own reported ratios
to at least 6 decimal places, despite `non_loan_earning_assets`' misleading name.
`non_performing_loans` has no stable named field at all; it is parsed from a
human-readable label inside `industry_breakdown`, confirmed for BBCA only — verify
before trusting `gross_npl_ratio` for any other bank. FCFF/FCFE correctly come back
`Unavailable` for BBCA: there is no change-in-operating-working-capital field
anywhere in `historical_financials`, for banks or otherwise.

### G5 — `UNVERIFIED`: dividend/corporate-action price adjustment

**Requirement affected:** Appendix A, Returns and drawdowns section ("label price
returns when dividends are unavailable").

**Status:** Unconfirmed whether Sectors' daily price series (Daily Full-Universe
Close, per-symbol Daily Transaction) is total-return-adjusted, split/dividend-adjusted
close only, or raw close.

**Analytical impact if raw/close-only:** Return and drawdown calculations in Appendix
A would need to be explicitly labeled as price returns rather than total returns,
which the business doc already permits ("label price returns when dividends are
unavailable") — so this is a labeling requirement, not a blocker, once confirmed.

**Recommended action:** Confirm adjustment convention before implementing the Returns
and drawdowns section of Appendix A.

### G6 — `CONFIRMED GAP`: consolidation basis and normalization traceability

**Requirement affected:** Section 5 ("business description, financial statements,
reporting periods, and consolidation basis"), section 3 ("every adjustment needs a
reason and a link back to the original figure").

**Status:** Confirmed during the same live verification pass as G4 (2026-09-24).
Neither `company/report/{symbol}/`'s `financials` section nor `financials/quarterly/
{symbol}/` labels consolidation basis (consolidated vs. standalone) anywhere in the
response. This is a real gap, not just an unverified one.

**Analytical impact:** Section 5's "align periods, currency, consolidation... before
calculating comparisons" instruction cannot be automatically enforced — the pipeline
cannot detect a standalone-vs-consolidated mismatch between two issuers (or between an
issuer and its peer set) from the data alone.

**Proposed scope reduction:** Assume consolidated basis for all figures (the common
default for IDX-listed issuers) and disclose this as a stated assumption on any
cross-issuer comparison, rather than silently treating the two bases as
interchangeable. If a specific comparison is known to involve a standalone-basis
issuer, that must come from analyst knowledge outside Sectors, not from a queryable
field.

### G7 — `CONFIRMED GAP`: quarterly-dates change-detector universe feed

**Requirement affected:** Section 4 (Investment Research Lead: thesis/change
monitoring "material changes" trigger), the fund-epoch/version-bump cache-invalidation
design this project built around that trigger.

**Status:** Confirmed by direct probing (2026-09-24): no bulk endpoint or screener
field returns "latest quarterly report date per symbol" for the whole IDX universe.
Every guessed screener field name for this returned `400 INVALID_WHERE_CLAUSE` ("field
does not exist"), a real rejection rather than a syntax issue. `ingest/jobs/
quarterly_dates.py` now raises `NotImplementedError` and is unscheduled rather than
silently no-opping.

**Analytical impact:** The Investment Research Lead's automatic "a new quarterly
report just landed" trigger cannot be built as a single daily bulk diff. Per-symbol
polling (`financials/quarterly/{symbol}/`) works but a 962-symbol daily sweep has a
real credit cost that has not yet been budgeted or measured.

**Proposed scope reduction:** For the demonstration, restrict change detection to the
analyst's actual watchlist/portfolio symbols (a handful, not the full universe) and
poll `financials/quarterly/{symbol}/` per symbol on that smaller set — well within a
reasonable credit budget — rather than attempting a full-universe daily sweep.

## Not a gap: everything else in section 5's evidence table

Portfolio/liquidity evidence (dated prices, traded value, free-float), events and
market signals (corporate actions, filings, news, price/volume/flow/broker data) map
cleanly onto endpoints already scoped in `sectors_idx_ingest_cache_plan_md.md` and are
not treated as gaps here.

## Recommended next step

G4 is resolved (available). G6 and G7 are confirmed real gaps with proposed scope
reductions above — no further investigation needed, just a business decision on
whether to accept them. G5 (price-adjustment convention) is still `UNVERIFIED` and
should be confirmed before implementing `analysis/returns.py` against real price data
for anything beyond simple price returns. G1 and G2 need a business decision (accept
the curated-mapping / scenario-assumption reductions above, or descope those
capabilities entirely for the demonstration) rather than further data investigation —
the data situation for both is already fully understood.

## Revision note (2026-09-24)

G4 moved from `UNVERIFIED` to resolved-available, and G6 moved from `UNVERIFIED` to a
confirmed gap, based on a live `company/report/BBCA/` call made during real-data
wiring (see `PROGRESS.md`). G7 (quarterly-dates universe feed) was added as a new
confirmed gap discovered during the same session. No new Sectors API calls were made
to produce this revision — it reflects findings already on record in `PROGRESS.md`.
