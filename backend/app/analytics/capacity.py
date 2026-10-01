"""Three-tier capacity [CORR-5]: calendar -> available -> effective ->
required -> utilization -> overload. Pure functions — capacity_calendar rows
are passed in, not queried here.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import pandas as pd

from app.analytics.blocking import BlockingIndex


@dataclass
class CapacityResult:
    work_centre_id: int
    period_start_date: dt.date
    calendar_hours: float
    available_hours: float
    effective_hours: float
    required_hours: float
    utilization_pct: float  # NaN if effective_hours <= 0 (unreliable capacity data for this period)
    overload_hours: float  # NaN under the same condition
    excluded_routing_operation_ids: list[int]


def _to_date(value) -> dt.date | None:
    return None if pd.isna(value) else pd.Timestamp(value).date()


class RoutingLoad:
    """Rough-cut capacity load from routings, indexed once: routing headers as (item, routing, window) and
    operations grouped by routing in table order. Build one per set of tables and reuse it across periods."""

    def __init__(self, routing_headers_df: pd.DataFrame, routing_operations_df: pd.DataFrame):
        self._latest_by_item = routing_headers_df.set_index("item_id")["routing_id"].to_dict()
        self._all_by_item: dict[int, list[int]] = {}
        for item_id, routing_id in zip(routing_headers_df["item_id"], routing_headers_df["routing_id"]):
            self._all_by_item.setdefault(int(item_id), []).append(int(routing_id))
        self._dated = "effective_from" in routing_headers_df.columns
        self._headers = []
        if self._dated:
            # A missing effective_to column means no revision has an end date (open-ended).
            ends = (routing_headers_df["effective_to"].map(_to_date) if "effective_to" in routing_headers_df.columns
                    else [None] * len(routing_headers_df))
            self._headers = list(zip(routing_headers_df["item_id"], routing_headers_df["routing_id"],
                                     routing_headers_df["effective_from"].map(_to_date), ends))
        self._ops: dict = {}
        for op in routing_operations_df.to_dict("records"):
            self._ops.setdefault(op["routing_id"], []).append(op)

    def routing_by_item(self, period: dt.date | None) -> tuple[dict[int, int], dict[int, list[int]]]:
        """(item -> routing used, item -> overlapping routing ids). Several revisions effective in the same
        week is ambiguous: like the lead-time calculation, the load is then not guessed."""
        if period is None or not self._dated:
            return self._latest_by_item, {}
        effective: dict[int, list[int]] = {}
        for item_id, routing_id, start, end in self._headers:
            if start is not None and start <= period and (end is None or end >= period):
                effective.setdefault(int(item_id), []).append(int(routing_id))
        unique = {item: ids[0] for item, ids in effective.items() if len(ids) == 1}
        ambiguous = {item: ids for item, ids in effective.items() if len(ids) > 1}
        return unique, ambiguous

    def required_hours(self, production_qty_by_item: dict[int, float], blocking_index: BlockingIndex,
                       period_start_date: dt.date | None = None) -> tuple[dict[int, float], list[int]]:
        routing_id_by_item, ambiguous = self.routing_by_item(period_start_date)
        required: dict[int, float] = {}
        excluded: list[int] = []

        for item_id, qty in production_qty_by_item.items():
            if qty <= 0:
                continue
            if item_id in ambiguous:
                excluded.extend(int(op["routing_operation_id"]) for routing_id in ambiguous[item_id]
                                for op in self._ops.get(routing_id, ()))
                continue
            routing_id = routing_id_by_item.get(item_id)
            if routing_id is None:
                if period_start_date is not None and self._dated and item_id in self._all_by_item:
                    # The item has routings but none is effective this week (a gap between revisions): the load
                    # is unknown, so its operations are reported as excluded rather than counted as zero.
                    excluded.extend(int(op["routing_operation_id"]) for rid in self._all_by_item[item_id]
                                    for op in self._ops.get(rid, ()))
                continue
            for op in self._ops.get(routing_id, ()):
                op_id = int(op["routing_operation_id"])
                if blocking_index.is_kpi_blocked("routing_operation", op_id):
                    excluded.append(op_id)
                    continue
                wc_id = op["work_centre_id"]
                if pd.isna(wc_id):
                    excluded.append(op_id)
                    continue
                setup = op["setup_time_minutes"]
                run = op["run_time_minutes_per_unit"]
                if pd.isna(setup) or pd.isna(run):
                    excluded.append(op_id)
                    continue
                batch_size = int(op["batch_size"])
                if batch_size <= 0:
                    excluded.append(op_id)
                    continue
                yield_pct = float(op["yield_pct"])
                if yield_pct <= 0:
                    excluded.append(op_id)
                    continue

                batches = math.ceil(qty / batch_size)
                minutes = setup * batches + run * (qty / yield_pct)
                hours = minutes / 60.0
                wc_id = int(wc_id)
                required[wc_id] = required.get(wc_id, 0.0) + hours

        return required, excluded


def compute_required_hours_by_work_centre(
    production_qty_by_item: dict[int, float],
    routing_headers_df: pd.DataFrame,
    routing_operations_df: pd.DataFrame,
    blocking_index: BlockingIndex,
    period_start_date: dt.date | None = None,
) -> tuple[dict[int, float], list[int]]:
    """Rough-cut capacity load: every routing operation for every item being
    produced this period loads its work centre in this same period (no
    operation-level time-offsetting; see period_engine.py's module
    docstring for why this simplification is documented and acceptable).
    A routing operation with a KPI_BLOCKING finding (missing time, missing
    work centre, invalid batch/yield) is excluded from the total; it does
    not block the item's other operations or any other item/work centre
    [CP3 req. 2].

    With `period_start_date`, each item uses the routing revision effective in that week (the same
    effective-dating the lead-time calculation applies). If several revisions overlap, the item's load is not
    guessed: their operations are reported as excluded, just as the lead time reports the route unavailable.
    Without a period (or without effective-date columns) the routing table is taken as is.
    Single-call wrapper: loops should build one RoutingLoad and reuse it.
    """
    return RoutingLoad(routing_headers_df, routing_operations_df).required_hours(
        production_qty_by_item, blocking_index, period_start_date)


class CapacityCalendar:
    """The capacity calendar indexed by (work centre, week start) once. week_start_date may be a plain date
    (in-memory generator) or a pandas Timestamp (DB-loaded); both are normalised to dates, because
    `date == Timestamp` is silently always False. The first row of a duplicated key wins, as before."""

    def __init__(self, capacity_calendar_df: pd.DataFrame):
        self._rows: dict = {}
        weeks = pd.to_datetime(capacity_calendar_df["week_start_date"]).dt.date
        for wc_id, week, calendar, available, effective in zip(
                capacity_calendar_df["work_centre_id"], weeks, capacity_calendar_df["calendar_hours"],
                capacity_calendar_df["available_hours"], capacity_calendar_df["effective_hours"]):
            self._rows.setdefault((wc_id, week), (float(calendar), float(available), float(effective)))

    def compute(self, work_centre_id: int, period_start_date: dt.date, required_hours: float,
                blocking_index: BlockingIndex, excluded_routing_operation_ids: list[int] | None = None,
                effective_hours_multiplier: float = 1.0) -> CapacityResult:
        """`effective_hours_multiplier` is the scenario capacity lever (e.g. "+30% welding capacity"),
        applied directly to this one work centre's effective hours."""
        row = self._rows.get((work_centre_id, period_start_date))
        if row is None:
            return CapacityResult(
                work_centre_id=work_centre_id, period_start_date=period_start_date,
                calendar_hours=float("nan"), available_hours=float("nan"), effective_hours=float("nan"),
                required_hours=required_hours, utilization_pct=float("nan"), overload_hours=float("nan"),
                excluded_routing_operation_ids=excluded_routing_operation_ids or [],
            )

        calendar_hours, available_hours, effective_hours = row
        effective_hours = effective_hours * effective_hours_multiplier

        kpi_blocked = blocking_index.is_kpi_blocked("work_centre_period", f"{work_centre_id}:{period_start_date}")
        if effective_hours <= 0 or kpi_blocked:
            utilization_pct = float("nan")
            overload_hours = float("nan")
        else:
            utilization_pct = required_hours / effective_hours
            overload_hours = max(0.0, required_hours - effective_hours)

        return CapacityResult(
            work_centre_id=work_centre_id, period_start_date=period_start_date,
            calendar_hours=calendar_hours, available_hours=available_hours, effective_hours=effective_hours,
            required_hours=required_hours, utilization_pct=utilization_pct, overload_hours=overload_hours,
            excluded_routing_operation_ids=excluded_routing_operation_ids or [],
        )


def compute_capacity(
    work_centre_id: int,
    period_start_date: dt.date,
    required_hours: float,
    capacity_calendar_df: pd.DataFrame,
    blocking_index: BlockingIndex,
    excluded_routing_operation_ids: list[int] | None = None,
    effective_hours_multiplier: float = 1.0,
) -> CapacityResult:
    """Single-call wrapper around CapacityCalendar; loops should build one calendar and reuse it."""
    return CapacityCalendar(capacity_calendar_df).compute(
        work_centre_id, period_start_date, required_hours, blocking_index, excluded_routing_operation_ids,
        effective_hours_multiplier)
