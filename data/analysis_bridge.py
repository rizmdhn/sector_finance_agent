"""I/O adapters wiring `analysis/`'s pure functions to real ingested/cached data.

`analysis/` itself has no I/O (see its module docstrings) — this is the only module
that reads real data and feeds it into `analysis/*.py`.

Two cost tiers here:
  - `portfolio_snapshot`, `liquidity_snapshot`, `returns_snapshot` read only Postgres
    (`price_daily`, already ingested by `ingest/jobs/*.py` /
    `data/repositories.py::ensure_price_detail`) and never call `SectorsClient` —
    zero Sectors credits, however often they're called.
  - `fundamentals_snapshot` calls `data/repositories.py::get_company_report` for the
    `financials` (and `overview`) sections, which is CACHE-strategy: 1 credit per
    section on the first call for a given symbol, free (Valkey cache hit) on every
    call after that until the symbol's data actually changes. Call it deliberately,
    not in a loop over many symbols, unless the credit cost is budgeted first.
"""

from datetime import date, timedelta

from analysis import fundamentals, liquidity, portfolio, returns, valuation
from analysis.types import UNAVAILABLE, Number, is_missing
from data.cache import Cache
from data.db import Database
from data.repositories import PERIOD_TO_DAYS, ensure_valid_symbol, get_company_report
from data.sectors_client import SectorsClient

PRICE_LABEL_CAVEAT = (
    "price return, not total return — dividend/split adjustment convention for "
    "Sectors' price series is unconfirmed (gap analysis doc G5)"
)


def portfolio_snapshot(db: Database, positions: dict[str, float], cash: float) -> dict:
    """`positions`: symbol -> shares held. Prices come from the latest ingested
    close per symbol (`price_daily`). A symbol with no ingested price is reported in
    `missing_price_symbols` rather than silently dropped or treated as zero, per
    Appendix A's "missing prices for material positions block full-portfolio weights."
    """
    position_values: dict[str, Number] = {}
    missing_price_symbols: list[str] = []

    for symbol, shares in positions.items():
        canonical = ensure_valid_symbol(db, symbol)
        latest = db.get_latest_close(canonical)
        price: Number = float(latest[1]) if latest is not None else UNAVAILABLE
        value = portfolio.position_value(shares, price)
        position_values[canonical] = value
        if is_missing(value):
            missing_price_symbols.append(canonical)

    total = portfolio.portfolio_value(position_values.values(), cash)
    weights = {symbol: portfolio.weight(v, total) for symbol, v in position_values.items()}

    numeric_weights = [w for w in weights.values() if not is_missing(w)]
    hhi_value: Number = portfolio.hhi(numeric_weights) if numeric_weights else UNAVAILABLE
    effective_holdings: Number = (
        portfolio.effective_number_of_holdings(hhi_value) if not is_missing(hhi_value) else UNAVAILABLE
    )

    return {
        "position_values": position_values,
        "portfolio_value": total,
        "weights": weights,
        "hhi": hhi_value,
        "effective_number_of_holdings": effective_holdings,
        "missing_price_symbols": missing_price_symbols,
    }


def liquidity_snapshot(
    db: Database,
    symbol: str,
    position_value: Number,
    participation_rate: float = 0.1,
    sessions: int = 20,
) -> dict:
    """ADV20 (20-session median traded value) and normal-conditions exit days for
    one symbol, from ingested `price_daily` volume/close. Returns `UNAVAILABLE` for
    `adv20`/`normal_exit_days` if fewer than `sessions` sessions have been ingested
    yet or any session in the window has a null close/volume — see
    `analysis/liquidity.py::adv20`'s no-silent-gap-filling rule.
    """
    canonical = ensure_valid_symbol(db, symbol)
    end = db.latest_trade_date() or date.today()
    start = end - timedelta(days=sessions * 2)  # calendar-day pad for weekends/holidays
    rows = db.read_price_range(canonical, start, end)[-sessions:]

    daily_traded_values = [
        (float(row["close"]) * float(row["volume"]))
        if row["close"] is not None and row["volume"] is not None
        else None
        for row in rows
    ]
    adv20_value = liquidity.adv20(daily_traded_values)
    exit_days = liquidity.normal_exit_days(position_value, adv20_value, participation_rate)

    return {
        "symbol": canonical,
        "sessions_used": len(rows),
        "sessions_requested": sessions,
        "adv20": adv20_value,
        "normal_exit_days": exit_days,
    }


def returns_snapshot(db: Database, symbol: str, period: str) -> dict:
    """Day-over-day price returns and drawdown from ingested `price_daily` closes.
    Labeled as price returns, not total returns, per gap analysis doc G5.
    """
    canonical = ensure_valid_symbol(db, symbol)
    days = PERIOD_TO_DAYS.get(period)
    if days is None:
        raise ValueError(f"unknown period: {period}")
    end = db.latest_trade_date() or date.today()
    start = end - timedelta(days=days)
    rows = db.read_price_range(canonical, start, end)

    closes = [float(row["close"]) if row["close"] is not None else None for row in rows]
    period_returns = [returns.simple_return(closes[i], closes[i - 1]) for i in range(1, len(closes))]
    clean_returns = [r for r in period_returns if not is_missing(r)]

    wealth_index = returns.cash_flow_adjusted_wealth_index(clean_returns) if clean_returns else []
    max_dd: Number = returns.max_drawdown(wealth_index) if wealth_index else UNAVAILABLE

    return {
        "symbol": canonical,
        "sessions_used": len(rows),
        "period_returns": period_returns,
        "max_drawdown": max_dd,
        "label": PRICE_LABEL_CAVEAT,
    }


