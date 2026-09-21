# Sectors API v2 (IDX): Ingest vs Cache Plan

**Scope:** Indonesia (IDX) endpoints only.

**Basis:** The Company Screener page was read in full. All other endpoints are planned from the endpoint index descriptions. Rows marked ⚠ rest on an assumption to verify on that endpoint's page before building.

---

## 1. Decision rule

| Strategy | What it means | Use when |
|---|---|---|
| **INGEST** | Scheduled job pulls data into your own DB (Postgres or DuckDB). Users and the agent read locally and never hit the API for this data. | A full-universe or date-window feed exists; data is append-only and immutable once published; volume is bounded. |
| **LAZY-ATOMIC** | Fetch on demand, store at the finest unit (e.g. `symbol + date`) in the DB. Only missing gaps are fetched. Past units never expire. | No universe feed exists, but data is immutable per unit. |
| **CACHE** | Fetch on demand, store in Redis/Valkey under a canonical key, with epoch or TTL invalidation. | Parameter space is large or unbounded, or the response is a derived view. |
| **REFERENCE** | Near-static list. Ingest weekly. Use it to validate inputs and to build enums in tool schemas. | Lists of slugs, codes, registries. |

"INGEST-served" means the endpoint itself is never called. Requests are answered from data ingested via another endpoint.

---

## 2. Endpoint plan

### Helper lists
| Endpoint | Strategy | Unit / key | Refresh | Notes |
|---|---|---|---|---|
| Subsectors, Industries, Subindustries | REFERENCE | whole list | weekly | Use as tool enums. Validate slugs before calling. |
| News Tags | REFERENCE | whole list | weekly | Enum for news and filings filters. |
| Companies with Revenue Segments | REFERENCE | dict of symbol → years | weekly, and after annual reports | Tells you when the segments endpoint is valid. |
| Latest Quarterly Financial Dates (Universe) | **INGEST** | symbol → latest report date | daily (hourly in reporting season) | **Change detector.** Diff against the previous snapshot and bump `ver:idx:{symbol}` on change. |
| Quarterly Financial Dates (per symbol) | INGEST-served | symbol | on version bump | Derive from the universe feed. Call the per-symbol endpoint only if history is missing. |

### Screener
| Endpoint | Strategy | Unit / key | Refresh | Notes |
|---|---|---|---|---|
| Companies Screener | **CACHE** | canonical query → top-200 rows | epoch-based (section 3) | Slice locally for `limit` and `offset`. Two-level cache for `q`. |
| Companies Screener (symbol master) | **INGEST** | one sweep with `limit=200`, paging by `offset` | weekly | Builds your list of valid symbols, so typos never hit the API. ⚠ Confirm an empty `where` is accepted. |
| Free Float Market Analysis | CACHE | (taxonomy level, slug) | daily | Small key space. |

### Company
| Endpoint | Strategy | Unit / key | Refresh | Notes |
|---|---|---|---|---|
| Corporate Actions (per symbol) | INGEST-served | from the calendar feed below | with the calendar | Serve per-symbol views from the ingested calendar. |
| Shareholders Composition | CACHE | symbol | 1 to 7 days ⚠ | Changes rarely. |

### Reports
| Endpoint | Strategy | Unit / key | Refresh | Notes |
|---|---|---|---|---|
| Company Report | **CACHE** | `(symbol, section)` | price-linked sections: until next daily ingest. Fundamentals: until `ver:idx:{symbol}` changes. | Never key on the combination of sections. Fetch only missing sections. Prewarm LQ45/IDX30 nightly. ⚠ Check the section list and per-section credit cost. |
| Company Revenue Segments | CACHE | `(symbol, year)` | completed years: long. Latest year: bump on annual report. | Check availability via the segments list first. |
| Company Quarterly Financials | CACHE | `(symbol, report_date)` | immutable once published (barring restatement) | New `report_date` comes from the change detector. |
| Subsector Report | CACHE | `(subsector, section)` | price-derived sections: daily. Others: longer. | Bounded key space, so prewarm all nightly. |

### Transaction data
| Endpoint | Strategy | Unit / key | Refresh | Notes |
|---|---|---|---|---|
| Daily Full-Universe Close | **INGEST** | one call sequence per trading date | daily after close | Primary price store. |
| Daily Transaction Data (per symbol, up to 90 days) | INGEST-served | read the price store | n/a | ⚠ If the universe feed lacks volume or market cap, fill those fields LAZY-ATOMIC per `(symbol, date)`. |
| IDX Market Summary (up to 90 days) | **INGEST** | per date | daily | Tiny. |
| Daily Full-Universe Index Close | **INGEST** | per date | daily | |
| Index Daily Transaction Data (up to 90 days) | INGEST-served | read the index store | n/a | |

### Rankings
| Endpoint | Strategy | Unit / key | Refresh | Notes |
|---|---|---|---|---|
| Top Company Movers | CACHE | `(classification, period)`, 2 × 5 = 10 keys | until next daily ingest | Could be computed locally from the price store, but caching the API's version keeps numbers consistent with the docs. |
| Most Traded Stocks (up to 90 days) | CACHE | `(start, end)` snapped to presets | past ranges never change | Can be computed locally if volume is ingested. |

### IPO
| Endpoint | Strategy | Unit / key | Refresh | Notes |
|---|---|---|---|---|
| Listing Performance | CACHE | symbol | ⚠ If windows are anchored to the listing date, completed windows never change: long TTL. Otherwise refresh daily. | |

