"""Regression tests for the third round: blocked stock, past-due receipts, overlapping routing revisions,
verifiable BI export pairs, and the indexed engine helpers (which must give exactly the old answers)."""
from __future__ import annotations

import datetime as dt
import itertools
import json
import os

import pandas as pd
import pytest

from app.analytics.blocking import BlockingIndex
from app.analytics.bom import active_bom_headers
from app.analytics.capacity import CapacityCalendar, compute_required_hours_by_work_centre
from app.analytics.mrp import BomExploder
from app.analytics.netting import net_requirement, net_single_position
from app.analytics.period_engine import _bucket_receipts_by_item_period, run_period_engine
from tests.test_period_engine import mini_plant, weeks


# ---- blocked stock is carried, not overwritten -------------------------------------------------------------
def test_blocked_stock_is_carried_forward_when_receipts_cover_the_demand():
    plant = mini_plant(2, steel_initial_inventory=500.0, steel_receipts={(2, weeks(2)[0]): 100.0})
    plant["blocking_index"] = BlockingIndex(entity_blocked={("item", "2"): ["negative_inventory"]})
    output = run_period_engine(top_level_demand={weeks(2)[0]: {1: 50.0}}, **plant)
    steel = next(r for r in output.material_results if r.item_id == 2)
    assert steel.usable_inventory == 0                       # blocked stock is never used ...
    assert steel.net_requirement == 0                        # ... the receipt covers the 50
    assert output.inventory_end_by_period[weeks(2)[0]][2] == pytest.approx(550.0)   # ... and the 500 is still there


# ---- past-due receipts are available from the first week ---------------------------------------------------
def test_past_due_receipt_is_bucketed_into_the_first_week():
    horizon = weeks(3)
    lines = pd.DataFrame([{"item_id": 2, "qty_ordered": 80.0, "qty_received": 20.0,
                           "expected_receipt_date": horizon[0] - dt.timedelta(days=10), "status": "PARTIAL"}])
    assert _bucket_receipts_by_item_period(lines, horizon) == {(2, horizon[0]): 60.0}


def test_past_due_receipt_covers_first_week_demand():
    plant = mini_plant(2, steel_initial_inventory=0.0, steel_receipts={(2, weeks(2)[0] - dt.timedelta(days=21)): 100.0})
    output = run_period_engine(top_level_demand={weeks(2)[0]: {1: 100.0}}, **plant)
    steel = next(r for r in output.material_results if r.item_id == 2)
    assert steel.scheduled_receipts == 100.0 and steel.net_requirement == 0


# ---- overlapping routing revisions: capacity and lead time agree that the answer is unknown ----------------
def test_overlapping_routing_revisions_are_excluded_from_load_like_lead_time():
    headers = pd.DataFrame([
        {"routing_id": 1, "item_id": 1, "effective_from": dt.date(2020, 1, 1), "effective_to": None},
        {"routing_id": 2, "item_id": 1, "effective_from": dt.date(2026, 1, 1), "effective_to": None}])
    ops = pd.DataFrame([
        {"routing_operation_id": 11, "routing_id": 1, "seq_no": 1, "work_centre_id": 1, "setup_time_minutes": 0.0,
         "run_time_minutes_per_unit": 6.0, "yield_pct": 1.0, "batch_size": 1, "queue_time_minutes": 0.0, "transfer_time_minutes": 0.0},
        {"routing_operation_id": 21, "routing_id": 2, "seq_no": 1, "work_centre_id": 1, "setup_time_minutes": 0.0,
         "run_time_minutes_per_unit": 60.0, "yield_pct": 1.0, "batch_size": 1, "queue_time_minutes": 0.0, "transfer_time_minutes": 0.0}])
    required, excluded = compute_required_hours_by_work_centre({1: 10.0}, headers, ops, BlockingIndex(), dt.date(2026, 2, 2))
    assert required == {} and sorted(excluded) == [11, 21]
    from app.analytics.leadtime import LeadTimeCalculator
    assert LeadTimeCalculator(headers, ops).compute(1, dt.date(2026, 2, 2), {}) is None


