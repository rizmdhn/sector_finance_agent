"""Manual entry points for testing the data layer against a real Sectors API key,
without waiting on the scheduler.

Usage (run from the repo root, with .env variables exported into the shell):

    python scripts/manage.py init-db
    python scripts/manage.py run-job symbol_master
    python scripts/manage.py run-job universe_close
    python scripts/manage.py run-job quarterly_dates
    python scripts/manage.py get-report BBCA overview,valuation
    python scripts/manage.py get-price-history BBCA 1m
    python scripts/manage.py backfill-price BBCA
    python scripts/manage.py screen "sector='Financials'" --order-by=-market_cap
    python scripts/manage.py analyze-portfolio "BBCA=1000,BMRI=500" --cash=10000000
    python scripts/manage.py analyze-liquidity BBCA 50000000000
    python scripts/manage.py analyze-returns BBCA 1m
    python scripts/manage.py analyze-fundamentals BBCA

analyze-portfolio/liquidity/returns read only from Postgres (data/analysis_bridge.py)
— they never call the Sectors API, so they cost zero credits regardless of how often
they're run. analyze-fundamentals fetches the company report's overview+financials
sections (CACHE strategy): 1 credit per section on the first call for a given symbol,
free on every call after that until the symbol's data changes.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import analysis_bridge, repositories
from data.deps import get_cache, get_client, get_db
from ingest.jobs import quarterly_dates, symbol_master, universe_close

JOBS = {
    "symbol_master": symbol_master.run,
    "universe_close": universe_close.run,
    "quarterly_dates": quarterly_dates.run,
}


def cmd_init_db(_args) -> None:
    get_db().init_schema()
    print("schema applied")


def cmd_run_job(args) -> None:
    job = JOBS[args.name]
    job(get_db(), get_cache(), get_client())
    print(f"{args.name} done")


def cmd_get_report(args) -> None:
    sections = args.sections.split(",")
    result = repositories.get_company_report(get_cache(), get_db(), get_client(), args.symbol, sections)
    print(json.dumps(result, indent=2, default=str))


def cmd_get_price_history(args) -> None:
    rows = repositories.get_price_history(get_db(), args.symbol, args.period)
    print(json.dumps(rows, indent=2, default=str))


def cmd_backfill_price(args) -> None:
    repositories.ensure_price_detail(get_db(), get_client(), args.symbol)
    print(f"backfilled OHLCV/market_cap for {args.symbol}")


def cmd_screen(args) -> None:
    rows = repositories.screen_companies(get_cache(), get_client(), args.where, order_by=args.order_by)
    print(json.dumps(rows, indent=2, default=str))


def cmd_analyze_portfolio(args) -> None:
    positions = dict(item.split("=") for item in args.positions.split(","))
    positions = {symbol: float(shares) for symbol, shares in positions.items()}
    result = analysis_bridge.portfolio_snapshot(get_db(), positions, cash=args.cash)
    print(json.dumps(result, indent=2, default=str))


def cmd_analyze_liquidity(args) -> None:
    result = analysis_bridge.liquidity_snapshot(
        get_db(), args.symbol, position_value=args.position_value, participation_rate=args.participation_rate
    )
    print(json.dumps(result, indent=2, default=str))


def cmd_analyze_returns(args) -> None:
    result = analysis_bridge.returns_snapshot(get_db(), args.symbol, args.period)
    print(json.dumps(result, indent=2, default=str))


def cmd_analyze_fundamentals(args) -> None:
    result = analysis_bridge.fundamentals_snapshot(get_cache(), get_db(), get_client(), args.symbol)
    print(json.dumps(result, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Apply data/schema.sql to Postgres").set_defaults(func=cmd_init_db)

    p = sub.add_parser("run-job", help="Run one ingest job immediately")
    p.add_argument("name", choices=sorted(JOBS))
    p.set_defaults(func=cmd_run_job)

    p = sub.add_parser("get-report", help="Fetch/cache a company report (CACHE strategy)")
    p.add_argument("symbol")
    p.add_argument("sections", help="comma-separated, e.g. overview,valuation")
    p.set_defaults(func=cmd_get_report)

    p = sub.add_parser("get-price-history", help="Read price history from Postgres (requires universe_close ingest first)")
    p.add_argument("symbol")
    p.add_argument("period", choices=["1m", "3m", "1y"])
    p.set_defaults(func=cmd_get_price_history)

    p = sub.add_parser(
        "backfill-price", help="LAZY-ATOMIC: fill volume/market_cap for one symbol (the bulk close feed lacks them)"
    )
    p.add_argument("symbol")
    p.set_defaults(func=cmd_backfill_price)

    p = sub.add_parser("screen", help="Fetch/cache a screener query (requires symbol_master ingest first)")
    p.add_argument("where", help="SQL-like condition string, e.g. \"sector='Financials'\"")
    # A positional wouldn't work here: argparse treats a bare "-market_cap" value as
    # an unrecognized flag, since it starts with "-" (Sectors' own descending-sort
    # convention). --order-by=-market_cap (the "=" form) sidesteps that.
    p.add_argument("--order-by", dest="order_by", default="symbol", help='e.g. "-market_cap" for descending')
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser(
        "analyze-portfolio",
        help="Portfolio value/weights/concentration from ingested prices (analysis/portfolio.py, no API call)",
    )
    p.add_argument("positions", help='comma-separated symbol=shares, e.g. "BBCA=1000,BMRI=500"')
    p.add_argument("--cash", type=float, default=0.0)
    p.set_defaults(func=cmd_analyze_portfolio)

    p = sub.add_parser(
        "analyze-liquidity",
        help="ADV20 and normal exit days from ingested prices (analysis/liquidity.py, no API call)",
    )
    p.add_argument("symbol")
    p.add_argument("position_value", type=float)
    p.add_argument("--participation-rate", type=float, default=0.1, dest="participation_rate")
    p.set_defaults(func=cmd_analyze_liquidity)

    p = sub.add_parser(
        "analyze-returns",
        help="Price returns and drawdown from ingested prices (analysis/returns.py, no API call)",
    )
    p.add_argument("symbol")
    p.add_argument("period", choices=["1m", "3m", "1y"])
    p.set_defaults(func=cmd_analyze_returns)

    p = sub.add_parser(
        "analyze-fundamentals",
        help="Fundamentals/valuation from the company report's financials section "
        "(analysis/fundamentals.py + analysis/valuation.py; CACHE strategy, "
        "1 credit per section on first call per symbol, free after that)",
    )
    p.add_argument("symbol")
    p.set_defaults(func=cmd_analyze_fundamentals)

    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
