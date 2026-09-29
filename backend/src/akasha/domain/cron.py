"""Cron expression parsing, in one place. Publish-time validation and the
scheduler share it, so a published schedule is always one the scheduler can
evaluate.

Dialect: croniter's 5-field syntax (minute hour day-of-month month
day-of-week) with numbers, ranges, steps, lists, month/day names, ``L`` and
``#``. Seconds and year fields are rejected. croniter's hashed and random
values (``H``, ``R``) are rejected too, because schedules must be
deterministic (AGENTS §72). Parsing is delegated to croniter: hand-rolled
cron is a bug farm. Datetimes are timezone-aware UTC.
"""

import re
from datetime import UTC, datetime

from croniter import CroniterError, croniter

# Fixed probe start: "can this expression ever fire?" must not depend on
# when the check runs. Cron has no year field, so any start works.
_PROBE_START = datetime(2000, 1, 1, tzinfo=UTC)
# A token that is croniter's H or R, alone or as in H(0-29) or R/10. Month
# and day names never start with h or r, so they are unaffected.
_NONDETERMINISTIC = re.compile(r"(?:^|[\s,])[hr](?:$|[\s,(/])", re.IGNORECASE)


def cron_problem(expr: str) -> str | None:
    """Why ``expr`` is not a usable schedule, or None when it is.

    Messages never echo the expression: validation issues carry locations,
    never config values.
    """
    if len(expr.split()) != 5:
        return (
            "cron expression must have exactly 5 fields (minute hour day month weekday)"
        )
    if _NONDETERMINISTIC.search(expr):
        return (
            "cron expression must be deterministic:"
            " hashed/random values (H, R) are not supported"
        )
    try:
        next_fire(expr, _PROBE_START)
    except CroniterError:  # malformed, or it can never fire (e.g. Feb 31)
        return "cron expression is invalid or never fires"
    return None


def next_fire(expr: str, after: datetime) -> datetime:
    """The first fire time strictly after ``after`` (UTC-aware)."""
    # croniter is untyped; the annotation pins the return down for mypy.
    result: datetime = croniter(expr, after.astimezone(UTC)).get_next(datetime)
    return result
