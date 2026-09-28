"""Three-tier capacity: calendar -> available -> effective -> required ->
utilization -> overload, and KPI_BLOCKING scoping [CP3 req. 2/6]."""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import pytest

from app.analytics.blocking import BlockingIndex
from app.analytics.capacity import compute_capacity, compute_required_hours_by_work_centre

WEEK = dt.date(2026, 1, 5)


def _routing_headers():
    return pd.DataFrame([
        {"item_id": 1, "routing_id": 1},
        {"item_id": 2, "routing_id": 2},
    ])


def _routing_ops():
    return pd.DataFrame([
        {"routing_operation_id": 1, "routing_id": 1, "work_centre_id": 100, "setup_time_minutes": 60.0,
         "run_time_minutes_per_unit": 10.0, "batch_size": 10, "yield_pct": 1.0},
        {"routing_operation_id": 2, "routing_id": 2, "work_centre_id": 100, "setup_time_minutes": 30.0,
         "run_time_minutes_per_unit": 5.0, "batch_size": 5, "yield_pct": 1.0},
    ])


def test_three_tiers_are_all_exposed_and_consistent():
    calendar = pd.DataFrame([{
        "work_centre_id": 100, "week_start_date": pd.Timestamp(WEEK), "calendar_hours": 40.0,
        "planned_downtime_hours": 4.0, "availability_pct": 0.9, "available_hours": 36.0, "effective_hours": 32.4,
    }])
    cap = compute_capacity(100, WEEK, required_hours=45.0, capacity_calendar_df=calendar, blocking_index=BlockingIndex())

    assert cap.calendar_hours == 40.0
    assert cap.available_hours == 36.0  # calendar - planned_downtime
    assert cap.effective_hours == pytest.approx(32.4)  # available * availability_pct
    assert cap.required_hours == 45.0
    assert cap.utilization_pct == pytest.approx(45.0 / 32.4)
    assert cap.overload_hours == pytest.approx(45.0 - 32.4)


def test_zero_effective_hours_reports_nan_utilization_not_infinity():
    calendar = pd.DataFrame([{
        "work_centre_id": 100, "week_start_date": pd.Timestamp(WEEK), "calendar_hours": 40.0,
        "planned_downtime_hours": 40.0, "availability_pct": 1.0, "available_hours": 0.0, "effective_hours": 0.0,
    }])
    cap = compute_capacity(100, WEEK, required_hours=10.0, capacity_calendar_df=calendar, blocking_index=BlockingIndex())
    assert math.isnan(cap.utilization_pct)
    assert math.isnan(cap.overload_hours)


def test_capacity_multiplier_applies_only_to_the_named_work_centre():
    calendar = pd.DataFrame([{
        "work_centre_id": 100, "week_start_date": pd.Timestamp(WEEK), "calendar_hours": 40.0,
        "planned_downtime_hours": 0.0, "availability_pct": 1.0, "available_hours": 40.0, "effective_hours": 40.0,
    }])
    cap = compute_capacity(100, WEEK, required_hours=45.0, capacity_calendar_df=calendar,
                            blocking_index=BlockingIndex(), effective_hours_multiplier=1.3)
    assert cap.effective_hours == pytest.approx(52.0)
    assert cap.utilization_pct == pytest.approx(45.0 / 52.0)


def test_required_hours_sums_across_items_sharing_a_work_centre():
    required, excluded = compute_required_hours_by_work_centre(
        {1: 100.0, 2: 50.0}, _routing_headers(), _routing_ops(), BlockingIndex()
    )
    item1_hours = (60 * 10 + 10 * 100) / 60.0  # batches=10
    item2_hours = (30 * 10 + 5 * 50) / 60.0    # batches=10
    assert required[100] == pytest.approx(item1_hours + item2_hours)
    assert excluded == []


def test_kpi_blocked_operation_is_excluded_without_affecting_other_items():
    index = BlockingIndex()
    index.kpi_blocked[("routing_operation", "1")] = ["missing setup time"]

    required, excluded = compute_required_hours_by_work_centre(
        {1: 100.0, 2: 50.0}, _routing_headers(), _routing_ops(), index
    )
    item2_hours = (30 * 10 + 5 * 50) / 60.0
    # Item 1's operation excluded; item 2's contribution is untouched.
    assert required[100] == pytest.approx(item2_hours)
    assert excluded == [1]


def test_missing_timing_or_invalid_batch_yield_is_excluded_not_crashed():
    ops = pd.DataFrame([
        {"routing_operation_id": 1, "routing_id": 1, "work_centre_id": 100, "setup_time_minutes": None,
         "run_time_minutes_per_unit": 10.0, "batch_size": 10, "yield_pct": 1.0},
        {"routing_operation_id": 2, "routing_id": 2, "work_centre_id": 100, "setup_time_minutes": 30.0,
         "run_time_minutes_per_unit": 5.0, "batch_size": 0, "yield_pct": 1.0},
    ])
    required, excluded = compute_required_hours_by_work_centre(
        {1: 100.0, 2: 50.0}, _routing_headers(), ops, BlockingIndex()
    )
    assert required.get(100, 0.0) == 0.0
    assert set(excluded) == {1, 2}
