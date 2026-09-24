"""Sectors API v2 (IDX) client. Shared by gateway/ (cache-miss reads) and ingest/
(scheduled pulls).

Every path below was verified live against the real API (2026-09-24), not guessed —
see portfolio-intelligence-data-gap-analysis-v1.md and PROGRESS.md for the discovery
notes. Two important corrections from earlier assumptions:

  - `screener`'s `where` is a SQL-like *string* (e.g. "sector='Financials'"), not a
    JSON object. Every response here is an envelope: {"results": [...], "pagination":
    {...}} (screener/news/filings/etc.) or a bare list/dict for the smaller reference
    endpoints — checked per-endpoint below.
  - The bulk daily close feed (`close/`) returns ONLY symbol/date/close — no volume,
    no market cap. This confirms the ⚠ in the original ingest plan doc. The per-symbol
    `daily/{symbol}/` endpoint (previously pruned as "dead"/INGEST-served, which was
    wrong) has full OHLCV + market cap and is now the LAZY-ATOMIC backfill source for
    what the bulk feed lacks — see data/repositories.py::ensure_price_detail.

Do NOT trust `https://docs.sectors.app/llms.txt` — it was fetched and summarized once
and produced a plausible-looking but almost entirely fabricated endpoint list (e.g.
`/v2/indonesia/screener/companies`, `/v2/indonesia/report/company-report/{symbol}`);
none of those paths exist. Every path in ENDPOINTS was confirmed by an actual request
with a real key, not by re-reading documentation.

Paths were verified live; most *filter query parameter names* were not (only tested
with zero params, which returned 200). `date`/`start`/`end`/`symbol` args on
get_corporate_actions_calendar, get_suspensions, get_top_brokers_daily, and
get_foreign_flow_daily are still best-guess parameter names, not confirmed — verify
each before relying on them to actually filter (an unrecognized query param is
typically just ignored by REST APIs, which would silently return unfiltered results
rather than erroring, so this is easy to miss).

Two of these were confirmed live via a real agent conversation (2026-09-24), not by
direct probing — see PROGRESS.md item 19:
  - `get_filings(symbol=...)` and `get_corporate_actions(symbol=...)`: confirmed
    working — a live call for BBCA returned BBCA-specific insider-filing and
    AGM/dividend data, not an unfiltered dump.
  - `get_news(symbol=...)`: confirmed the OPPOSITE of "silently ignored" — the API
    hard-errors (400) on an unrecognized `symbol` param rather than ignoring it. The
    real param is `symbols` (plural); fixed below.

Still unresolved despite direct probing (not in ENDPOINTS, no method exists):
  - Revenue segments / companies-with-revenue-segments (no working path found).
  - The quarterly-dates *universe* feed (the change-detector's core input) — no
    working bulk path found. This blocks ingest/jobs/quarterly_dates.py as designed;
    see PROGRESS.md for the open question of whether to redesign that job to poll
    quarterly_financials per symbol instead of a single bulk diff.

Credit protection (original ingest plan doc, section 6):
  - Callers must validate symbols/slugs/broker codes against the symbol master and
    reference lists before calling — a 404 costs 1 credit, a 400 does not.
  - 404s are negative-cached briefly via the injected Cache. 429/5xx are never cached.
"""

import httpx

from data.cache import Cache
from data.rate_limit import TokenBucket

ENDPOINTS = {
    # Helper lists / reference. Bare list responses (not {"results": ...}).
    "subsectors": "subsectors/",
    "industries": "industries/",
    "subindustries": "subindustries/",
    "news_tags": "tags/",
    "broker_registry": "brokers/",
    # Screener / symbol master. {"results": [...], "pagination": {...}}.
    "screener": "companies/",
    "free_float": "free-float/",  # bare list
    # Company. Bare dict responses.
    "corporate_actions_symbol": "company/corporate-actions/{symbol}/",
    "shareholders_composition": "company/shareholders-composition/{symbol}/",
    "company_report": "company/report/{symbol}/",
    "quarterly_financials": "financials/quarterly/{symbol}/",  # bare list, one entry per quarter
    "subsector_report": "subsector/report/{subsector}/",
    # Transaction data.
    "daily_universe_close": "close/",  # {"results": [{"symbol","date","close"}]} — no volume/market_cap
    "daily_transaction_symbol": "daily/{symbol}/",  # bare list of {"symbol","date","open","high","low","close","volume","market_cap"}
    # News, filings, calendar. {"results": [...]} for news/filings; bare dict/list for the rest.
    "corporate_actions_calendar": "corporate-actions/",
    "filings": "filings/",
    "news": "news/",
    "suspensions": "suspensions/",  # {"results": [...]}
    # Brokers.
    "top_brokers_daily": "brokers/top/",
    "foreign_flow_daily": "foreign-flow/",  # {"results": [...]}
    "broker_activity_symbol": "broker-summary/{symbol}/",
}


class SectorsAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"{status_code}: {message}")
        self.status_code = status_code


class SectorsNotFoundError(SectorsAPIError):
    pass


class SectorsClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        rate_limiter: TokenBucket | None = None,
        cache: Cache | None = None,
    ):
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": api_key},
            timeout=30.0,
        )
        self._rate_limiter = rate_limiter or TokenBucket(capacity=5, refill_per_second=1.0)
        self._cache = cache

    def close(self) -> None:
        self._http.close()

    def _get(self, endpoint_key: str, path_params: dict | None = None, **query) -> dict:
        # ENDPOINTS values are relative (no leading "/") and base_url ends with "/v2/" —
        # a leading "/" here would make httpx resolve against the domain root and
        # silently drop the "/v2/" prefix.
        path = ENDPOINTS[endpoint_key].format(**(path_params or {}))
        query = {k: v for k, v in query.items() if v is not None}
        negative_cache_key = f"sectors:404:{path}:{sorted(query.items())}"

        if self._cache is not None and self._cache.is_negative_cached(negative_cache_key):
            raise SectorsNotFoundError(404, f"negative-cached: {path}")

        self._rate_limiter.acquire()
        response = self._http.get(path, params=query)

        if response.status_code == 404:
            if self._cache is not None:
                self._cache.negative_cache(negative_cache_key)
            raise SectorsNotFoundError(404, response.text)
        if response.status_code == 400:
            raise SectorsAPIError(400, response.text)
        response.raise_for_status()
        return response.json()

    # -- Helper lists / reference --------------------------------------------

    def get_subsectors(self) -> list[dict]:
        return self._get("subsectors")

    def get_industries(self) -> list[dict]:
        return self._get("industries")

    def get_subindustries(self) -> list[dict]:
        return self._get("subindustries")

    def get_news_tags(self) -> list[str]:
        return self._get("news_tags")

    def get_broker_registry(self) -> list[dict]:
        return self._get("broker_registry")

    # -- Screener --------------------------------------------------------------

    def get_screener(
        self,
        where: str | None = None,
        order_by: str = "symbol",
        limit: int = 200,
        offset: int = 0,
        include_query_values: bool = True,
    ) -> dict:
        """`where` is a SQL-like condition string, e.g. "sector='Financials'". Prefix
        `order_by` with "-" for descending (e.g. "-market_cap"). Structured mode
        (this one) costs 1 credit; passing a natural-language `q` instead costs 3 and
        is deliberately not exposed here — prefer letting the agent emit `where`
        directly (idx_agent_infrastructure_diagrams_md.md's own guidance)."""
        return self._get(
            "screener",
            where=where,
            order_by=order_by,
            limit=limit,
            offset=offset,
            include_query_values=include_query_values,
        )

    def get_free_float(self) -> list[dict]:
        return self._get("free_float")

    # -- Company / reports ------------------------------------------------------

    def get_shareholders_composition(self, symbol: str) -> dict:
        return self._get("shareholders_composition", {"symbol": symbol})

    def get_company_report(self, symbol: str, sections: list[str]) -> dict:
        return self._get("company_report", {"symbol": symbol}, sections=",".join(sections))

    def get_quarterly_financials(self, symbol: str) -> list[dict]:
        return self._get("quarterly_financials", {"symbol": symbol})

    def get_subsector_report(self, subsector: str) -> dict:
        return self._get("subsector_report", {"subsector": subsector})

    def get_corporate_actions(self, symbol: str) -> dict:
        return self._get("corporate_actions_symbol", {"symbol": symbol})

    # -- Transaction data ---------------------------------------------------

    def get_daily_universe_close(self, trade_date: str, offset: int = 0) -> dict:
        """Bulk close price for every symbol on one date. No volume or market cap —
        use get_daily_transaction to backfill those per symbol when needed.

        Confirmed live: hard-capped at 30 rows/page regardless of a requested
        `limit` (962 symbols -> ~33 pages/day); paginate via the response's own
        `pagination.has_next`/`next_offset`, same as the screener.
        """
        return self._get("daily_universe_close", date=trade_date, offset=offset)

    def get_daily_transaction(self, symbol: str) -> list[dict]:
        """Per-symbol OHLCV + market cap, up to 90 days. This is the LAZY-ATOMIC
        source for volume/market_cap that the bulk close feed does not provide."""
        return self._get("daily_transaction_symbol", {"symbol": symbol})

    # -- News, filings, calendar ----------------------------------------------

    def get_corporate_actions_calendar(self, start: str | None = None, end: str | None = None) -> dict:
        return self._get("corporate_actions_calendar", start=start, end=end)

    def get_filings(self, symbol: str | None = None) -> dict:
        return self._get("filings", symbol=symbol)

    def get_news(self, symbol: str | None = None) -> dict:
        """Confirmed live (2026-09-24): `/news/` rejects a `symbol` param outright
        (400: "Unsupported query parameter(s): symbol. Allowed: commodity_type, end,
        extension, keyword, limit, offset, sector, start, sub_sector, symbols,
        tags.") — the real param is the plural `symbols`. `get_filings`/
        `get_corporate_actions_symbol` do accept `symbol` (singular) and were
        confirmed live to actually scope results, so this is a `/news/`-specific
        quirk, not a client-wide naming mistake."""
        return self._get("news", symbols=symbol)

    def get_suspensions(self) -> dict:
        return self._get("suspensions")

    # -- Brokers ----------------------------------------------------------------

    def get_top_brokers_daily(self, trade_date: str | None = None) -> dict:
        return self._get("top_brokers_daily", date=trade_date)

    def get_foreign_flow_daily(self, trade_date: str | None = None) -> dict:
        return self._get("foreign_flow_daily", date=trade_date)

    def get_broker_activity_symbol(self, symbol: str, start: str, end: str) -> dict:
        return self._get("broker_activity_symbol", {"symbol": symbol}, start=start, end=end)
