"""Regression tests for the second review round: engine receipts, capacity-data gaps, effective dating,
policy structure, bootstrap, completeness gating and atomic BI export."""
from __future__ import annotations

import datetime as dt
import json
import sys

import pandas as pd
import pytest

from app.analytics import policy as pol
from app.analytics.blocking import BlockingIndex
from app.analytics.capacity import compute_required_hours_by_work_centre
from app.analytics.period_engine import (_bucket_receipts_by_item_period, compute_lead_time_for_item,
                                         run_period_engine)
from tests.test_period_engine import mini_plant, weeks
from tests.test_policy import _tables


def steel_rows(output):
    return {r.period_start_date: r for r in output.material_results if r.item_id == 2}


# ---- 1: a receipt in a zero-demand week is carried forward ------------------------------------------------
def test_receipt_in_a_zero_demand_week_covers_later_demand():
    plant = mini_plant(4, steel_initial_inventory=0.0, steel_receipts={(2, weeks(4)[1]): 160.0})
    demand = {weeks(4)[2]: {1: 160.0}}                       # no demand in weeks 0, 1 and 3
    rows = steel_rows(run_period_engine(top_level_demand=demand, **plant))
    assert rows[weeks(4)[2]].net_requirement == 0
    assert rows[weeks(4)[2]].shortage_flag is False


def test_receipt_in_a_demand_week_is_still_counted_once():
    plant = mini_plant(3, steel_initial_inventory=0.0, steel_receipts={(2, weeks(3)[0]): 160.0})
    demand = {weeks(3)[0]: {1: 100.0}, weeks(3)[1]: {1: 100.0}}
    rows = steel_rows(run_period_engine(top_level_demand=demand, **plant))
    assert rows[weeks(3)[0]].net_requirement == 0
    assert rows[weeks(3)[1]].net_requirement == pytest.approx(40.0)   # 160 received - 100 used = 60 left for 100


# ---- 2: a receipt after the horizon cannot cover demand inside it ------------------------------------------
def test_receipt_beyond_the_horizon_is_not_credited_to_the_last_week():
    horizon = weeks(3)
    lines = pd.DataFrame([
        {"item_id": 2, "qty_ordered": 50.0, "qty_received": 0.0, "expected_receipt_date": horizon[-1] + dt.timedelta(days=6), "status": "OPEN"},
        {"item_id": 2, "qty_ordered": 70.0, "qty_received": 0.0, "expected_receipt_date": horizon[-1] + dt.timedelta(days=7), "status": "OPEN"},
        {"item_id": 2, "qty_ordered": 90.0, "qty_received": 0.0, "expected_receipt_date": horizon[-1] + dt.timedelta(days=60), "status": "OPEN"}])
    assert _bucket_receipts_by_item_period(lines, horizon) == {(2, horizon[-1]): 50.0}


def test_far_future_receipt_leaves_the_final_week_short():
    plant = mini_plant(3, steel_initial_inventory=0.0, steel_receipts={(2, weeks(3)[-1] + dt.timedelta(days=90)): 1000.0})
    rows = steel_rows(run_period_engine(top_level_demand={weeks(3)[2]: {1: 100.0}}, **plant))
    assert rows[weeks(3)[2]].shortage_flag is True


# ---- 3: missing capacity data makes the route unavailable, not optimistic ----------------------------------
def test_lead_time_is_unavailable_when_a_centre_has_no_capacity_period():
    plant = mini_plant(3)
    output = run_period_engine(top_level_demand={w: {1: 100.0} for w in plant["horizon"]}, **plant)
    by_period = {(r.work_centre_id, r.period_start_date): r for r in output.work_centre_results}
    args = (plant["routing_headers_df"], plant["routing_operations_df"])
    tables = (plant["items_df"], plant["bom_headers_df"], plant["bom_components_df"])
    period = plant["horizon"][1]
    assert compute_lead_time_for_item(1, period, *args, by_period, *tables) is not None
    del by_period[(1, period)]
    assert compute_lead_time_for_item(1, period, *args, by_period, *tables) is None


