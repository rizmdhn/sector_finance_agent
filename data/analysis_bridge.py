"""I/O adapters wiring `analysis/`'s pure functions to real ingested/cached data.

`analysis/` itself has no I/O (see its module docstrings) — this is the only module
that reads Postgres and feeds the results into `analysis/*.py`. It never calls
`SectorsClient` directly: every value here comes from data already ingested by
`ingest/jobs/*.py` or backfilled via `data/repositories.py::ensure_price_detail`, so
calling any function here never costs a Sectors API credit — it only reads what has
already been paid for.

Scope note: only portfolio value/weights/concentration, returns/drawdown, and
liquidity are wired here, because those map onto `price_daily` (already ingested).
Fundamentals and raw-component valuation (analysis/fundamentals.py,
analysis/valuation.py's pe()/pb()/fcff()/fcfe()) need the company report's
`financials` section, which has not been fetched for any symbol yet (see gap
analysis doc G4) — wiring those is deferred until that pull is deliberately budgeted,
rather than fetched speculatively here.
"""

from datetime import date, timedelta

from analysis import liquidity, portfolio, returns
from analysis.types import UNAVAILABLE, Number, is_missing
from data.db import Database
from data.repositories import PERIOD_TO_DAYS, ensure_valid_symbol

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
