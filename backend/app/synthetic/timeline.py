"""Shared synthetic timeline so every generator module (demand, execution,
capacity, state snapshots) agrees on the same weekly calendar.

REFERENCE_DATE is the fictional "as of" date for the synthetic plant's
history — not tied to the real calendar date the generator happens to run on,
so the dataset is reproducible regardless of when `docker compose up` is run.
"""
from __future__ import annotations

import datetime as dt

from app.config import settings

REFERENCE_DATE = dt.date(2026, 6, 1)  # a Monday

HISTORY_START = REFERENCE_DATE - dt.timedelta(weeks=settings.history_weeks)
FORECAST_SNAPSHOT_START = REFERENCE_DATE - dt.timedelta(weeks=settings.forecast_snapshot_weeks + 5)
FUTURE_END = REFERENCE_DATE + dt.timedelta(weeks=settings.future_horizon_weeks)


def week_starts(start: dt.date, end: dt.date) -> list[dt.date]:
    """All Monday-aligned week-start dates in [start, end)."""
    # Align `start` to the Monday on or before it.
    aligned = start - dt.timedelta(days=start.weekday())
    weeks = []
    cur = aligned
    while cur < end:
        weeks.append(cur)
        cur += dt.timedelta(weeks=1)
    return weeks


ALL_DELIVERY_WEEKS = week_starts(HISTORY_START, FUTURE_END)
FORECAST_SNAPSHOT_WEEKS = week_starts(FORECAST_SNAPSHOT_START, REFERENCE_DATE + dt.timedelta(weeks=5))
CAPACITY_CALENDAR_WEEKS = week_starts(HISTORY_START, FUTURE_END)
