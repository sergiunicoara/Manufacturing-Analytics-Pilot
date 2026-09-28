"""CP3.1 req. A: cascading multi-level MRP netting golden tests.

The exact invariant requested: demand for a parent nets against the parent's
own inventory first, and only the parent's NET requirement (not its gross)
cascades down to explode its components — so a subassembly with on-hand
inventory measurably reduces what's required beneath it.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.analytics.blocking import BlockingIndex
from app.analytics.mrp import compute_low_level_codes, explode_one_level
from app.analytics.period_engine import run_period_engine

AS_OF = dt.date(2026, 1, 5)


def _three_level_plant(frame_inventory: float, n_weeks: int = 1):
    """CAB (FG) -> FRAME (SUBASSY, qty_per=1) -> STEEL (RAW, qty_per=2,
    scrap=0.1). No routing/work-centre load needed for this test — it only
    exercises the netting/explosion cascade, so a trivial 1-op routing on a
    single work centre with ample capacity is enough to keep the engine
    happy without capacity effects muddying the material-side assertions.
    """
    items = pd.DataFrame([
        {"item_id": 1, "item_code": "CAB", "description": "Cabinet", "item_type": "FG", "uom": "PC", "product_family": None, "is_active": True},
        {"item_id": 2, "item_code": "FRAME", "description": "Frame", "item_type": "SUBASSY", "uom": "PC", "product_family": None, "is_active": True},
        {"item_id": 3, "item_code": "STEEL", "description": "Steel", "item_type": "RAW", "uom": "KG", "product_family": None, "is_active": True},
    ])
    bom_headers = pd.DataFrame([
        {"bom_id": 1, "parent_item_id": 1, "revision": "A", "status": "ACTIVE", "effective_from": dt.date(2020, 1, 1), "effective_to": None},
        {"bom_id": 2, "parent_item_id": 2, "revision": "A", "status": "ACTIVE", "effective_from": dt.date(2020, 1, 1), "effective_to": None},
    ])
    bom_components = pd.DataFrame([
        {"bom_component_id": 1, "bom_id": 1, "component_item_id": 2, "quantity_per": 1.0, "scrap_pct": 0.0,
         "effective_from": dt.date(2020, 1, 1), "effective_to": None},
        {"bom_component_id": 2, "bom_id": 2, "component_item_id": 3, "quantity_per": 2.0, "scrap_pct": 0.1,
         "effective_from": dt.date(2020, 1, 1), "effective_to": None},
    ])
    routing_headers = pd.DataFrame([
        {"routing_id": 1, "item_id": 1, "revision": "A", "effective_from": dt.date(2020, 1, 1), "effective_to": None},
        {"routing_id": 2, "item_id": 2, "revision": "A", "effective_from": dt.date(2020, 1, 1), "effective_to": None},
    ])
    routing_operations = pd.DataFrame([
        {"routing_operation_id": 1, "routing_id": 1, "seq_no": 1, "work_centre_id": 1, "operation_name": "Assemble",
         "setup_time_minutes": 5.0, "run_time_minutes_per_unit": 1.0, "queue_time_minutes": 30.0,
         "transfer_time_minutes": 5.0, "yield_pct": 1.0, "batch_size": 100},
        {"routing_operation_id": 2, "routing_id": 2, "seq_no": 1, "work_centre_id": 1, "operation_name": "Weld",
         "setup_time_minutes": 5.0, "run_time_minutes_per_unit": 1.0, "queue_time_minutes": 30.0,
         "transfer_time_minutes": 5.0, "yield_pct": 1.0, "batch_size": 100},
    ])
    work_centres = pd.DataFrame([
        {"work_centre_id": 1, "work_centre_code": "WC-1", "name": "WC 1", "site_id": 1, "process_type": "ASSEMBLY",
         "shifts_per_day": 3, "hours_per_shift": 8.0, "days_per_week": 7, "cost_per_hour": 50.0},
    ])
    horizon = [AS_OF + dt.timedelta(weeks=i) for i in range(n_weeks)]
    capacity_calendar = pd.DataFrame([
        {"work_centre_id": 1, "week_start_date": w, "calendar_hours": 1000.0, "planned_downtime_hours": 0.0,
         "availability_pct": 1.0, "available_hours": 1000.0, "effective_hours": 1000.0}
        for w in horizon
    ])
    purchase_order_lines = pd.DataFrame(columns=["item_id", "qty_ordered", "qty_received", "expected_receipt_date", "status"])

    return dict(
        horizon=horizon, items_df=items, bom_headers_df=bom_headers, bom_components_df=bom_components,
        routing_headers_df=routing_headers, routing_operations_df=routing_operations, work_centres_df=work_centres,
        capacity_calendar_df=capacity_calendar, purchase_order_lines_df=purchase_order_lines,
        initial_inventory={2: frame_inventory}, blocking_index=BlockingIndex(),
    )


def test_worked_example_frame_inventory_reduces_downstream_steel_requirement():
    """The exact CP3.1 example: demand CAB=100, FRAME qty/CAB=1, usable
    FRAME inventory=80 -> component requirements below FRAME are generated
    for 20 FRAME, not 100."""
    plant = _three_level_plant(frame_inventory=80.0)
    demand = {plant["horizon"][0]: {1: 100.0}}
    output = run_period_engine(top_level_demand=demand, **plant)

    frame_result = output.material_series(2)[0]
    steel_result = output.material_series(3)[0]

    assert frame_result.gross_requirement == pytest.approx(100.0)
    assert frame_result.usable_inventory == pytest.approx(80.0)
    assert frame_result.net_requirement == pytest.approx(20.0)  # not 100

    # STEEL gross must be driven by FRAME's NET (20), not FRAME's gross (100):
    # 20 * quantity_per(2.0) / (1 - scrap_pct(0.1)) = 44.444...
    expected_steel_gross = 20.0 * 2.0 / (1 - 0.1)
    assert steel_result.gross_requirement == pytest.approx(expected_steel_gross)
    assert steel_result.gross_requirement != pytest.approx(100.0 * 2.0 / 0.9)  # the old (wrong) full-gross answer


def test_zero_subassembly_inventory_falls_back_to_full_cascading_demand():
    plant = _three_level_plant(frame_inventory=0.0)
    demand = {plant["horizon"][0]: {1: 100.0}}
    output = run_period_engine(top_level_demand=demand, **plant)

    frame_result = output.material_series(2)[0]
    steel_result = output.material_series(3)[0]
    assert frame_result.net_requirement == pytest.approx(100.0)
    assert steel_result.gross_requirement == pytest.approx(100.0 * 2.0 / 0.9)


def test_inventory_exceeding_demand_produces_zero_downstream_cascade():
    plant = _three_level_plant(frame_inventory=500.0)
    demand = {plant["horizon"][0]: {1: 100.0}}
    output = run_period_engine(top_level_demand=demand, **plant)

    frame_result = output.material_series(2)[0]
    assert frame_result.net_requirement == 0.0
    # STEEL should not even appear as a material result -- nothing cascaded.
    assert output.material_series(3) == []


def test_frame_inventory_carries_forward_and_is_further_reduced_next_period():
    """Two weeks, no replenishment: week 1 consumes inventory down toward
    net=0, so week 2 (same demand) should show LESS inventory available and
    a correspondingly larger net requirement than week 1 -- state genuinely
    carries forward through the cascading netting path, not just the old
    single-level one."""
    plant = _three_level_plant(frame_inventory=80.0, n_weeks=2)
    demand = {w: {1: 100.0} for w in plant["horizon"]}
    output = run_period_engine(top_level_demand=demand, **plant)

    week1, week2 = output.material_series(2)
    assert week1.usable_inventory == pytest.approx(80.0)
    assert week1.net_requirement == pytest.approx(20.0)
    assert week2.usable_inventory == pytest.approx(0.0)  # consumed in week 1
    assert week2.net_requirement == pytest.approx(100.0)  # full demand now


def test_low_level_codes_reflect_deepest_appearance():
    items = pd.DataFrame([
        {"item_id": 1, "item_type": "FG"}, {"item_id": 2, "item_type": "SUBASSY"},
        {"item_id": 3, "item_type": "RAW"},
    ])
    boms = pd.DataFrame([
        {"bom_id": 1, "parent_item_id": 1}, {"bom_id": 2, "parent_item_id": 2},
    ])
    comps = pd.DataFrame([
        {"bom_id": 1, "component_item_id": 2}, {"bom_id": 2, "component_item_id": 3},
    ])
    llc = compute_low_level_codes(items, boms, comps)
    assert llc[1] == 0
    assert llc[2] == 1
    assert llc[3] == 2


def test_low_level_code_uses_the_deepest_of_multiple_parents():
    """A shared raw material used both directly by the FG and deep in a
    subassembly chain gets the DEEPER of the two low-level codes -- it must
    not be netted/exploded until every parent, at every depth, has run."""
    items = pd.DataFrame([
        {"item_id": 1, "item_type": "FG"}, {"item_id": 2, "item_type": "SUBASSY"},
        {"item_id": 3, "item_type": "RAW"},  # shared: used by both item 1 directly and item 2
    ])
    boms = pd.DataFrame([
        {"bom_id": 1, "parent_item_id": 1}, {"bom_id": 2, "parent_item_id": 2},
    ])
    comps = pd.DataFrame([
        {"bom_id": 1, "component_item_id": 2},  # FG -> SUBASSY
        {"bom_id": 1, "component_item_id": 3},  # FG -> RAW directly (would suggest LLC=1)
        {"bom_id": 2, "component_item_id": 3},  # SUBASSY -> RAW (suggests LLC=2 -- the deeper one wins)
    ])
    llc = compute_low_level_codes(items, boms, comps)
    assert llc[3] == 2


def test_low_level_code_cycle_guard_terminates():
    items = pd.DataFrame([{"item_id": 1, "item_type": "SUBASSY"}, {"item_id": 2, "item_type": "SUBASSY"}])
    boms = pd.DataFrame([{"bom_id": 1, "parent_item_id": 1}, {"bom_id": 2, "parent_item_id": 2}])
    comps = pd.DataFrame([{"bom_id": 1, "component_item_id": 2}, {"bom_id": 2, "component_item_id": 1}])
    llc = compute_low_level_codes(items, boms, comps)  # must not hang
    assert set(llc.keys()) == {1, 2}


def test_explode_one_level_applies_scrap_and_does_not_recurse():
    items = pd.DataFrame([
        {"item_id": 1, "item_code": "A", "item_type": "SUBASSY"},
        {"item_id": 2, "item_code": "B", "item_type": "RAW"},
    ])
    boms = pd.DataFrame([{"bom_id": 1, "parent_item_id": 1, "revision": "A", "effective_from": dt.date(2020, 1, 1), "effective_to": None}])
    comps = pd.DataFrame([{"bom_component_id": 1, "bom_id": 1, "component_item_id": 2, "quantity_per": 3.0, "scrap_pct": 0.25,
                            "effective_from": dt.date(2020, 1, 1), "effective_to": None}])
    result = explode_one_level(1, 10.0, AS_OF, items, boms, comps)
    assert len(result.children) == 1
    assert result.children[0].gross_qty == pytest.approx(10.0 * 3.0 / 0.75)


def test_blocked_item_does_not_cascade_demand_to_its_components():
    plant = _three_level_plant(frame_inventory=0.0)
    blocking = BlockingIndex()
    blocking.entity_blocked[("item", "2")] = ["overlapping_bom_revisions"]
    plant["blocking_index"] = blocking

    demand = {plant["horizon"][0]: {1: 100.0}}
    output = run_period_engine(top_level_demand=demand, **plant)

    # FRAME's own material result is still recorded (gross demand is real)...
    frame_result = output.material_series(2)[0]
    assert frame_result.gross_requirement == pytest.approx(100.0)
    # ...but blocked means treated as having zero usable inventory (can't
    # trust it) AND its net requirement must not cascade into STEEL.
    assert output.material_series(3) == []
