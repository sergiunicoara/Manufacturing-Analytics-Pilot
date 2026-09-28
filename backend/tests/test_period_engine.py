"""Period engine mechanics: stateful backlog continuity, queue-time
continuity (no jump), constraint classification timeline, WIP/Little's-Law
sanity check, material-shortage-vs-capacity distinction, and short-spike
non-persistence.

These use a small, fully controlled synthetic "mini-plant" fixture rather
than the full generated dataset, so exact numbers are hand-verifiable and
tests aren't sensitive to unrelated generator changes. The CAB-100/welding
named golden scenarios (CP3 req. 11/12) run against the real generated
dataset in test_golden_scenarios.py instead.
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import pytest

from app.analytics.blocking import BlockingIndex
from app.analytics.period_engine import run_period_engine

MONDAY = dt.date(2026, 1, 5)


def weeks(n: int) -> list[dt.date]:
    return [MONDAY + dt.timedelta(weeks=i) for i in range(n)]


def mini_plant(n_weeks: int, steel_initial_inventory: float = 100000.0, steel_receipts: dict | None = None):
    """One FG (WIDGET, item_id=1) with a single routing operation on WC-1,
    consuming one RAW material (STEEL, item_id=2). All capacity math is
    hand-computable: calendar=40h/week, available=effective=40h/week.
    """
    items = pd.DataFrame([
        {"item_id": 1, "item_code": "WIDGET", "description": "Widget", "item_type": "FG", "uom": "PC", "product_family": None, "is_active": True},
        {"item_id": 2, "item_code": "STEEL", "description": "Steel", "item_type": "RAW", "uom": "KG", "product_family": None, "is_active": True},
    ])
    bom_headers = pd.DataFrame([
        {"bom_id": 1, "parent_item_id": 1, "revision": "A", "status": "ACTIVE", "effective_from": dt.date(2020, 1, 1), "effective_to": None},
    ])
    bom_components = pd.DataFrame([
        {"bom_component_id": 1, "bom_id": 1, "component_item_id": 2, "quantity_per": 1.0, "scrap_pct": 0.0,
         "effective_from": dt.date(2020, 1, 1), "effective_to": None},
    ])
    routing_headers = pd.DataFrame([
        {"routing_id": 1, "item_id": 1, "revision": "A", "effective_from": dt.date(2020, 1, 1), "effective_to": None},
    ])
    routing_operations = pd.DataFrame([
        {"routing_operation_id": 1, "routing_id": 1, "seq_no": 1, "work_centre_id": 1, "operation_name": "Assembly",
         "setup_time_minutes": 60.0, "run_time_minutes_per_unit": 10.0, "queue_time_minutes": 120.0,
         "transfer_time_minutes": 30.0, "yield_pct": 1.0, "batch_size": 10},
    ])
    work_centres = pd.DataFrame([
        {"work_centre_id": 1, "work_centre_code": "WC-1", "name": "WC 1", "site_id": 1, "process_type": "ASSEMBLY",
         "shifts_per_day": 1, "hours_per_shift": 8.0, "days_per_week": 5, "cost_per_hour": 50.0},
        {"work_centre_id": 2, "work_centre_code": "WC-2", "name": "WC 2", "site_id": 1, "process_type": "INSPECTION",
         "shifts_per_day": 1, "hours_per_shift": 8.0, "days_per_week": 5, "cost_per_hour": 40.0},
    ])
    horizon = weeks(n_weeks)
    capacity_rows = []
    for wc_id in (1, 2):
        for w in horizon:
            capacity_rows.append({
                "work_centre_id": wc_id, "week_start_date": pd.Timestamp(w), "calendar_hours": 40.0,
                "planned_downtime_hours": 0.0, "availability_pct": 1.0, "available_hours": 40.0, "effective_hours": 40.0,
            })
    capacity_calendar = pd.DataFrame(capacity_rows)

    po_rows = []
    for (item_id, period), qty in (steel_receipts or {}).items():
        po_rows.append({
            "item_id": item_id, "qty_ordered": qty, "qty_received": 0.0,
            "expected_receipt_date": period, "status": "OPEN",
        })
    purchase_order_lines = pd.DataFrame(po_rows, columns=["item_id", "qty_ordered", "qty_received", "expected_receipt_date", "status"])

    blocking_index = BlockingIndex()
    initial_inventory = {2: steel_initial_inventory}

    return dict(
        horizon=horizon, items_df=items, bom_headers_df=bom_headers, bom_components_df=bom_components,
        routing_headers_df=routing_headers, routing_operations_df=routing_operations, work_centres_df=work_centres,
        capacity_calendar_df=capacity_calendar, purchase_order_lines_df=purchase_order_lines,
        initial_inventory=initial_inventory, blocking_index=blocking_index,
    )


def required_hours_for_qty(qty: float) -> float:
    batches = math.ceil(qty / 10)
    return (60 * batches + 10 * qty) / 60.0


# ============================================================
# CP3 req. 7: stateful backlog continuity
# ============================================================


def test_backlog_end_equals_next_period_backlog_start():
    plant = mini_plant(8)
    demand = {w: {1: 160.0} for w in plant["horizon"]}  # required=~104h/week vs 40h effective -> sustained overload
    output = run_period_engine(top_level_demand=demand, **plant)

    series = output.wc_series(1)
    for i in range(len(series) - 1):
        assert series[i].backlog_hours_end == pytest.approx(series[i + 1].backlog_hours_start, rel=1e-9), (
            f"backlog continuity broken between period {i} and {i+1}"
        )
    # And it must actually be accumulating (sustained overload), not stuck at 0.
    assert series[-1].backlog_hours_end > series[2].backlog_hours_end > 0


def test_backlog_drains_toward_zero_when_underloaded():
    plant = mini_plant(6)
    demand = {w: {1: 50.0} for w in plant["horizon"]}  # required ~14.2h vs 40h effective -> never overloaded
    output = run_period_engine(top_level_demand=demand, **plant)
    series = output.wc_series(1)
    assert all(r.backlog_hours_end == pytest.approx(0.0) for r in series)
    assert all(r.utilization_pct < 1.0 for r in series)


# ============================================================
# CP3 req. 8: queue-time continuity (no jump) across utilization levels
# ============================================================


def test_queue_time_is_continuous_across_the_utilization_range():
    """Runs several independent single-period-style scenarios at closely
    spaced utilization levels straddling 100% and asserts queue_time_days
    changes smoothly (no discontinuous jump), confirming the unified
    backlog-based formula (see period_engine.py module docstring) has no
    seam — unlike the originally-sketched piecewise design."""
    results = []
    for qty in (140, 145, 150, 155, 160, 165, 170):  # utilization sweeps through ~0.9 to ~1.15
        plant = mini_plant(1)
        demand = {plant["horizon"][0]: {1: float(qty)}}
        output = run_period_engine(top_level_demand=demand, **plant)
        r = output.wc_series(1)[0]
        results.append((r.utilization_pct, r.queue_time_days))

    results.sort()
    queue_values = [q for _, q in results]
    # Monotonic non-decreasing in utilization.
    assert all(queue_values[i] <= queue_values[i + 1] + 1e-9 for i in range(len(queue_values) - 1))
    # No single step changes by more than a small multiple of the average
    # step size — i.e. no discontinuous jump at any point, including across
    # utilization = 1.0.
    steps = [queue_values[i + 1] - queue_values[i] for i in range(len(queue_values) - 1)]
    avg_step = sum(steps) / len(steps)
    assert max(steps) < avg_step * 4 + 0.5, f"discontinuous jump detected: steps={steps}"


def test_queue_time_never_blows_up_or_goes_negative_far_above_saturation():
    plant = mini_plant(3)
    demand = {w: {1: 1000.0} for w in plant["horizon"]}  # wildly overloaded
    output = run_period_engine(top_level_demand=demand, **plant)
    for r in output.wc_series(1):
        assert r.queue_time_days >= 0
        assert math.isfinite(r.queue_time_days)


# ============================================================
# CP3 req. 10: constraint classification timeline
# ============================================================


def test_single_overload_week_does_not_become_candidate_constraint():
    plant = mini_plant(6)
    demand = {w: {1: 50.0} for w in plant["horizon"]}
    spike_week = plant["horizon"][2]
    demand[spike_week] = {1: 500.0}  # one-week spike, clearly overloaded that week only
    output = run_period_engine(top_level_demand=demand, **plant)
    series = output.wc_series(1)

    assert series[2].utilization_pct > 1.0
    assert series[2].constraint_classification == "OVERLOADED"
    assert all(r.constraint_classification not in ("CANDIDATE_CONSTRAINT", "PRIMARY_CONSTRAINT") for r in series)


def test_persistent_overload_becomes_candidate_then_primary_constraint():
    plant = mini_plant(8)
    demand = {w: {1: 160.0} for w in plant["horizon"]}  # sustained overload from week 0
    output = run_period_engine(top_level_demand=demand, **plant)
    series = output.wc_series(1)

    classifications = [r.constraint_classification for r in series]
    # Weeks 0, 1 overloaded but not yet candidate (need >= 3 consecutive).
    assert classifications[0] == "OVERLOADED"
    assert classifications[1] == "OVERLOADED"
    # From week 2 onward (3rd consecutive overloaded period), candidate or
    # primary (a lone candidate is always promoted to primary).
    for cls in classifications[2:]:
        assert cls in ("CANDIDATE_CONSTRAINT", "PRIMARY_CONSTRAINT")
    # With only one work centre ever overloaded, it must become THE primary.
    assert "PRIMARY_CONSTRAINT" in classifications


def test_primary_constraint_goes_to_the_larger_cumulative_backlog():
    """Two work centres both sustained-overloaded; WC-1 more severely. WC-1
    must be the one designated PRIMARY_CONSTRAINT once both qualify."""
    plant = mini_plant(8)
    # Route the same demand through WC-2 too (identical routing-time
    # formula), but give WC-2 slightly more effective capacity than WC-1
    # (41.5h vs 40h/week) so it's still overloaded (required ~42.67h/week)
    # but less severely -- its backlog should accumulate more slowly.
    ops = plant["routing_operations_df"]
    ops2 = pd.concat([ops, pd.DataFrame([{
        "routing_operation_id": 2, "routing_id": 1, "seq_no": 2, "work_centre_id": 2, "operation_name": "Inspect",
        "setup_time_minutes": 60.0, "run_time_minutes_per_unit": 10.0, "queue_time_minutes": 30.0,
        "transfer_time_minutes": 5.0, "yield_pct": 1.0, "batch_size": 10,
    }])], ignore_index=True)
    plant["routing_operations_df"] = ops2
    cal = plant["capacity_calendar_df"]
    cal.loc[cal["work_centre_id"] == 2, "effective_hours"] = 41.5
    plant["capacity_calendar_df"] = cal

    demand = {w: {1: 160.0} for w in plant["horizon"]}
    output = run_period_engine(top_level_demand=demand, **plant)

    series1 = output.wc_series(1)
    series2 = output.wc_series(2)
    assert series1[-1].backlog_hours_end > series2[-1].backlog_hours_end > 0
    assert series1[-1].constraint_classification == "PRIMARY_CONSTRAINT"
    assert series2[-1].constraint_classification == "CANDIDATE_CONSTRAINT"


# ============================================================
# CP3 req. 9: WIP reconciles with Little's Law as a SANITY CHECK
# ============================================================


def test_wip_is_consistent_with_littles_law_within_tolerance():
    plant = mini_plant(10)
    demand = {w: {1: 160.0} for w in plant["horizon"]}
    output = run_period_engine(top_level_demand=demand, **plant)
    series = output.wc_series(1)
    last = series[-1]

    # Little's Law: WIP ~= throughput * lead_time. Throughput here is capped
    # at effective capacity (units/day); lead_time proxy = queue_time_days
    # (the backlog-driven wait) -- this is a sanity CHECK on the engine's
    # WIP output, not how WIP was computed (WIP was derived from backlog
    # hours / minutes-per-unit in period_engine.py).
    minutes_per_unit = 10.0  # from the fixture's run_time_minutes_per_unit
    throughput_units_per_day = (last.effective_hours * 60.0 / minutes_per_unit) / 7.0
    littles_law_wip = throughput_units_per_day * last.queue_time_days

    assert last.wip_qty == pytest.approx(littles_law_wip, rel=0.05)


# ============================================================
# CP3 req. 13: material shortage, not capacity, is the limiting factor
# ============================================================


def test_material_shortage_does_not_falsely_trigger_a_capacity_constraint():
    plant = mini_plant(6, steel_initial_inventory=5.0, steel_receipts={})  # almost no steel, no receipts
    demand = {w: {1: 20.0} for w in plant["horizon"]}  # modest demand: required ~26.7h vs 40h effective -- comfortably under capacity
    output = run_period_engine(top_level_demand=demand, **plant)

    wc_series = output.wc_series(1)
    assert all(r.utilization_pct < 1.0 for r in wc_series)
    assert all(r.constraint_classification == "NONE" for r in wc_series)

    steel_series = output.material_series(2)
    assert any(r.shortage_flag for r in steel_series), "expected the steel shortage to be recorded"
    assert steel_series[0].net_requirement > 0


# ============================================================
# CP3 req. 14: a short-lived forecast spike does not create a standing
# constraint (the precondition for a buffer recommendation, per PLAN.md —
# buffers are only ever recommended upstream of a CANDIDATE/PRIMARY
# constraint, never from a one-off OVERLOADED spike).
# ============================================================


def test_short_forecast_spike_does_not_create_a_persistent_constraint():
    # 14 weeks: enough for backlog run-up from a 2-week spike (required
    # exceeds effective by ~93h/week during the spike) to fully drain again
    # at baseline's ~-27h/week recovery rate (verified below, not asserted
    # blind: this is a property of the fixture's numbers, not a hardcoded
    # "it happens to work out" horizon length).
    plant = mini_plant(14)
    demand = {w: {1: 50.0} for w in plant["horizon"]}  # steady, comfortable baseline
    spike_weeks = plant["horizon"][4:6]  # 2-week spike, below the 3-consecutive threshold
    for w in spike_weeks:
        demand[w] = {1: 500.0}
    output = run_period_engine(top_level_demand=demand, **plant)
    series = output.wc_series(1)

    assert not any(r.constraint_classification in ("CANDIDATE_CONSTRAINT", "PRIMARY_CONSTRAINT") for r in series)
    # Backlog must peak during/just after the spike, then monotonically
    # drain back to zero well before the horizon ends -- not persist.
    peak = max(r.backlog_hours_end for r in series)
    peak_week_idx = max(range(len(series)), key=lambda i: series[i].backlog_hours_end)
    assert peak > 100  # the spike did create real backlog
    assert series[-1].backlog_hours_end == pytest.approx(0.0, abs=1e-6)  # fully drained by horizon end
    # Monotonically non-increasing from the peak onward -- recovering, not
    # oscillating or re-growing.
    tail = [r.backlog_hours_end for r in series[peak_week_idx:]]
    assert all(tail[i] >= tail[i + 1] - 1e-9 for i in range(len(tail) - 1))
