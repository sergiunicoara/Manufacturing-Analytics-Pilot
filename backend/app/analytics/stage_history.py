"""Recorded operation elapsed time from production_order_operations actuals.

Terminology is deliberate. `actual_finish - actual_start` is *recorded elapsed
time*, not productive processing time. The interval between one operation's
finish and the next operation's start is an *inter-operation elapsed gap*, not
proven queue time. Neither is decomposed into the modelled processing / queue /
transfer components of `leadtime.py`; the source events do not support that.

Every operation lands in exactly one record class, so nothing unfinished or
invalid is silently counted as a completed, zero-duration observation.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analytics.blocking import BlockingIndex

COMPLETED_VALID = "COMPLETED_VALID"
COMPLETED_MISSING_START = "COMPLETED_MISSING_START"
COMPLETED_MISSING_FINISH = "COMPLETED_MISSING_FINISH"
INVALID_ORDERING = "INVALID_ORDERING"
STATUS_CONFLICT = "STATUS_CONFLICT"
DUPLICATE = "DUPLICATE"
DQ_EXCLUDED = "DQ_EXCLUDED"
OPEN_STARTED = "OPEN_STARTED"
NOT_STARTED = "NOT_STARTED"
OUTSIDE_WINDOW = "OUTSIDE_WINDOW"

RECORD_CLASSES = (COMPLETED_VALID, COMPLETED_MISSING_START, COMPLETED_MISSING_FINISH,
                  INVALID_ORDERING, STATUS_CONFLICT, DUPLICATE, DQ_EXCLUDED,
                  OPEN_STARTED, NOT_STARTED, OUTSIDE_WINDOW)

COMPLETED_STATUSES = frozenset({"COMPLETED"})
PERCENTILES = (0.5, 0.85, 0.95)
TIMEZONE_ASSUMPTION = ("Timestamps are naive plant-local times; no timezone or daylight-saving "
                       "conversion is applied.")


@dataclass(frozen=True)
class ObservationWindow:
    """Completed operations are observed by actual_finish in [start, end)."""
    start: dt.date | None = None
    end: dt.date | None = None

    def contains(self, finish: pd.Series) -> pd.Series:
        mask = pd.Series(True, index=finish.index)
        if self.start is not None:
            mask &= finish >= pd.Timestamp(self.start)
        if self.end is not None:
            mask &= finish < pd.Timestamp(self.end)
        return mask

    def label(self) -> str:
        return f"actual_finish in [{self.start or '-inf'}, {self.end or '+inf'})"


def timestamp_resolution_hours(ops: pd.DataFrame) -> float | None:
    """Coarsest resolution consistent with every recorded actual timestamp."""
    stamps = pd.concat([ops["actual_start"], ops["actual_finish"]]).dropna()
    if stamps.empty:
        return None
    stamps = pd.to_datetime(stamps)
    if (stamps == stamps.dt.normalize()).all():
        return 24.0
    if (stamps.dt.second == 0).all() and (stamps.dt.minute == 0).all():
        return 1.0
    return None


def timestamp_resolution_mix(ops: pd.DataFrame) -> dict[str, int]:
    """Operations whose recorded start and finish are both at midnight (day resolution) versus
    carrying a time of day (sub-day resolution). Mixed sources are common: MES bookings vs. manual."""
    both = ops.loc[ops["actual_start"].notna() & ops["actual_finish"].notna()]
    start, finish = pd.to_datetime(both["actual_start"]), pd.to_datetime(both["actual_finish"])
    day = (start == start.dt.normalize()) & (finish == finish.dt.normalize())
    return {"sub_day": int((~day).sum()), "day": int(day.sum())}


def resolution_assumption(mix: dict[str, int]) -> str:
    if mix["day"] and not mix["sub_day"]:
        return ("Timestamps are recorded at day resolution, so an elapsed value of 0 means same-day start and "
                "finish, not zero effort.")
    if mix["day"]:
        return (f"Timestamp resolution is mixed: {mix['sub_day']} operations carry a time of day and {mix['day']} "
                "are recorded at day resolution only (whole-day elapsed values); both are included.")
    return "Timestamps carry a time of day (minute resolution)."


def classify_operations(ops: pd.DataFrame, production_orders: pd.DataFrame,
                        blocking: BlockingIndex | None = None,
                        window: ObservationWindow = ObservationWindow()) -> pd.DataFrame:
    """One row per operation with `record_class`, `elapsed_hours` (valid only)
    and the joined order dimensions (item_id, site_id)."""
    frame = ops.copy()
    frame["actual_start"] = pd.to_datetime(frame["actual_start"])
    frame["actual_finish"] = pd.to_datetime(frame["actual_finish"])
    orders = production_orders[["production_order_id", "item_id", "site_id"]]
    frame = frame.merge(orders, on="production_order_id", how="left")

    has_start = frame["actual_start"].notna()
    has_finish = frame["actual_finish"].notna()
    completed = frame["status"].isin(COMPLETED_STATUSES)
    duplicate = frame.duplicated(["production_order_id", "seq_no"], keep="first")
    blocked_orders = set()
    if blocking is not None:
        blocked_orders = {int(entity_id) for (entity_type, entity_id) in blocking.entity_blocked
                          if entity_type == "production_order"}
    dq_excluded = frame["production_order_id"].isin(blocked_orders)

    record_class = np.select(
        [duplicate,
         dq_excluded,
         has_start & has_finish & (frame["actual_finish"] < frame["actual_start"]),
         ~completed & has_finish,
         completed & ~has_start,
         completed & ~has_finish,
         ~completed & has_start,
         ~completed],
        [DUPLICATE, DQ_EXCLUDED, INVALID_ORDERING, STATUS_CONFLICT,
         COMPLETED_MISSING_START, COMPLETED_MISSING_FINISH, OPEN_STARTED, NOT_STARTED],
        default=COMPLETED_VALID,
    )
    frame["record_class"] = record_class
    valid = frame["record_class"] == COMPLETED_VALID
    in_window = window.contains(frame["actual_finish"])
    frame.loc[valid & ~in_window, "record_class"] = OUTSIDE_WINDOW
    valid = frame["record_class"] == COMPLETED_VALID
    elapsed = (frame["actual_finish"] - frame["actual_start"]).dt.total_seconds() / 3600.0
    frame["elapsed_hours"] = elapsed.where(valid)
    frame["finish_week"] = (frame["actual_finish"] - pd.to_timedelta(frame["actual_finish"].dt.weekday, unit="D")).dt.normalize()
    return frame


def record_class_counts(classified: pd.DataFrame) -> dict[str, int]:
    counts = classified["record_class"].value_counts()
    return {name: int(counts.get(name, 0)) for name in RECORD_CLASSES}


def summarize_elapsed(classified: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Count, median and upper percentiles of recorded elapsed hours per group.

    Coverage = valid completed observations / every operation in the group
    whose status says COMPLETED or that carries a finish timestamp (the
    population that *should* have produced a usable observation)."""
    eligible = classified.loc[classified["status"].isin(COMPLETED_STATUSES)
                              | classified["actual_finish"].notna()]
    eligible = eligible.loc[eligible["record_class"] != OUTSIDE_WINDOW]
    valid = eligible.loc[eligible["record_class"] == COMPLETED_VALID]
    grouped = valid.groupby(by, dropna=False)["elapsed_hours"]
    stats = grouped.agg(n_observations="count", mean_hours="mean").reset_index()
    for q in PERCENTILES:
        column = f"p{int(q * 100)}_hours"
        stats = stats.merge(grouped.quantile(q).rename(column).reset_index(), on=by, how="left")
    stats = stats.merge(grouped.apply(lambda s: int((s == 0).sum())).rename("n_zero_elapsed").reset_index(), on=by, how="left")
    population = eligible.groupby(by, dropna=False).size().rename("n_eligible").reset_index()
    stats = population.merge(stats, on=by, how="left")
    stats["n_observations"] = stats["n_observations"].fillna(0).astype(int)
    stats["n_zero_elapsed"] = stats["n_zero_elapsed"].fillna(0).astype(int)
    stats["coverage_pct"] = (100.0 * stats["n_observations"] / stats["n_eligible"]).round(1)
    return stats


