"""Manual entry points for testing the data layer against a real Sectors API key,
without waiting on the scheduler.

Usage (run from the repo root, with .env variables exported into the shell):

    python scripts/manage.py init-db
    python scripts/manage.py run-job symbol_master
    python scripts/manage.py run-job universe_close
    python scripts/manage.py run-job quarterly_dates
    python scripts/manage.py get-report BBCA overview,valuation
    python scripts/manage.py get-price-history BBCA 1m
    python scripts/manage.py get-movers gainers 1d
    python scripts/manage.py screen '{"sector": "Banks"}' market_cap
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import repositories
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


def cmd_get_movers(args) -> None:
    rows = repositories.get_market_movers(get_cache(), get_client(), args.classification, args.period)
    print(json.dumps(rows, indent=2, default=str))


def cmd_screen(args) -> None:
    where = json.loads(args.where)
    rows = repositories.screen_companies(get_cache(), get_client(), where, order_by=args.order_by)
    print(json.dumps(rows, indent=2, default=str))


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

    p = sub.add_parser("get-movers", help="Fetch/cache top market movers")
    p.add_argument("classification")
    p.add_argument("period")
    p.set_defaults(func=cmd_get_movers)

    p = sub.add_parser("screen", help="Fetch/cache a screener query (requires symbol_master ingest first)")
    p.add_argument("where", help='JSON object, e.g. \'{"sector": "Banks"}\'')
    p.add_argument("order_by", nargs="?", default="symbol")
    p.set_defaults(func=cmd_screen)

    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