def test_lead_time_is_unavailable_when_a_centre_has_zero_effective_hours():
    plant = mini_plant(3)
    output = run_period_engine(top_level_demand={w: {1: 100.0} for w in plant["horizon"]}, **plant)
    by_period = {(r.work_centre_id, r.period_start_date): r for r in output.work_centre_results}
    period = plant["horizon"][1]
    by_period[(1, period)].effective_hours_per_workday = 0.0
    assert compute_lead_time_for_item(1, period, plant["routing_headers_df"], plant["routing_operations_df"], by_period,
                                      plant["items_df"], plant["bom_headers_df"], plant["bom_components_df"]) is None


# ---- 4: capacity load follows routing effective dates ------------------------------------------------------
def test_required_hours_use_the_routing_revision_effective_that_week():
    headers = pd.DataFrame([
        {"routing_id": 1, "item_id": 1, "effective_from": dt.date(2020, 1, 1), "effective_to": dt.date(2026, 1, 31)},
        {"routing_id": 2, "item_id": 1, "effective_from": dt.date(2026, 2, 1), "effective_to": None}])
    ops = pd.DataFrame([
        {"routing_operation_id": 1, "routing_id": 1, "seq_no": 1, "work_centre_id": 1, "setup_time_minutes": 0.0,
         "run_time_minutes_per_unit": 6.0, "yield_pct": 1.0, "batch_size": 1},
        {"routing_operation_id": 2, "routing_id": 2, "seq_no": 1, "work_centre_id": 1, "setup_time_minutes": 0.0,
         "run_time_minutes_per_unit": 60.0, "yield_pct": 1.0, "batch_size": 1}])
    hours = lambda day: compute_required_hours_by_work_centre({1: 10.0}, headers, ops, BlockingIndex(), day)[0][1]
    assert hours(dt.date(2026, 1, 19)) == pytest.approx(1.0)      # old revision: 10 units * 6 min
    assert hours(dt.date(2026, 2, 2)) == pytest.approx(10.0)      # new revision: 10 units * 60 min


# ---- 5: expired BOM revisions do not feed the policy -------------------------------------------------------
def test_policy_ignores_an_expired_bom_revision():
    tables = _tables(tolerance_days=10, fg_days=4, sub_days=6)
    last_order = pd.to_datetime(tables["sales_orders"]["order_date"]).max().date()
    tables["items"] = pd.concat([tables["items"], pd.DataFrame({"item_id": [5], "item_code": ["SUB-OLD"], "item_type": ["SUBASSY"]})])
    tables["bom_headers"] = pd.DataFrame({
        "bom_id": [10, 11, 12, 13], "parent_item_id": [1, 2, 4, 1],
        "effective_from": [dt.date(2020, 1, 1)] * 3 + [dt.date(2019, 1, 1)],
        "effective_to": [None, None, None, last_order - dt.timedelta(days=30)]})
    tables["bom_components"] = pd.DataFrame({"bom_id": [10, 11, 12, 13], "component_item_id": [2, 3, 2, 5]})
    rec = {r.item_id: r for r in pol.recommend_policies(tables, {1: 0.1})}[1]
    assert rec.policy == pol.MTO and not rec.blockers     # the expired component SUB-OLD has no history but is ignored


# ---- 6: a cyclic BOM is a blocker, not a confident answer --------------------------------------------------
def test_policy_blocks_on_a_bom_cycle():
    tables = _tables(tolerance_days=10, fg_days=4, sub_days=6)
    tables["bom_headers"] = pd.DataFrame({"bom_id": [10, 11, 12, 14], "parent_item_id": [1, 2, 4, 2]})
    tables["bom_components"] = pd.DataFrame({"bom_id": [10, 11, 12, 14], "component_item_id": [2, 3, 2, 1]})   # 1 -> 2 -> 1
    rec = {r.item_id: r for r in pol.recommend_policies(tables, {1: 0.1})}[1]
    assert rec.policy == pol.INSUFFICIENT
    assert any("cycle" in blocker for blocker in rec.blockers)


