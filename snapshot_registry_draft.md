# Persisting Sectors responses — draft registry (for review, no code yet)

Goal: every response we pay for is kept in Postgres, stamped with the date the *data* is
about (not the fetch time), so nothing goes stale — dynamic data becomes history.

## Modes

| Mode | Stored as | Refresh |
|---|---|---|
| **CURRENT** | one row per key, upsert | version bump / weekly sweep; keep `fetched_at` |
| **SERIES** | append one row per `(key, as_of)`, never overwrite | only when a newer `as_of` can exist (epoch moved) |
| **EVENT** | append once, dedupe on natural key, never expires | on fetch |

Fetch rule for SERIES: if newest stored `as_of` = latest available date, don't call Sectors.

## What is already persisted today (Postgres)

| Data | Table | Mode it already behaves as |
|---|---|---|
| Daily close (universe) | `price_daily` | SERIES (OHLCV/market cap mostly NULL, filled lazily per symbol) |
| Corporate actions | `corporate_actions` | EVENT |
| Filings, news | `filings`, `news_articles` | EVENT |
| Foreign flow, broker rankings, suspensions | `foreign_flow_daily`, `broker_rankings`, `suspensions` | SERIES / EVENT |
| Ticker list, sector, indices | `symbol_master` | CURRENT |
| Helper lists | `reference_lists` | CURRENT |

## What is only in Valkey today (lost on flush) — the gap this registry closes

| Source | Seen contents | Mode | Key | as_of comes from | Refresh trigger | Destination |
|---|---|---|---|---|---|---|
| report `overview` | last_close_price, market_cap, daily_close_change, indices, sector, sub_sector, listing_date, employee_num, esg_score | split: price fields SERIES, profile fields CURRENT | `(symbol, as_of)` / `symbol` | `latest_close_date` | price epoch | `company_daily` (series) + update `symbol_master` |
| report `valuation` | forward_pe, intrinsic_value, last_close_price, historical_valuation | SERIES | `(symbol, as_of)` | `latest_close_date` | price epoch | `valuation_history` |
| report `financials` | eps, historical_financials, historical_financial_ratio, yoy growth | SERIES by report period | `(symbol, period)` | report period in the payload (confirm field) | symbol version bump (quarterly-dates change detector) | `fundamentals_history` |
| report `dividend` | historical_dividends, upcoming_dividends, yield_ttm, payout_ratio | EVENT (per ex-date) + CURRENT summary | `(symbol, ex_date)` | ex-date | symbol version bump | `dividends` |
| report `ownership` | major_shareholders, conglomerates_group, whale_investors, top_transactions | SERIES | `(symbol, as_of)` | fetched trading date (no date in payload — confirm) | symbol version bump | `ownership_snapshots` |
| report `peers`, `future`, `management` | not seen yet — **unverified** | decide after first capture | | | | raw only for now |
| `shareholders_composition` | monthly local/foreign holder breakdown | SERIES | `(symbol, date)` | each entry's `date` | monthly | `shareholding_monthly` |
| `quarterly_financials` | one entry per quarter | SERIES | `(symbol, period)` | entry period | version bump | `fundamentals_history` (shared) |
| `free_float` | per-symbol free float | SERIES | `(symbol, as_of)` | fetched date | weekly | `free_float_history` |
| `subsector_report` | subsector aggregates | SERIES | `(subsector, as_of)` | fetched trading date | price epoch | raw only for now |
| screener | arbitrary query → rows | **raw snapshot only** (query space is open-ended) | `(query_hash, fetched_at)` | fetched trading date | — | `api_snapshots` |

## Layer 1 for everything (cheap, do first)

`api_snapshots(endpoint, symbol, query_hash, as_of, fetched_at, credits, payload JSONB)` —
written once at `SectorsClient._get` for every real response. Typed tables above are then
*derived* from it by small extract steps, so the classification can be changed later and
tables rebuilt without buying anything again. `credits` doubles as the spend ledger.

## Please check

1. Is the CURRENT/SERIES/EVENT split above right for each row? Biggest judgement calls:
   `ownership` (SERIES vs CURRENT) and `financials` (restatements — append new row, keep old).
2. Which typed tables do you actually want first? Suggest: `company_daily`,
   `valuation_history`, `fundamentals_history` — the rest stay raw until needed.
3. Unverified items (marked above) need one real response each before a mode is final;
   they get captured for free the first time anyone asks.
4. Confirm Sectors' terms allow long-term storage/analytics of responses (not checked).
