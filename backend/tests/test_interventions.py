"""CP3.1 req. D/E: capacity intervention timing (start-week-scoped), buffer
vs. capacity distinction, and the smallest-recovering-intervention search.
Reuses test_period_engine's mini-plant fixture for exact, hand-verifiable
numbers.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.analytics.period_engine import run_period_engine
from tests.test_period_engine import mini_plant


def test_capacity_multiplier_with_start_week_only_applies_from_that_week():
    plant = mini_plant(6)
    demand = {w: {1: 160.0} for w in plant["horizon"]}  # sustained overload throughout
    start_week = plant["horizon"][3]

    output = run_period_engine(
        top_level_demand=demand,
        capacity_multiplier_by_work_centre={1: (2.0, start_week)},
        **plant,
    )
    series = output.wc_series(1)
    for r in series:
        if r.period_start_date < start_week:
            assert r.effective_hours == pytest.approx(40.0)  # unmodified
        else:
            assert r.effective_hours == pytest.approx(80.0)  # 2x from start_week onward


def test_buffer_boost_does_not_change_effective_capacity():
    """[CP3.1 req. E] Buffer-only must not create machine capacity."""
    plant = mini_plant(6)
    demand = {w: {1: 160.0} for w in plant["horizon"]}

    baseline = run_period_engine(top_level_demand=demand, **plant)
    buffered = run_period_engine(top_level_demand=demand, buffer_boost_by_item={2: 500.0}, **plant)

    for b, s in zip(baseline.wc_series(1), buffered.wc_series(1)):
        assert b.effective_hours == pytest.approx(s.effective_hours)
        assert b.calendar_hours == pytest.approx(s.calendar_hours)


def test_buffer_only_delays_but_does_not_resolve_sustained_overload():
    """A one-time inventory boost smooths the early weeks (less material
    shortage pressure is irrelevant here since this fixture's bottleneck is
    pure work-centre capacity, not material -- so a material buffer on the
    RAW input should have *no* effect on a capacity-driven backlog at all,
    which is exactly the point: buffer only helps where the limiting factor
    is material, never where it's a physical capacity ceiling)."""
    plant = mini_plant(8)
    demand = {w: {1: 160.0} for w in plant["horizon"]}  # sustained capacity overload, ample steel

    baseline = run_period_engine(top_level_demand=demand, **plant)
    buffered = run_period_engine(top_level_demand=demand, buffer_boost_by_item={2: 100000.0}, **plant)

    # Backlog trajectory identical -- a huge material buffer cannot touch a
    # work-centre-hours constraint.
    for b, s in zip(baseline.wc_series(1), buffered.wc_series(1)):
        assert b.backlog_hours_end == pytest.approx(s.backlog_hours_end)
        assert b.constraint_classification == s.constraint_classification


def test_capacity_intervention_resolves_a_sustained_overload_that_buffer_cannot():
    plant = mini_plant(10)
    demand = {w: {1: 160.0} for w in plant["horizon"]}
    start_week = plant["horizon"][5]  # let backlog build up for several weeks first

    with_capacity = run_period_engine(
        top_level_demand=demand, capacity_multiplier_by_work_centre={1: (2.0, start_week)}, **plant
    )
    series = with_capacity.wc_series(1)
    peak_backlog = series[4].backlog_hours_end  # last week before the intervention takes effect
    assert peak_backlog > 0  # confirms backlog genuinely built up before the intervention
    # Required ~42.67h/week vs boosted effective 80h/week from week 5 on --
    # utilization drops comfortably under 1, backlog must stop growing and
    # decline back toward zero.
    assert series[-1].utilization_pct < 1.0
    assert series[-1].backlog_hours_end < peak_backlog


def test_smallest_recovering_intervention_search_finds_peak_then_decline():
    from app.analytics.scenario_demo import find_smallest_recovering_capacity_intervention

    plant = mini_plant(14)
    demand = {w: {1: 160.0} for w in plant["horizon"]}
    start_week = plant["horizon"][3]

    scenario_inputs = dict(plant)
    scenario_inputs["top_level_demand"] = demand

    result = find_smallest_recovering_capacity_intervention(
        scenario_inputs, work_centre_ids=[1], intervention_start_week=start_week,
        candidate_multipliers=[1.05, 1.1, 1.2, 1.5, 2.0, 3.0],
    )
    assert result["multiplier"] is not None
    series = result["combined_backlog_series"]
    peak_idx = result["peak_index"]
    assert peak_idx < len(series) - 1
    assert series[-1] < result["peak"]