def test_reaches_cycle_distinguishes_a_shared_component_from_a_cycle():
    assert pol.reaches_cycle(1, {1: {2, 3}, 2: {4}, 3: {4}}) is False   # diamond, not a cycle
    assert pol.reaches_cycle(1, {1: {2}, 2: {3}, 3: {1}}) is True
    assert pol.reaches_cycle(1, {1: {1}}) is True


# ---- 7: the setup command does not depend on the API's own login -------------------------------------------
def test_security_setup_builds_admin_urls_from_admin_credentials(monkeypatch):
    from app.db.security_setup import admin_urls
    monkeypatch.delenv("MSSQL_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("MSSQL_SA_PASSWORD", raising=False)
    with pytest.raises(SystemExit, match="MSSQL_ADMIN_PASSWORD"):
        admin_urls("mfg")
    monkeypatch.setenv("MSSQL_ADMIN_PASSWORD", "p%ss:w/rd")
    monkeypatch.setenv("MSSQL_ADMIN_USER", "admin1")
    master, target = admin_urls("mfg_db")
    assert "admin1:p%25ss%3Aw%2Frd@" in master and "/master?" in master
    assert "/mfg_db?" in target


# ---- 8: a blocking completeness report stops automation and the destructive load --------------------------
def test_completeness_cli_exits_non_zero_on_block_and_zero_on_warn(monkeypatch):
    from app.integration import completeness
    monkeypatch.setattr(sys, "argv", ["completeness", "--source", "csv", "--path", "x"])
    monkeypatch.setattr(completeness, "tables_from_csv", lambda *a: {})
    for overall, code in ((completeness.BLOCK, 1), (completeness.WARN, None), (completeness.PASS, None)):
        monkeypatch.setattr(completeness, "run_checks", lambda *a, _o=overall: {"overall": _o, "counts": {}, "results": []})
        if code is None:
            completeness.main()
        else:
            with pytest.raises(SystemExit) as exit_info:
                completeness.main()
            assert exit_info.value.code == code


def test_loader_refuses_to_drop_anything_when_the_staged_data_blocks(monkeypatch):
    from app.integration import completeness
    from app.loader import load_staging_to_sql as loader
    touched = []
    monkeypatch.setattr(completeness, "tables_from_csv", lambda *a: {})
    monkeypatch.setattr(completeness, "run_checks", lambda *a: {"overall": completeness.BLOCK, "counts": {}, "results": [
        {"status": completeness.BLOCK, "dataset": "items", "check": "required", "detail": "missing file"}]})
    monkeypatch.setattr(loader, "wait_for_sql_server", lambda: touched.append("wait"))
    monkeypatch.setattr(loader, "run_ddl_file", lambda *a: touched.append("ddl"))
    with pytest.raises(SystemExit, match="nothing was dropped"):
        loader.load_all("staging")
    assert touched == []


# ---- 9: a failed export leaves the previous CSV and sidecar untouched --------------------------------------
def test_failed_bi_export_keeps_the_previous_pair(tmp_path, monkeypatch):
    from app.integrations.file_adapters import BI_REQUIRED_METADATA, FileBIExportAdapter
    adapter = FileBIExportAdapter(tmp_path)
    metadata = {key: "v1" for key in BI_REQUIRED_METADATA}
    adapter.export("backlog", pd.DataFrame({"a": [1]}), metadata)
    before = ((tmp_path / "backlog.csv").read_text(), (tmp_path / "backlog.meta.json").read_text())

    real_write_text = type(tmp_path).write_text

    def failing(self, *args, **kwargs):
        if ".meta.json." in self.name and self.name.endswith(".tmp"):
            raise OSError("disk full")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "write_text", failing)
    with pytest.raises(OSError):
        adapter.export("backlog", pd.DataFrame({"a": [999]}), {key: "v2" for key in BI_REQUIRED_METADATA})
    assert ((tmp_path / "backlog.csv").read_text(), (tmp_path / "backlog.meta.json").read_text()) == before
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads(before[1])[next(iter(BI_REQUIRED_METADATA))] == "v1"