def inter_operation_gaps(classified: pd.DataFrame) -> pd.DataFrame:
    """Gap between consecutive *valid* operations of the same production order.

    Negative gaps are overlaps and are labelled as such; they are never
    clamped to zero or reinterpreted as a plausible queue."""
    valid = classified.loc[classified["record_class"] == COMPLETED_VALID].sort_values(
        ["production_order_id", "seq_no"])
    nxt = valid.groupby("production_order_id").shift(-1)
    gaps = pd.DataFrame({
        "production_order_id": valid["production_order_id"],
        "from_po_operation_id": valid["po_operation_id"],
        "to_po_operation_id": nxt["po_operation_id"],
        "from_work_centre_id": valid["work_centre_id"],
        "to_work_centre_id": nxt["work_centre_id"],
        "item_id": valid["item_id"],
        "site_id": valid["site_id"],
        "gap_hours": (nxt["actual_start"] - valid["actual_finish"]).dt.total_seconds() / 3600.0,
    }).dropna(subset=["to_po_operation_id"])
    gaps["to_po_operation_id"] = gaps["to_po_operation_id"].astype(int)
    gaps["to_work_centre_id"] = gaps["to_work_centre_id"].astype(int)
    gaps["gap_class"] = np.where(gaps["gap_hours"] < 0, "OVERLAP", "NON_NEGATIVE_GAP")
    return gaps.reset_index(drop=True)


