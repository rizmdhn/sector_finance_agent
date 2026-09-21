"""Sectors API v2 (IDX) client. Shared by gateway/ (cache-miss reads) and ingest/
(scheduled pulls).

Endpoint paths below are placeholders (TODO) pending sectors_idx_ingest_cache_plan_md.md
section 8 ("to verify"): exact paths, page size, and per-endpoint credit cost have not
been confirmed against the live API docs. Fix the ENDPOINTS values before relying on this
client against a real deployment.

Credit protection (plan section 6):
  - Callers must validate symbols/slugs/broker codes against the symbol master and
    reference lists before calling — a 404 costs 1 credit, a 400 does not.
  - 404s are negative-cached briefly via the injected Cache. 429/5xx are never cached.
"""

import httpx

from data.cache import Cache
from data.rate_limit import TokenBucket

# TODO: confirm every path against the Sectors API v2 docs.
ENDPOINTS = {
    # Helper lists / reference
    "subsectors": "/v1/subsectors/",
    "industries": "/v1/industries/",
    "subindustries": "/v1/subindustries/",
    "news_tags": "/v1/news/tags/",
    "companies_with_revenue_segments": "/v1/companies/revenue-segments/",
    "quarterly_dates_universe": "/v1/company/get-all-quarterly-dates/",
    "quarterly_dates_symbol": "/v1/company/quarterly-dates/{symbol}/",
    # Screener
    "screener": "/v1/companies/screener/",
    "free_float": "/v1/companies/free-float/",
    # Company
    "corporate_actions_symbol": "/v1/company/corporate-actions/{symbol}/",
    "shareholders_composition": "/v1/company/shareholders/{symbol}/",
    # Reports
    "company_report": "/v1/company/report/{symbol}/",
    "revenue_segments": "/v1/company/revenue-segments/{symbol}/",
    "quarterly_financials": "/v1/company/quarterly-financials/{symbol}/",
    "subsector_report": "/v1/subsector/report/{subsector}/",
    # Transaction data
    "daily_universe_close": "/v1/idx/daily/",
    "daily_transaction_symbol": "/v1/company/daily/{symbol}/",
    "market_summary": "/v1/idx/market-summary/",
    "index_daily_close": "/v1/idx/index-daily/",
    "index_daily_transaction": "/v1/idx/index-daily/{index_code}/",
    # Rankings
    "top_movers": "/v1/companies/top-movers/",
    "most_traded": "/v1/companies/most-traded/",
    # IPO
    "listing_performance": "/v1/ipo/listing-performance/{symbol}/",
    # News, filings, calendar
    "corporate_actions_calendar": "/v1/idx/corporate-actions-calendar/",
    "filings": "/v1/idx/filings/",
    "news": "/v1/idx/news/",
    "suspensions": "/v1/idx/suspensions/",
    # Brokers
    "broker_registry": "/v1/brokers/",
    "top_brokers_daily": "/v1/idx/top-brokers/",
    "foreign_flow_daily": "/v1/idx/foreign-flow/",
    "net_foreign_inflow_symbol": "/v1/company/foreign-flow/{symbol}/",
    "broker_activity_symbol": "/v1/company/broker-activity/{symbol}/",
    "broker_activity_broker": "/v1/broker/activity/{broker}/",
    "top_buyers_sellers_symbol": "/v1/company/top-buyers-sellers/{symbol}/",
    "top_accum_distrib_broker": "/v1/broker/top-accumulation-distribution/{broker}/",
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
        path = ENDPOINTS[endpoint_key].format(**(path_params or {}))
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

    def get_news_tags(self) -> list[dict]:
        return self._get("news_tags")

    def get_companies_with_revenue_segments(self) -> dict:
        return self._get("companies_with_revenue_segments")

    def get_quarterly_dates_universe(self) -> dict:
        return self._get("quarterly_dates_universe")

    def get_broker_registry(self) -> list[dict]:
        return self._get("broker_registry")

    # -- Screener --------------------------------------------------------------

    def get_screener(
        self,
        where: dict | None = None,
        order_by: str = "symbol",
        limit: int = 200,
        offset: int = 0,
        desc: bool = False,
    ) -> dict:
        return self._get(
            "screener",
            **{
                "where": where or {},
                "order_by": order_by,
                "limit": limit,
                "offset": offset,
                "desc": desc,
                "include_query_values": True,
            },
        )

    def get_free_float(self, level: str, slug: str) -> dict:
        return self._get("free_float", level=level, slug=slug)

    # -- Company / reports ------------------------------------------------------

    def get_shareholders_composition(self, symbol: str) -> dict:
        return self._get("shareholders_composition", {"symbol": symbol})

    def get_company_report(self, symbol: str, sections: list[str]) -> dict:
        return self._get("company_report", {"symbol": symbol}, sections=",".join(sections))

    def get_revenue_segments(self, symbol: str, year: int | None = None) -> dict:
        return self._get("revenue_segments", {"symbol": symbol}, year=year)

    def get_quarterly_financials(self, symbol: str, report_date: str | None = None) -> dict:
        return self._get("quarterly_financials", {"symbol": symbol}, report_date=report_date)

    def get_subsector_report(self, subsector: str, sections: list[str]) -> dict:
        return self._get(
            "subsector_report", {"subsector": subsector}, sections=",".join(sections)
        )

    # -- Transaction data ---------------------------------------------------

    def get_daily_universe_close(self, trade_date: str) -> list[dict]:
        return self._get("daily_universe_close", date=trade_date)

    def get_market_summary(self, trade_date: str) -> dict:
        return self._get("market_summary", date=trade_date)

    def get_index_daily_close(self, trade_date: str) -> list[dict]:
        return self._get("index_daily_close", date=trade_date)

    # -- Rankings ------------------------------------------------------------

    def get_top_movers(self, classification: str, period: str) -> list[dict]:
        return self._get("top_movers", classification=classification, period=period)

    def get_most_traded(self, start: str, end: str) -> list[dict]:
        return self._get("most_traded", start=start, end=end)

    # -- IPO -------------------------------------------------------------------

    def get_listing_performance(self, symbol: str) -> dict:
        return self._get("listing_performance", {"symbol": symbol})

    # -- News, filings, calendar ----------------------------------------------

    def get_corporate_actions_calendar(self, start: str, end: str) -> list[dict]:
        return self._get("corporate_actions_calendar", start=start, end=end)

    def get_filings(self, trade_date: str) -> list[dict]:
        return self._get("filings", date=trade_date)

    def get_news(self, extension: str, trade_date: str) -> list[dict]:
        return self._get("news", extension=extension, date=trade_date)

    def get_suspensions(self, trade_date: str) -> list[dict]:
        return self._get("suspensions", date=trade_date)

    # -- Brokers ----------------------------------------------------------------

    def get_top_brokers_daily(self, trade_date: str) -> list[dict]:
        return self._get("top_brokers_daily", date=trade_date)

    def get_foreign_flow_daily(self, trade_date: str) -> list[dict]:
        return self._get("foreign_flow_daily", date=trade_date)

    def get_broker_activity_symbol(self, symbol: str, start: str, end: str) -> list[dict]:
        return self._get("broker_activity_symbol", {"symbol": symbol}, start=start, end=end)

    def get_broker_activity_broker(self, broker: str, start: str, end: str) -> list[dict]:
        return self._get("broker_activity_broker", {"broker": broker}, start=start, end=end)

    def get_top_buyers_sellers(self, symbol: str, start: str, end: str) -> dict:
        return self._get("top_buyers_sellers_symbol", {"symbol": symbol}, start=start, end=end)

    def get_top_accum_distrib(self, broker: str, start: str, end: str) -> dict:
        return self._get("top_accum_distrib_broker", {"broker": broker}, start=start, end=end)