# ---- BI export pairs are verifiable ------------------------------------------------------------------------
def _bi(tmp_path):
    from app.integrations.file_adapters import BI_REQUIRED_METADATA, FileBIExportAdapter
    return FileBIExportAdapter(tmp_path), {key: "v" for key in BI_REQUIRED_METADATA}


def test_bi_sidecar_records_the_csv_hash_and_verifies(tmp_path):
    adapter, metadata = _bi(tmp_path)
    adapter.export("backlog", pd.DataFrame({"a": [1, 2]}), metadata)
    assert "csv_sha256" in json.loads((tmp_path / "backlog.meta.json").read_text())
    assert adapter.verify("backlog")
    (tmp_path / "backlog.csv").write_text("a\n999\n")
    assert not adapter.verify("backlog")
    assert not adapter.verify("missing")


def test_a_crash_between_the_two_renames_is_detected(tmp_path, monkeypatch):
    adapter, metadata = _bi(tmp_path)
    adapter.export("backlog", pd.DataFrame({"a": [1]}), metadata)
    real_replace, calls = os.replace, []

    def crash_on_second(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("power cut")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", crash_on_second)
    with pytest.raises(OSError):
        adapter.export("backlog", pd.DataFrame({"a": [2]}), metadata)
    monkeypatch.setattr(os, "replace", real_replace)
    assert not adapter.verify("backlog")          # new CSV, old sidecar: reported, never silently trusted
    assert not list(tmp_path.glob("*.tmp"))


# ---- the indexed helpers give exactly the old answers -------------------------------------------------------
def test_scalar_netting_matches_the_dataframe_netting():
    day = dt.date(2026, 6, 1)
    for gross, on_hand, receipts in itertools.product([0.0, 5.0, 37.25, 1000.0], [-20.0, 0.0, 3.5, 40.0, 5000.0], [0.0, 2.0, 60.0]):
        inv = pd.DataFrame([{"on_hand_qty": on_hand, "is_blocked": False}]) if on_hand != 0 else pd.DataFrame(columns=["on_hand_qty", "is_blocked"])
        po = pd.DataFrame([{"qty_ordered": receipts, "qty_received": 0.0, "expected_receipt_date": day, "status": "OPEN"}]) \
            if receipts > 0 else pd.DataFrame(columns=["qty_ordered", "qty_received", "expected_receipt_date", "status"])
        old = net_requirement(1, "X", gross, day, inv, po)
        assert net_single_position(gross, on_hand, receipts) == (old.usable_inventory, old.scheduled_receipts, old.net_requirement)


def test_bom_exploder_selects_the_same_revision_as_active_bom_headers(tables):
    exploder = BomExploder(tables["items"], tables["bom_headers"], tables["bom_components"])
    dates = [dt.date(2025, 1, 6), dt.date(2026, 6, 1), dt.date(2026, 8, 17)]
    for parent in tables["bom_headers"]["parent_item_id"].unique():
        for day in dates:
            headers = active_bom_headers(int(parent), day, tables["bom_headers"])
            result = exploder.explode_one_level(int(parent), 1.0, day)
            if headers.empty:
                assert result.is_leaf
            elif len(headers) > 1:
                assert result.incomplete and result.blocked_reason == "ambiguous_bom_revision"
            else:
                comps = tables["bom_components"].loc[tables["bom_components"]["bom_id"] == headers.iloc[0]["bom_id"]]
                assert [c.component_item_id for c in result.children] == [int(x) for x in comps["component_item_id"]]


def test_capacity_calendar_matches_a_direct_filter(tables):
    frame = tables["capacity_calendar"]
    calendar = CapacityCalendar(frame)
    weeks_as_dates = pd.to_datetime(frame["week_start_date"]).dt.date
    for wc_id, week in list(zip(frame["work_centre_id"], weeks_as_dates))[::37]:
        row = frame.loc[(frame["work_centre_id"] == wc_id) & (weeks_as_dates == week)].iloc[0]
        result = calendar.compute(int(wc_id), week, 10.0, BlockingIndex())
        assert (result.calendar_hours, result.available_hours, result.effective_hours) == (
            float(row["calendar_hours"]), float(row["available_hours"]), float(row["effective_hours"]))
    assert pd.isna(calendar.compute(999999, dt.date(2026, 1, 5), 1.0, BlockingIndex()).effective_hours)
