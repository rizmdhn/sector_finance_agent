"""Credit-safe checks for the ingest worker, runnable inside its container:

    docker compose exec ingest-worker python -m ingest.cli status   # zero credits
    docker compose exec ingest-worker python -m ingest.cli seed     # ticker list only
"""

import sys

from data.deps import get_cache, get_client, get_db
from ingest.jobs import symbol_master


def status() -> None:
    db = get_db()
    for table, count in db.table_counts().items():
        print(f"{table:16} {count} rows")
    print(f"latest_trade_date {db.latest_trade_date()}")
    print(f"BBCA.JK valid     {db.is_valid_symbol('BBCA.JK')}")


def seed() -> None:
    """Pages already paid for are memoized in Valkey for an hour, so re-running after
    a 429 only buys what's missing."""
    print(f"wrote {symbol_master.run_essential(get_db(), get_client(), get_cache())} symbols")
    status()


if __name__ == "__main__":
    commands = {"status": status, "seed": seed}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(f"usage: python -m ingest.cli [{'|'.join(commands)}]")
    commands[sys.argv[1]]()