def standard_processing_hours(classified: pd.DataFrame, routing_operations: pd.DataFrame,
                              production_orders: pd.DataFrame) -> pd.Series:
    """Routing-standard processing hours for each operation's order quantity:
    (setup × ceil(qty / batch_size) + run × qty / yield) / 60. NaN when the
    routing standard is missing — never a silent zero."""
    routing = routing_operations.set_index("routing_operation_id")[
        ["setup_time_minutes", "run_time_minutes_per_unit", "yield_pct", "batch_size"]]
    qty = production_orders.set_index("production_order_id")["qty"].astype(float)
    frame = classified[["routing_operation_id", "production_order_id"]].join(routing, on="routing_operation_id")
    frame["qty"] = frame["production_order_id"].map(qty)
    batches = np.ceil(frame["qty"] / frame["batch_size"].astype(float).clip(lower=1))
    yield_pct = frame["yield_pct"].astype(float).where(frame["yield_pct"].astype(float) > 0)
    minutes = (frame["setup_time_minutes"].astype(float) * batches
               + frame["run_time_minutes_per_unit"].astype(float) * frame["qty"] / yield_pct)
    return minutes / 60.0


def wip_ageing(wip: pd.DataFrame, snapshot_date: dt.date | None = None) -> tuple[dt.date | None, pd.DataFrame]:
    """Age of each WIP record at one snapshot: snapshot_date − stage_entered_at, in days.

    Uses the latest snapshot when none is given. Records whose stage entry is
    after the snapshot are flagged INVALID_AGE and excluded from statistics."""
    if wip.empty:
        return None, wip.assign(age_days=pd.Series(dtype=float), age_class=pd.Series(dtype=str))
    frame = wip.copy()
    frame["snapshot_date"] = pd.to_datetime(frame["snapshot_date"])
    frame["stage_entered_at"] = pd.to_datetime(frame["stage_entered_at"])
    chosen = pd.Timestamp(snapshot_date) if snapshot_date else frame["snapshot_date"].max()
    frame = frame.loc[frame["snapshot_date"] == chosen].copy()
    frame["age_days"] = (frame["snapshot_date"] - frame["stage_entered_at"]).dt.days.astype(float)
    frame["age_class"] = np.where(frame["age_days"] < 0, "INVALID_AGE", "VALID")
    frame.loc[frame["age_class"] == "INVALID_AGE", "age_days"] = np.nan
    return chosen.date(), frame
