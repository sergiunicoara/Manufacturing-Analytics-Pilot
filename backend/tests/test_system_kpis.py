"""CP3.2 req. 4: system-level intervention KPIs go beyond single
work-centre backlog."""
from __future__ import annotations

import pytest

from app.analytics.period_engine import run_period_engine
from app.analytics.reconciliation import compute_system_kpis
from tests.test_period_engine import mini_plant


def _avg_minutes_per_unit(plant):
    return {1: 10.0}  # from the fixture's run_time_minutes_per_unit


def test_service_risk_is_low_when_never_constrained():
    plant = mini_plant(6)
    demand = {w: {1: 50.0} for w in plant["horizon"]}  # comfortable, never overloaded
    output = run_period_engine(top_level_demand=demand, **plant)

    kpis = compute_system_kpis(
        "BASELINE", output, constrained_work_centre_ids=[1], total_demand_units=50.0 * 6,
        lead_time_item_id=1, routing_headers_df=plant["routing_headers_df"],
        routing_operations_df=plant["routing_operations_df"], horizon=plant["horizon"],
        avg_minutes_per_unit_by_wc=_avg_minutes_per_unit(plant),
    )
    assert kpis.service_risk == "LOW"
    assert kpis.ending_total_backlog_hours == pytest.approx(0.0)


def test_service_risk_is_high_under_sustained_growing_overload():
    plant = mini_plant(8)
    demand = {w: {1: 160.0} for w in plant["horizon"]}  # sustained overload, still growing at the end
    output = run_period_engine(top_level_demand=demand, **plant)

    kpis = compute_system_kpis(
        "SHOCK", output, constrained_work_centre_ids=[1], total_demand_units=160.0 * 8,
        lead_time_item_id=1, routing_headers_df=plant["routing_headers_df"],
        routing_operations_df=plant["routing_operations_df"], horizon=plant["horizon"],
        avg_minutes_per_unit_by_wc=_avg_minutes_per_unit(plant),
    )
    assert kpis.service_risk == "HIGH"
    assert kpis.ending_total_backlog_hours > 0


def test_service_risk_is_medium_when_constraint_recovered():
    plant = mini_plant(14)
    demand = {w: {1: 160.0} for w in plant["horizon"]}
    output = run_period_engine(
        top_level_demand=demand, capacity_multiplier_by_work_centre={1: (2.0, plant["horizon"][4])}, **plant
    )
    kpis = compute_system_kpis(
        "CAPACITY_ONLY", output, constrained_work_centre_ids=[1], total_demand_units=160.0 * 14,
        lead_time_item_id=1, routing_headers_df=plant["routing_headers_df"],
        routing_operations_df=plant["routing_operations_df"], horizon=plant["horizon"],
        avg_minutes_per_unit_by_wc=_avg_minutes_per_unit(plant),
    )
    assert kpis.service_risk in ("LOW", "MEDIUM")  # recovered -- must not still read HIGH
    assert kpis.ending_total_backlog_hours == pytest.approx(0.0)


def test_kpis_expose_lead_time_percentiles_and_constrained_wc_detail():
    plant = mini_plant(6)
    demand = {w: {1: 100.0} for w in plant["horizon"]}
    output = run_period_engine(top_level_demand=demand, **plant)

    kpis = compute_system_kpis(
        "BASELINE", output, constrained_work_centre_ids=[1], total_demand_units=100.0 * 6,
        lead_time_item_id=1, routing_headers_df=plant["routing_headers_df"],
        routing_operations_df=plant["routing_operations_df"], horizon=plant["horizon"],
        avg_minutes_per_unit_by_wc=_avg_minutes_per_unit(plant),
    )
    assert kpis.avg_lead_time_days >= 0
    assert kpis.p95_lead_time_days >= kpis.p90_lead_time_days >= 0
    assert 1 in kpis.constrained_wc_avg_utilization
    assert 1 in kpis.constrained_wc_ending_backlog_hours