def _num(value) -> float | None:
    """Passes numeric values through; None stays None (analysis/*.py's is_missing
    already treats None as missing, so no sentinel translation is needed here) —
    normalizes only against non-numeric junk (e.g. a stray string) from the API."""
    return value if isinstance(value, (int, float)) else None


def _avg(a, b) -> float | None:
    a, b = _num(a), _num(b)
    return (a + b) / 2 if a is not None and b is not None else None


def _extract_npl(industry_breakdown) -> float | None:
    """`non_performing_loans` has no stable named field — it only appears inside
    `industry_breakdown.loan_at_risk`, keyed by a human-readable label. Matched by
    substring since the exact label casing/wording is not a documented contract.
    Returns None (not just for banks) if the label isn't found, which correctly
    propagates to UNAVAILABLE in analysis/fundamentals.py::gross_npl_ratio.
    """
    if not isinstance(industry_breakdown, dict):
        return None
    loan_at_risk = industry_breakdown.get("loan_at_risk")
    if not isinstance(loan_at_risk, dict):
        return None
    for label, value in loan_at_risk.items():
        if "non-performing" in label.lower() or "npl" in label.lower():
            return _num(value)
    return None


def fundamentals_snapshot(cache: Cache, db: Database, client: SectorsClient, symbol: str) -> dict:
    """Fundamentals and raw-component valuation from the company report's `overview`
    and `financials` sections (CACHE strategy — see this module's docstring for the
    credit cost). Uses the latest fiscal year in `historical_financials`; ratios
    needing an average (ROA, ROE) additionally need the prior year and are
    `UNAVAILABLE` if only one year is present.

    Bank-specific ratios (`bank` key, non-None only when loan/deposit/NII fields are
    present) approximate a few inputs Sectors does not expose as named fields —
    documented per-field in the returned `bank._provenance`, per Appendix A's
    requirement that every adjustment carry a reason. `non_performing_loans` is
    parsed from a human-readable label inside `industry_breakdown`, not a stable
    field — verify it before treating gross_npl_ratio as reliable for any bank
    other than the one this was built against (BBCA).
    """
    symbol = ensure_valid_symbol(db, symbol)
    report = get_company_report(cache, db, client, symbol, ["overview", "financials"])
    overview = report.get("overview") or {}
    financials_data = report.get("financials") or {}
    history = financials_data.get("historical_financials") or []

    if not history:
        return {
            "symbol": symbol,
            "fiscal_year": None,
            "is_bank": False,
            "general": {},
            "bank": None,
            "valuation": {},
            "note": "company report returned no historical_financials for this symbol",
        }

    latest = history[-1]
    prior = history[-2] if len(history) >= 2 else None
    fiscal_year = latest.get("year")

    revenue_t = _num(latest.get("revenue"))
    revenue_prior = _num(prior.get("revenue")) if prior else None
    ebit = _num(latest.get("ebit"))
    ebitda = _num(latest.get("ebitda"))
    net_income = _num(latest.get("earnings"))
    operating_pnl = _num(latest.get("operating_pnl"))
    tax = _num(latest.get("tax"))
    ebt = _num(latest.get("earnings_before_tax"))
    tax_rate = tax / ebt if tax is not None and ebt else None

    average_total_assets = _avg(latest.get("total_assets"), prior.get("total_assets") if prior else None)
    average_common_equity = _avg(latest.get("total_equity"), prior.get("total_equity") if prior else None)

    interest_bearing_debt = _num(latest.get("total_debt"))
    cash_and_equivalents = _num(latest.get("cash_and_equivalents")) or _num(latest.get("cash_only"))
    interest_expense = _num(latest.get("interest_expense"))
    operating_cash_flow = _num(latest.get("operating_cash_flow"))

    net_debt_value = fundamentals.net_debt(interest_bearing_debt, cash_and_equivalents)

    general = {
        "revenue_growth": fundamentals.revenue_growth(revenue_t, revenue_prior),
        "operating_margin": fundamentals.operating_margin(operating_pnl, revenue_t),
        "net_margin": fundamentals.net_margin(net_income, revenue_t),
        "roa": fundamentals.roa(net_income, average_total_assets),
        "roe": fundamentals.roe(net_income, average_common_equity),
        "net_debt": net_debt_value,
        "net_debt_to_ebitda": fundamentals.net_debt_to_ebitda(net_debt_value, ebitda),
        "interest_coverage": fundamentals.interest_coverage(ebit, interest_expense),
        "cfo_margin": fundamentals.cfo_margin(operating_cash_flow, revenue_t),
        "cash_conversion": fundamentals.cash_conversion(operating_cash_flow, net_income),
    }

    is_bank = any(
        latest.get(k) is not None for k in ("gross_loan", "total_deposit", "net_interest_income")
    )
    bank = None
    if is_bank:
        gross_loans = _num(latest.get("gross_loan"))
        net_loans = _num(latest.get("net_loan"))
        deposits = _num(latest.get("total_deposit"))
        net_interest_income = _num(latest.get("net_interest_income"))
        non_interest_income = _num(latest.get("non_interest_income"))
        # Despite the name, `non_loan_earning_assets` alone (not summed with loans)
        # exactly reproduces Sectors' own reported net_interest_margin for BBCA
        # (net_interest_income / non_loan_earning_assets == Sectors' reported NIM to
        # 6 decimal places) — treated as the effective earning-asset base for NIM.
        average_earning_assets = _num(latest.get("non_loan_earning_assets"))
        allowance = _num(latest.get("allowance_for_loans"))
        applicable_loan_loss_reserves = abs(allowance) if allowance is not None else None
        non_performing_loans = _extract_npl(latest.get("industry_breakdown"))
        operating_expense = _num(latest.get("operating_expense"))
        operating_income = (
            net_interest_income + non_interest_income
            if net_interest_income is not None and non_interest_income is not None
            else None
        )
        tier1 = _num(latest.get("core_capital_tier1"))
        tier2 = _num(latest.get("supplementary_capital_tier2"))
        eligible_capital = tier1 + tier2 if tier1 is not None and tier2 is not None else tier1
        rwa = _num(latest.get("total_risk_weighted_asset"))

        bank = {
            "nim": fundamentals.nim(net_interest_income, average_earning_assets),
            "gross_npl_ratio": fundamentals.gross_npl_ratio(non_performing_loans, gross_loans),
            "loan_loss_coverage": fundamentals.loan_loss_coverage(
                applicable_loan_loss_reserves, non_performing_loans
            ),
            "loan_to_deposit": fundamentals.loan_to_deposit(net_loans, deposits),
            "cost_to_income": fundamentals.cost_to_income(operating_expense, operating_income),
            "capital_adequacy": fundamentals.capital_adequacy(eligible_capital, rwa),
            "_provenance": {
                "average_earning_assets": "non_loan_earning_assets alone, despite its name — "
                "matches Sectors' own reported net_interest_margin for BBCA to 6 decimal places",
                "loan_to_deposit_numerator": "net_loan, not gross_loan — matches Sectors' own "
                "reported loan_to_deposit_ratio for BBCA exactly",
                "operating_income": "approximated as net_interest_income + non_interest_income",
                "non_performing_loans": "parsed from industry_breakdown.loan_at_risk by label match "
                "(no stable field name) — verify before trusting for symbols other than BBCA",
                "roe_convention": "uses average common equity per Appendix A, so it will differ "
                "from Sectors' own reported ROE (which appears to use ending equity only)",
                "eligible_regulatory_capital": "core_capital_tier1 + supplementary_capital_tier2 "
                "(falls back to tier1 alone if tier2 is absent)",
            },
        }

    market_cap = _num(overview.get("market_cap"))
    total_equity_t = _num(latest.get("total_equity"))
    debt = _num(latest.get("total_debt"))

    d_and_a = ebitda - ebit if ebitda is not None and ebit is not None else None
    capex = _num(latest.get("realized_capital_goods_investment"))
    net_borrowing = (
        _num(latest.get("total_debt")) - _num(prior.get("total_debt"))
        if prior and latest.get("total_debt") is not None and prior.get("total_debt") is not None
        else None
    )
    # No field for change-in-operating-working-capital exists in historical_financials
    # at all (bank or otherwise) — always UNAVAILABLE here, which correctly makes
    # FCFF/FCFE UNAVAILABLE rather than approximated from an unrelated figure.
    change_in_operating_nwc = None

    ev = valuation.enterprise_value(market_cap, debt, 0.0, 0.0, cash_and_equivalents)

    valuation_snapshot = {
        "pe": valuation.pe(market_cap, net_income),
        "pb": valuation.pb(market_cap, total_equity_t),
        "enterprise_value": ev,
        "ev_to_ebitda": valuation.ev_to_ebitda(ev, ebitda),
        "fcff": valuation.fcff(ebit, tax_rate, d_and_a, capex, change_in_operating_nwc),
        "fcfe": valuation.fcfe(net_income, d_and_a, capex, change_in_operating_nwc, net_borrowing),
        "_provenance": {
            "enterprise_value": "preferred_equity and noncontrolling_interests assumed 0.0 — "
            "Sectors does not break these out separately",
            "fcff_fcfe": "change_in_operating_nwc has no source field in historical_financials; "
            "always UNAVAILABLE rather than approximated, which correctly makes FCFF/FCFE UNAVAILABLE",
        },
    }

    return {
        "symbol": symbol,
        "fiscal_year": fiscal_year,
        "is_bank": is_bank,
        "general": general,
        "bank": bank,
        "valuation": valuation_snapshot,
    }
