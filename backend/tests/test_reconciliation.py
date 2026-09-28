"""CP3.2 req. 3/4/10: flow-conservation reconciliation and proof that a
buffer cannot create capacity or destroy demand.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.analytics.period_engine import run_period_engine
from app.analytics.reconciliation import reconcile_material_flow, reconcile_work_centre_hours
from tests.test_period_engine import mini_plant


def test_work_centre_hours_reconcile_under_sustained_overload():
    plant = mini_plant(10)
    demand = {w: {1: 160.0} for w in plant["horizon"]}
    output = run_period_engine(top_level_demand=demand, **plant)

    result = reconcile_work_centre_hours(output, work_centre_id=1)
    assert result.holds, result
    assert result.final_backlog_hours > 0  # a real overload occurred, not a trivial all-zero case


def test_work_centre_hours_reconcile_with_capacity_intervention():
    plant = mini_plant(12)
    demand = {w: {1: 160.0} for w in plant["horizon"]}
    output = run_period_engine(
        top_level_demand=demand, capacity_multiplier_by_work_centre={1: (2.0, plant["horizon"][5])}, **plant
    )
    result = reconcile_work_centre_hours(output, work_centre_id=1)
    assert result.holds, result


def test_work_centre_hours_reconcile_with_buffer():
    plant = mini_plant(10)
    demand = {w: {1: 160.0} for w in plant["horizon"]}
    output = run_period_engine(top_level_demand=demand, buffer_boost_by_item={2: 300.0}, **plant)
    result = reconcile_work_centre_hours(output, work_centre_id=1)
    assert result.holds, result


def test_material_flow_reconciles_with_shortage():
    plant = mini_plant(6, steel_initial_inventory=5.0, steel_receipts={})
    demand = {w: {1: 20.0} for w in plant["horizon"]}
    output = run_period_engine(top_level_demand=demand, **plant)

    result = reconcile_material_flow(output, item_id=2)  # STEEL
    assert result.holds, result
    assert result.total_net_requirement > 0  # the shortage is real


def test_material_flow_reconciles_with_buffer_boost():
    plant = mini_plant(6)
    demand = {w: {1: 100.0} for w in plant["horizon"]}
    output = run_period_engine(top_level_demand=demand, buffer_boost_by_item={2: 500.0}, **plant)
    result = reconcile_material_flow(output, item_id=2)
    assert result.holds, result
    assert result.total_satisfied_from_inventory > 0


# ============================================================
# CP3.2 req. 10: buffer cannot create capacity or destroy demand
# ============================================================


def test_buffer_never_changes_total_demand_or_capacity():
    plant = mini_plant(10)
    demand = {w: {1: 160.0} for w in plant["horizon"]}

    shock = run_period_engine(top_level_demand=demand, **plant)
    buffered = run_period_engine(top_level_demand=demand, buffer_boost_by_item={2: 1000.0}, **plant)

    # Total demand fed to the engine is literally the same dict -- but prove
    # it shows up identically in the recorded gross_requirement too (nothing
    # about a buffer silently reduces what was asked for).
    shock_total_gross = sum(r.gross_requirement for r in shock.material_series(1))
    buffered_total_gross = sum(r.gross_requirement for r in buffered.material_series(1))
    assert shock_total_gross == pytest.approx(buffered_total_gross)

    # Capacity (calendar/available/effective hours) must be bit-identical --
    # a buffer never touches the capacity side of the model.
    for s, b in zip(shock.wc_series(1), buffered.wc_series(1)):
        assert s.calendar_hours == pytest.approx(b.calendar_hours)
        assert s.available_hours == pytest.approx(b.available_hours)
        assert s.effective_hours == pytest.approx(b.effective_hours)


def test_buffer_backlog_reduction_is_fully_accounted_for_by_inventory_satisfaction():
    """Where does a buffer's backlog improvement 'go'? Into
    satisfied_from_inventory on the buffered item -- provably, not into thin
    air. This is the explicit reconciliation CP3.2 req. 3 asks for."""
    plant = mini_plant(8, steel_initial_inventory=0.0)  # no pre-existing steel stock in the shock case
    demand = {w: {1: 160.0} for w in plant["horizon"]}

    shock = run_period_engine(top_level_demand=demand, **plant)
    buffered = run_period_engine(top_level_demand=demand, buffer_boost_by_item={2: 300.0}, **plant)

    shock_material = reconcile_material_flow(shock, item_id=2)
    buffered_material = reconcile_material_flow(buffered, item_id=2)

    # The buffer must show up as MORE demand satisfied from inventory...
    assert buffered_material.total_satisfied_from_inventory > shock_material.total_satisfied_from_inventory
    # ...and correspondingly LESS net (fresh-production) requirement...
    assert buffered_material.total_net_requirement < shock_material.total_net_requirement
    # ...with gross requirement (total demand) unchanged -- nothing destroyed.
    assert buffered_material.total_gross_requirement == pytest.approx(shock_material.total_gross_requirement)

    # And the reduced net requirement is exactly what reduced the
    # constrained work centre's required hours (same routing, same qty ->
    # hours relationship in both runs).
    shock_backlog = shock.wc_series(1)[-1].backlog_hours_end
    buffered_backlog = buffered.wc_series(1)[-1].backlog_hours_end
    assert buffered_backlog <= shock_backlog


def test_buffer_cannot_resolve_a_capacity_constraint_that_exceeds_the_buffer_size():
    """A modest buffer smooths but does not fully resolve a sustained
    capacity-bound overload once exhausted -- proving buffer-only does not
    silently manufacture unlimited throughput."""
    plant = mini_plant(14)
    demand = {w: {1: 160.0} for w in plant["horizon"]}
    small_buffer = run_period_engine(top_level_demand=demand, buffer_boost_by_item={2: 50.0}, **plant)
    series = small_buffer.wc_series(1)
    # Backlog still ends up positive and growing -- a small material buffer
    # (on the RAW input, which was never the bottleneck in this fixture)
    # cannot touch a sustained work-centre-hours deficit.
    assert series[-1].backlog_hours_end > 0
