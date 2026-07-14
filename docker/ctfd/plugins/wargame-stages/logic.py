"""Framework-free stage rules. Kept pure so boundary behavior is easy to test."""
from dataclasses import dataclass


class InvalidTransition(ValueError):
    pass


def start_stage(state, started_at, now):
    if started_at is not None:
        return state, started_at, False
    if state != "pending":
        raise InvalidTransition(f"cannot start a stage in {state!r}")
    return "active", now, True


def lock_stage(state, locked_at, now):
    if state == "locked" and locked_at is not None:
        return state, locked_at, False
    if state != "active":
        raise InvalidTransition(f"cannot lock a stage in {state!r}")
    return "locked", now, True


def close_stage(state, locked_at, closed_at, now):
    if state == "closed" and closed_at is not None:
        return state, locked_at, closed_at, False
    if state not in ("active", "locked"):
        raise InvalidTransition(f"cannot close a stage in {state!r}")
    return "closed", locked_at or now, now, True


def solve_is_scoring(solved_at, started_at, cutoff=None):
    return started_at is not None and solved_at >= started_at and (cutoff is None or solved_at <= cutoff)


def safe_csv_cell(value):
    """Prevent spreadsheet programs from treating untrusted names as formulas."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


@dataclass(frozen=True)
class SolveEvent:
    account_id: int
    name: str
    points: int
    solved_at: object


def rank_solves(events, started_at, cutoff=None):
    totals = {}
    for event in events:
        if not solve_is_scoring(event.solved_at, started_at, cutoff):
            continue
        row = totals.setdefault(event.account_id, {
            "account_id": event.account_id, "name": event.name,
            "score": 0, "solve_count": 0, "last_solve_at": event.solved_at,
        })
        row["score"] += event.points
        row["solve_count"] += 1
        row["last_solve_at"] = max(row["last_solve_at"], event.solved_at)
    rows = list(totals.values())
    for row in rows:
        row["elapsed_seconds"] = int((row["last_solve_at"] - started_at).total_seconds())
    rows.sort(key=lambda row: (-row["score"], row["elapsed_seconds"], row["name"].casefold(), row["account_id"]))
    for place, row in enumerate(rows, 1):
        row["place"] = place
    return rows
