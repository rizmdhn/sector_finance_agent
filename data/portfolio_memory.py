"""The user's current holdings, kept as ONE structured memory record that is replaced
(never appended to) whenever they say it changed — "I hold 100 BBCA", then later "I
bought 50 more". Without this the agent saved a new free-text fact each time, so old
and new holdings both looked current and nothing could be summed.

The record is a normal `user_memory` row (kind `portfolio`, so it shows in the Memory
screen) whose metadata holds the machine-readable `positions`. Editing its text in the
Memory screen does not change `positions` — holdings change by telling the agent.
"""

from data.canonical import idx_today
from data.db import Database
from data.repositories import ensure_valid_symbol


def get_portfolio(db: Database, user_id: str) -> dict | None:
    row = db.get_active_portfolio(user_id)
    if row is None:
        return None
    meta = row["metadata"]
    return {"positions": meta["positions"], "cash": meta.get("cash"), "as_of": meta["as_of"]}


def _save(db: Database, user_id: str, positions: dict[str, float], cash: float | None) -> dict:
    as_of = idx_today().isoformat()
    parts = [f"{symbol} {shares:,.0f} shares" for symbol, shares in sorted(positions.items())]
    if cash is not None:
        parts.append(f"cash IDR {cash:,.0f}")
    content = f"Holdings as of {as_of}: " + ("; ".join(parts) if parts else "none")
    db.replace_portfolio(user_id, content, {"kind": "portfolio", "positions": positions, "cash": cash, "as_of": as_of})
    return {"positions": positions, "cash": cash, "as_of": as_of}


def set_portfolio(db: Database, user_id: str, positions: dict[str, float], cash: float | None = None) -> dict:
    """Replace the whole portfolio. `cash` left out keeps the previously saved cash."""
    clean: dict[str, float] = {}
    for symbol, shares in positions.items():
        if shares < 0:
            raise ValueError(f"{symbol}: shares can't be negative")
        if shares > 0:
            clean[ensure_valid_symbol(db, symbol)] = float(shares)
    previous = get_portfolio(db, user_id)
    return _save(db, user_id, clean, cash if cash is not None else (previous or {}).get("cash"))


def record_trades(db: Database, user_id: str, trades: dict[str, float]) -> dict:
    """Apply buys (positive) and sells (negative) to the saved portfolio. Cash is not
    adjusted — a trade's price isn't known here."""
    current = get_portfolio(db, user_id)
    positions = dict((current or {}).get("positions", {}))
    for symbol, change in trades.items():
        canonical = ensure_valid_symbol(db, symbol)
        new = positions.get(canonical, 0.0) + change
        if new < -1e-9:
            raise ValueError(
                f"selling {-change:,.0f} {canonical} but only {positions.get(canonical, 0.0):,.0f} are saved — "
                "ask the user for their real holdings and use set_portfolio"
            )
        if new > 1e-9:
            positions[canonical] = new
        else:
            positions.pop(canonical, None)
    return _save(db, user_id, positions, (current or {}).get("cash"))
