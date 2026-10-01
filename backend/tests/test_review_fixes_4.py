"""Regression tests for the fourth round: receipt timing for blocked items, stale past-due receipts, routing
and BOM revision gaps, the reference implementation's rules, missing end-date columns and BI temp files."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.analytics import policy as pol
from app.analytics.blocking import BlockingIndex
from app.analytics.capacity import RoutingLoad, compute_required_hours_by_work_centre
from app.analytics.leadtime import LeadTimeCalculator
from app.analytics.period_engine import _bucket_receipts_by_item_period, run_period_engine
from tests.reference_leadtime import reference_compute_lead_time_for_item
from tests.test_period_engine import mini_plant, weeks
from tests.test_policy import _tables

BLOCK_STEEL = BlockingIndex(entity_blocked={("item", "2"): ["negative_inventory"]})


def steel(output):
    return {r.period_start_date: r for r in output.material_results if r.item_id == 2}


# ---- 1: a blocked item's receipt is usable whichever week it arrives in --------------------------------------
@pytest.mark.parametrize("receipt_week", [0, 1])
def test_blocked_item_receipt_is_usable_whether_or_not_it_lands_in_a_demand_week(receipt_week):
    plant = mini_plant(3, steel_initial_inventory=500.0, steel_receipts={(2, weeks(3)[receipt_week]): 100.0})
    plant["blocking_index"] = BLOCK_STEEL
    output = run_period_engine(top_level_demand={weeks(3)[1]: {1: 80.0}}, **plant)
    week1 = steel(output)[weeks(3)[1]]
    assert week1.usable_inventory == (100.0 if receipt_week == 0 else 0.0)   # carried receipt, or this week's
    assert week1.net_requirement == 0                                        # either way the 80 is covered
    assert output.inventory_end_by_period[weeks(3)[1]][2] == pytest.approx(520.0)   # 500 held apart + 20 left


def test_blocked_on_hand_and_blocked_buffer_are_never_usable():
    plant = mini_plant(2, steel_initial_inventory=500.0)
    plant["blocking_index"] = BLOCK_STEEL
    output = run_period_engine(top_level_demand={weeks(2)[0]: {1: 80.0}}, buffer_boost_by_item={2: 300.0}, **plant)
    week0 = steel(output)[weeks(2)[0]]
    assert week0.usable_inventory == 0 and week0.net_requirement == pytest.approx(80.0)
    assert output.inventory_end_by_period[weeks(2)[0]][2] == pytest.approx(800.0)


# ---- 2: long-overdue receipts are excluded and reported ------------------------------------------------------
def test_receipt_overdue_beyond_the_limit_is_excluded_and_listed():
    horizon = weeks(2)
    lines = pd.DataFrame([
        {"item_id": 2, "qty_ordered": 10.0, "qty_received": 0.0, "expected_receipt_date": horizon[0] - dt.timedelta(days=28), "status": "OPEN"},
        {"item_id": 2, "qty_ordered": 70.0, "qty_received": 0.0, "expected_receipt_date": horizon[0] - dt.timedelta(days=29), "status": "OPEN"},
        {"item_id": 3, "qty_ordered": 90.0, "qty_received": 0.0, "expected_receipt_date": dt.date(2019, 3, 1), "status": "OPEN"}])
    stale = []
    assert _bucket_receipts_by_item_period(lines, horizon, 28, stale) == {(2, horizon[0]): 10.0}
    assert stale == [(2, horizon[0] - dt.timedelta(days=29), 70.0), (3, dt.date(2019, 3, 1), 90.0)]


def test_engine_output_lists_stale_receipts_and_does_not_use_them():
    plant = mini_plant(2, steel_initial_inventory=0.0, steel_receipts={(2, dt.date(2019, 3, 4)): 1000.0})
    output = run_period_engine(top_level_demand={weeks(2)[0]: {1: 50.0}}, **plant)
    assert steel(output)[weeks(2)[0]].net_requirement == pytest.approx(50.0)
    assert [(item, qty) for item, _, qty in output.stale_receipts_excluded] == [(2, 1000.0)]


# ---- 3: a week between routing revisions is unknown for capacity and lead time alike --------------------------
GAP_HEADERS = pd.DataFrame([
    {"routing_id": 1, "item_id": 1, "effective_from": dt.date(2020, 1, 1), "effective_to": dt.date(2026, 2, 28)},
    {"routing_id": 2, "item_id": 1, "effective_from": dt.date(2026, 4, 1), "effective_to": None}])
GAP_OPS = pd.DataFrame([
    {"routing_operation_id": op, "routing_id": rid, "seq_no": 1, "work_centre_id": 1, "setup_time_minutes": 0.0,
     "run_time_minutes_per_unit": 6.0, "yield_pct": 1.0, "batch_size": 1, "queue_time_minutes": 0.0, "transfer_time_minutes": 0.0}
    for op, rid in ((11, 1), (21, 2))])


def test_routing_gap_week_reports_operations_excluded_not_zero_load():
    required, excluded = compute_required_hours_by_work_centre({1: 10.0}, GAP_HEADERS, GAP_OPS, BlockingIndex(), dt.date(2026, 3, 16))
    assert required == {} and sorted(excluded) == [11, 21]
    required, excluded = compute_required_hours_by_work_centre({1: 10.0}, GAP_HEADERS, GAP_OPS, BlockingIndex(), dt.date(2026, 4, 6))
    assert required == {1: pytest.approx(1.0)} and excluded == []
    # an item with no routing at all is still simply not loaded (nothing to report)
    assert compute_required_hours_by_work_centre({9: 10.0}, GAP_HEADERS, GAP_OPS, BlockingIndex(), dt.date(2026, 3, 16)) == ({}, [])


def test_routing_gap_week_makes_lead_time_unavailable_in_both_implementations():
    gap, after = dt.date(2026, 3, 16), dt.date(2026, 4, 6)
    calc = LeadTimeCalculator(GAP_HEADERS, GAP_OPS)
    assert calc.compute(1, gap, {}) is None
    assert reference_compute_lead_time_for_item(1, gap, GAP_HEADERS, GAP_OPS, {}) is None
    assert calc.compute(9, gap, {}).total_days == 0.0      # no routing at all: nothing to process


# ---- 5: the reference implementation follows the missing-capacity rule too ------------------------------------
def test_reference_and_calculator_agree_on_a_zero_capacity_week():
    plant = mini_plant(3)
    output = run_period_engine(top_level_demand={w: {1: 100.0} for w in plant["horizon"]}, **plant)
    by_period = {(r.work_centre_id, r.period_start_date): r for r in output.work_centre_results}
    period = plant["horizon"][1]
    by_period[(1, period)].effective_hours_per_workday = 0.0
    args = (plant["routing_headers_df"], plant["routing_operations_df"])
    tables = (plant["items_df"], plant["bom_headers_df"], plant["bom_components_df"])
    assert LeadTimeCalculator(*args, *tables).compute(1, period, by_period) is None
    assert reference_compute_lead_time_for_item(1, period, *args, by_period, *tables) is None


# ---- 4: no BOM revision effective on the evaluation date -> insufficient evidence ------------------------------
def test_policy_blocks_when_no_bom_revision_is_current():
    tables = _tables(tolerance_days=10, fg_days=4, sub_days=6)
    last_order = pol.current_as_of(tables["sales_orders"])
    tables["bom_headers"] = pd.DataFrame({
        "bom_id": [10, 11, 12], "parent_item_id": [1, 2, 4],
        "effective_from": [dt.date(2020, 1, 1), dt.date(2020, 1, 1), dt.date(2020, 1, 1)],
        "effective_to": [last_order - dt.timedelta(days=1), None, None]})       # FG-A's only revision expired
    rec = {r.item_id: r for r in pol.recommend_policies(tables, {1: 0.1})}[1]
    assert rec.policy == pol.INSUFFICIENT
    assert any("No BOM revision of FG-A is effective" in b for b in rec.blockers)


def test_policy_without_any_bom_is_not_blocked_for_structure():
    tables = _tables(tolerance_days=10, fg_days=4, sub_days=6)
    tables["bom_headers"] = tables["bom_headers"].loc[tables["bom_headers"]["parent_item_id"] != 1]
    rec = {r.item_id: r for r in pol.recommend_policies(tables, {1: 0.1})}[1]
    assert not any("No BOM revision" in b for b in rec.blockers)


# ---- 9: a routing table without effective_to is open-ended, not a crash ---------------------------------------
def test_missing_effective_to_column_means_open_ended_revisions():
    headers = GAP_HEADERS.drop(columns=["effective_to"]).iloc[[0]]
    required, _ = RoutingLoad(headers, GAP_OPS).required_hours({1: 10.0}, BlockingIndex(), dt.date(2030, 1, 7))
    assert required == {1: pytest.approx(1.0)}
    assert LeadTimeCalculator(headers, GAP_OPS)._route_candidates[1][0][2] is None   # open-ended, no KeyError


# ---- 7: concurrent exports use their own temporary files --------------------------------------------------------
def test_each_export_uses_unique_temporary_files(tmp_path, monkeypatch):
    import os
    from app.integrations.file_adapters import BI_REQUIRED_METADATA, FileBIExportAdapter
    seen, real_replace = [], os.replace
    monkeypatch.setattr(os, "replace", lambda src, dst: (seen.append(os.path.basename(src)), real_replace(src, dst)))
    adapter = FileBIExportAdapter(tmp_path)
    metadata = {key: "v" for key in BI_REQUIRED_METADATA}
    adapter.export("backlog", pd.DataFrame({"a": [1]}), metadata)
    adapter.export("backlog", pd.DataFrame({"a": [2]}), metadata)
    assert len(set(seen)) == 6 and all(name.endswith(".tmp") for name in seen)
    assert adapter.verify("backlog")


# ---- 10: one connection-URL builder ------------------------------------------------------------------------------
def test_admin_and_app_urls_come_from_the_same_builder(monkeypatch):
    from app.config import settings
    from app.db.security_setup import admin_urls
    monkeypatch.setenv("MSSQL_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("MSSQL_ADMIN_USER", "admin1")
    assert admin_urls("mfg") == (settings.odbc_url("admin1", "pw", "master"), settings.odbc_url("admin1", "pw", "mfg"))
    assert settings.mssql_odbc_url == settings.odbc_url(settings.mssql_user, settings.mssql_password, settings.mssql_database)
