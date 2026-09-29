"""Calibrated execution history: realistic operation timestamps and enough completed history.

Runs LAST in the generator (after DQ injection and state snapshots) on its own spawned RNG stream,
so it cannot reshuffle any other generated table: demand, BOM, routing, capacity, costs, inventory,
WIP and injected defects stay bit-identical. It changes only two tables:

1. Operation timestamps of fully COMPLETED production orders are rebuilt at minute resolution from
   the routing standard (setup × batches + run × qty / yield), a per-operation efficiency factor,
   the work centre's shift calendar (shifts × hours from 06:00, working days Monday onward), queue
   waiting before each operation and the routing transfer time. A small share of operations start
   before the previous one finishes (transfer-batch overlap). The order's actual start/finish dates
   follow its operations. Orders whose routing lacks a standard or a work centre keep their original
   day-resolution timestamps (they are DQ-affected anyway).
2. Additional COMPLETED historical orders are appended for manufactured items with a complete
   routing, so each item has 4–7 completed orders to measure. New orders never touch WIP, open
   orders or any planning input.

The period engine does not read production orders, so scenario results are unaffected.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from app.synthetic.context import GenContext
from app.synthetic.timeline import HISTORY_START, REFERENCE_DATE

SPAWN_KEY = (2,)                 # child 2 of the context seed; children 0 and 1 are rng / capacity_rng
SHIFT_START_HOUR = 6
EFFICIENCY_MEDIAN, EFFICIENCY_SIGMA = 1.08, 0.15
QUEUE_MEDIAN_HOURS, QUEUE_SIGMA = 10.0, 0.9
OVERLAP_SHARE = 0.02
TARGET_COMPLETED = (4, 8)        # rng.integers bounds → 4..7 completed orders per item
LATEST_APPENDED_START = REFERENCE_DATE - dt.timedelta(days=35)


def history_rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=SPAWN_KEY))


class WorkCalendar:
    """Contiguous daily shift window from 06:00 on the first `days_per_week` weekdays."""

    def __init__(self, shifts_per_day: int, hours_per_shift: float, days_per_week: int):
        self.daily_hours = min(24.0, float(shifts_per_day) * float(hours_per_shift))
        self.days_per_week = int(days_per_week)

    def _window(self, day: dt.datetime) -> tuple[dt.datetime, dt.datetime] | None:
        if day.weekday() >= self.days_per_week or self.daily_hours <= 0:
            return None
        start = day.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(hours=SHIFT_START_HOUR)
        return start, start + dt.timedelta(hours=self.daily_hours)

    def _containing(self, t: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
        """The window containing t; a window may run past midnight into the next calendar day."""
        today = t.replace(hour=0, minute=0, second=0, microsecond=0)
        for day in (today - dt.timedelta(days=1), today):
            window = self._window(day)
            if window and window[0] <= t < window[1]:
                return window
        raise ValueError(f"{t} is not inside a working window")

    def next_working(self, t: dt.datetime) -> dt.datetime:
        day = t.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=1)
        for _ in range(16):
            window = self._window(day)
            if window and t < window[1]:
                return max(t, window[0])
            day += dt.timedelta(days=1)
        raise ValueError("no working window within two weeks")

    def advance(self, t: dt.datetime, work_hours: float) -> dt.datetime:
        remaining = work_hours
        t = self.next_working(t)
        while remaining > 1e-9:
            window_end = self._containing(t)[1]
            take = min(remaining, (window_end - t).total_seconds() / 3600.0)
            t += dt.timedelta(hours=take)
            remaining -= take
            if remaining > 1e-9:
                t = self.next_working(t)
        return t


def standard_work_hours(op: pd.Series, qty: float) -> float | None:
    setup, run = op["setup_time_minutes"], op["run_time_minutes_per_unit"]
    if pd.isna(setup) or pd.isna(run) or pd.isna(op["work_centre_id"]):
        return None
    batches = math.ceil(qty / max(1, int(op["batch_size"])))
    yield_pct = float(op["yield_pct"]) if float(op["yield_pct"]) > 0 else 1.0
    return (float(setup) * batches + float(run) * qty / yield_pct) / 60.0


def _minute(t: dt.datetime) -> dt.datetime:
    return t.replace(second=0, microsecond=0)


def simulate_order(ops: pd.DataFrame, qty: float, start: dt.datetime, calendars: dict[int, WorkCalendar],
                   rng: np.random.Generator) -> list[tuple[dt.datetime, dt.datetime]] | None:
    """Timestamps for each routing operation in sequence, or None if any standard is missing."""
    hours = [standard_work_hours(op, qty) for _, op in ops.iterrows()]
    if any(h is None for h in hours) or any(int(op["work_centre_id"]) not in calendars for _, op in ops.iterrows()):
        return None
    result, t, previous = [], start, None
    for (_, op), work in zip(ops.iterrows(), hours):
        calendar = calendars[int(op["work_centre_id"])]
        wait = rng.uniform(0.0, 4.0) if previous is None else rng.lognormal(math.log(QUEUE_MEDIAN_HOURS), QUEUE_SIGMA)
        begin = calendar.next_working(t + dt.timedelta(hours=float(wait)))
        if previous is not None and rng.random() < OVERLAP_SHARE:
            begin = max(previous[0], previous[1] - dt.timedelta(hours=float(rng.uniform(0.5, 2.0))))
        efficiency = rng.lognormal(math.log(EFFICIENCY_MEDIAN), EFFICIENCY_SIGMA)
        end = calendar.advance(begin, max(work * efficiency, 1.0 / 60.0))
        result.append((_minute(begin), _minute(end)))
        previous = result[-1]
        transfer = op["transfer_time_minutes"]
        t = end + dt.timedelta(minutes=0.0 if pd.isna(transfer) else float(transfer))
    return result


def calibrate_execution_history(ctx: GenContext) -> dict:
    rng = history_rng(ctx.seed)
    orders = ctx.tables["production_orders"].copy()
    ops_table = ctx.tables["production_order_operations"].copy()
    routing_ops = ctx.tables["routing_operations"]
    by_routing = {rid: g.sort_values("seq_no") for rid, g in routing_ops.groupby("routing_id")}
    routing_op_by_id = routing_ops.set_index("routing_operation_id")
    calendars = {int(r.work_centre_id): WorkCalendar(r.shifts_per_day, r.hours_per_shift, r.days_per_week)
                 for r in ctx.tables["work_centres"].itertuples()}

    rebuilt = kept = 0
    ops_table["actual_start"] = pd.to_datetime(ops_table["actual_start"]).astype(object)
    ops_table["actual_finish"] = pd.to_datetime(ops_table["actual_finish"]).astype(object)
    for idx, order in orders.loc[orders["status"] == "COMPLETED"].iterrows():
        order_ops = ops_table.loc[ops_table["production_order_id"] == order["production_order_id"]].sort_values("seq_no")
        if order_ops.empty:
            continue
        standards = routing_op_by_id.reindex(order_ops["routing_operation_id"]).reset_index()
        standards["work_centre_id"] = order_ops["work_centre_id"].to_numpy()
        start = dt.datetime.combine(pd.Timestamp(order["actual_start"]).date(), dt.time(SHIFT_START_HOUR))
        times = simulate_order(standards, float(order["qty"]), start, calendars, rng)
        if times is None or times[-1][1].date() >= REFERENCE_DATE:
            kept += 1
            continue
        for (op_idx, _), (begin, end) in zip(order_ops.iterrows(), times):
            ops_table.at[op_idx, "actual_start"] = begin
            ops_table.at[op_idx, "actual_finish"] = end
        orders.at[idx, "actual_start"] = times[0][0].date()
        orders.at[idx, "actual_finish"] = times[-1][1].date()
        rebuilt += 1

    items = ctx.tables["items"]
    manufactured = items.loc[items["item_type"].isin(["FG", "SUBASSY"])]
    routing_by_item = ctx.tables["routing_headers"].set_index("item_id")["routing_id"].to_dict()
    bom_by_parent = ctx.tables["bom_headers"].set_index("parent_item_id")["bom_id"].to_dict()
    completed_by_item = orders.loc[orders["status"] == "COMPLETED"].groupby("item_id").size()
    site_id = int(ctx.tables["sites"].iloc[0]["site_id"])
    po_seq, op_seq = ctx.id_seq("production_orders"), ctx.id_seq("production_order_operations")
    window_days = (LATEST_APPENDED_START - HISTORY_START).days
    new_orders, new_ops = [], []
    for item in manufactured.itertuples():
        item_id = int(item.item_id)
        routing_id = routing_by_item.get(item_id)
        ops = by_routing.get(routing_id)
        target = int(rng.integers(*TARGET_COMPLETED))
        missing = max(0, target - int(completed_by_item.get(item_id, 0)))
        if ops is None:
            continue
        for _ in range(missing):
            planned_start = HISTORY_START + dt.timedelta(days=int(rng.integers(0, window_days)))
            qty = float(rng.integers(5, 60) if item.item_type == "FG" else rng.integers(10, 250))
            slip = int(rng.integers(0, 3))
            start = dt.datetime.combine(planned_start + dt.timedelta(days=slip), dt.time(SHIFT_START_HOUR))
            times = simulate_order(ops, qty, start, calendars, rng)
            if times is None or times[-1][1].date() >= REFERENCE_DATE - dt.timedelta(days=7):
                continue
            order_id = po_seq.next()
            planned_finish = max(planned_start, times[-1][1].date() - dt.timedelta(days=int(rng.integers(0, 3))))
            new_orders.append({"production_order_id": order_id, "order_number": f"PO-PROD-{order_id:07d}",
                               "item_id": item_id, "bom_id": bom_by_parent.get(item_id), "routing_id": routing_id,
                               "site_id": site_id, "qty": qty, "status": "COMPLETED",
                               "planned_start": planned_start, "planned_finish": planned_finish,
                               "actual_start": times[0][0].date(), "actual_finish": times[-1][1].date()})
            span = max(1, (planned_finish - planned_start).days) / len(ops)
            for i, ((_, op), (begin, end)) in enumerate(zip(ops.iterrows(), times)):
                new_ops.append({"po_operation_id": op_seq.next(), "production_order_id": order_id,
                                "routing_operation_id": int(op["routing_operation_id"]), "seq_no": int(op["seq_no"]),
                                "work_centre_id": op["work_centre_id"],
                                "planned_start": planned_start + dt.timedelta(days=span * i),
                                "planned_finish": planned_start + dt.timedelta(days=span * (i + 1)),
                                "actual_start": begin, "actual_finish": end, "status": "COMPLETED"})

    ctx.add_table("production_orders", pd.concat([orders, pd.DataFrame(new_orders)], ignore_index=True))
    ctx.add_table("production_order_operations", pd.concat([ops_table, pd.DataFrame(new_ops)], ignore_index=True))
    return {"orders_rebuilt": rebuilt, "orders_kept_original": kept,
            "orders_appended": len(new_orders), "operations_appended": len(new_ops)}


def generate_all(ctx: GenContext) -> dict:
    return calibrate_execution_history(ctx)