### News, filings, calendar
| Endpoint | Strategy | Unit / key | Refresh | Notes |
|---|---|---|---|---|
| Corporate Actions Calendar | **INGEST** | date window, incremental | daily. Re-pull the forward-looking window each time, since announced actions can change. | Feeds the per-symbol Corporate Actions views. |
| Company Filings (insider) | **INGEST** | append by date | hourly to daily | Query locally by symbol, sector, holder type. |
| News Articles (IDX and mining `extension`) | **INGEST** | append by date, per `extension` | hourly to daily | Index in Postgres full-text or pgvector if you want RAG over news. |
| Stock Suspensions | **INGEST** | small list | daily | |

### Brokers
| Endpoint | Strategy | Unit / key | Refresh | Notes |
|---|---|---|---|---|
| Broker Registry | REFERENCE | whole list | weekly | Source of valid broker codes. |
| Top Brokers Daily Ranking | **INGEST** | per date, all brokers | daily after close | Store unfiltered, then filter by origin or cohort locally. |
| Daily Full-Universe Foreign Flow | **INGEST** | per date | daily after close | |
| Daily Net Foreign Inflow (per symbol, up to 90 days) | INGEST-served | read the foreign-flow store | n/a | |
| Broker Activity Per Symbol (up to 14 days) | **LAZY-ATOMIC** | `(symbol, date)` → broker rows | past days never expire | No universe feed, so a full ingest is every symbol every day. Ingest only a watchlist or hot symbols. ⚠ |
| Broker Activity By Code (up to 14 days) | LAZY-ATOMIC | `(broker, date)` | past days never expire | |
| Top Buyers and Sellers Per Symbol | INGEST-served or CACHE | compute from the per-symbol rows; otherwise `(symbol, start, end)` snapped | | |
| Top Accumulations and Distributions Per Broker | CACHE | `(broker, start, end)` snapped | recent ranges: daily. Past: never. | |

---

## 3. Screener details (the hardest endpoint)

**Canonical key**
1. Lowercase field names and operators. Unify quotes and whitespace.
2. Sort conditions only when the whole clause is joined by `and` (never reorder across `or`).
3. Fill defaults: `limit=50`, `offset=0`, `order_by=symbol`, `desc=false`.
4. Resolve relative terms ("latest year") to explicit years. The docs say "latest year" shifts meaning between January and April.
5. Always request `include_query_values=true` and strip on your side, so one key serves both.
6. Fetch `limit=200` once and slice locally for smaller pages.

**Epoch instead of TTL.** Parse the fields used and classify:

| Field class (my grouping, verify) | Examples | Add to key |
|---|---|---|
| Price-derived | `last_close_price`, `daily_close_change`, `market_cap`, `market_cap_rank`, 52-week and YTD highs and lows, `tags` | `price_epoch` = trading date of the last completed ingest |
| Quarterly | `*_mrq`, `*_ttm`, `revenue_q[Q1-2024]`, `yoy_quarter_*` | `fund_epoch` = counter bumped when the change detector fires for any symbol |
| Annual and forecast | `revenue[2024]`, `forecast_*[2025]` | 1-day TTL |
| Static | `sector`, `listing_date`, `employee_num` | 7-day TTL |

Key = `hash(canonical_query)` plus the epochs that apply. Old keys simply stop being read and expire.

**`q` (natural language, 3 credits).** The response returns the structured `where` and `order_by` it generated. Cache normalized `q` → translated params, then cache results under the structured key. Preferably let your own agent emit `where` directly and never call `q`. Put the field list in the cached prompt prefix.

---

## 4. Ingestion schedule

| Job | When | What |
|---|---|---|
| IDX end-of-day | After close (WIB). **Poll until today's date appears** instead of hard-coding a time. | Full-universe close, index close, market summary, foreign flow, top brokers ranking, quarterly-dates universe, suspensions, corporate-actions calendar |
| Feeds | Hourly to daily | IDX news, insider filings |
| Nightly prewarm | After the end-of-day job | Company Report sections for LQ45/IDX30 (use the `indices` field), all subsector reports, 10 top-mover keys, free float |
| Weekly | | Reference lists, symbol master |

**Backfill once.** Universe feeds are per date, so N days is N × pages of calls. Estimate the credit cost before running it.

---

## 5. Key and invalidation conventions

- Key: `sec:v1:idx:{endpoint}:{sha1(canonical_params)}[:{epoch}]`. Bump `v1` when parsing logic changes.
- Symbols always `BBCA.JK` (uppercase, suffix included). Dates are ISO trading dates, never wall-clock time.
- Per-symbol fundamentals include `ver:idx:{symbol}`, bumped by the change detector, a new filing, or an ex-dividend date.
- Store the raw payload (compressed) and the normalized, trimmed version, with `fetched_at`.
- Single-flight lock per key. Serve stale while one worker refreshes.

## 6. Credit protection

- Validate symbols, slugs and broker codes against the symbol master and reference lists. A 404 costs 1 credit; a 400 is free.
- A failed `q` call can cost 1 credit after it reaches their language model.
- Negative-cache 404s briefly. Do not cache 429 or 5xx beyond a short backoff.
- One shared HTTP client with a token bucket.

## 7. Build order

1. Symbol master and reference lists
2. Redis wrapper with canonicalization and single-flight
3. Universe-close ingest and price store
4. Quarterly-dates change detector and epochs
5. Company Report and quarterly financials cache
6. Screener cache
7. News, filings, brokers, foreign flow

## 8. To verify before building

- Do universe feeds include volume and market cap, or only close?
- Page size and credit cost per page for universe feeds.
- Section list and per-section pricing on Company Report and Subsector Report.
- When the provider's daily data lands after close.
- Whether responses carry an "updated at" timestamp.
- Whether an empty `where` works for the screener sweep.
- Data licensing terms on storing and redistributing the data.
