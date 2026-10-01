"""Regression tests for the fifth round: loader preflight covers every file the load needs, policy checks the
whole current BOM structure, and a BI export always leaves a complete verified pair to read."""
from __future__ import annotations

import datetime as dt
import os
import shutil

import pandas as pd
import pytest

from app.analytics import policy as pol
from app.loader import load_staging_to_sql as loader
from tests.test_policy import _tables

STAGING = os.environ.get("PILOT_STAGING_DIR", "staging")


# ---- loader: every file the load needs is read before anything is dropped ------------------------------------
@pytest.fixture
def staged_copy(tmp_path):
    if not all(os.path.isfile(os.path.join(STAGING, f"{t}.csv")) for t in loader.TABLE_ORDER):
        pytest.skip("no complete staging directory in this layout")
    for table in loader.TABLE_ORDER:
        shutil.copyfile(os.path.join(STAGING, f"{table}.csv"), tmp_path / f"{table}.csv")
    return tmp_path


def _no_database(monkeypatch):
    touched = []
    monkeypatch.setattr(loader, "wait_for_sql_server", lambda: touched.append("wait"))
    monkeypatch.setattr(loader, "run_ddl_file", lambda *a: touched.append("ddl"))
    return touched


@pytest.mark.parametrize("skip_completeness", [False, True])
def test_missing_loader_only_file_stops_before_the_drop(staged_copy, monkeypatch, skip_completeness):
    (staged_copy / "warehouses.csv").unlink()           # not in the completeness contract, but the loader needs it
    touched = _no_database(monkeypatch)
    with pytest.raises(SystemExit, match="warehouses.csv is missing"):
        loader.load_all(str(staged_copy), check_completeness=not skip_completeness)
    assert touched == []


def test_unreadable_file_stops_before_the_drop(staged_copy, monkeypatch):
    (staged_copy / "suppliers.csv").write_text("")      # empty file: pandas cannot parse it
    touched = _no_database(monkeypatch)
    with pytest.raises(SystemExit, match="suppliers.csv cannot be read"):
        loader.load_all(str(staged_copy))
    assert touched == []


def test_complete_staging_reads_every_table():
    if not all(os.path.isfile(os.path.join(STAGING, f"{t}.csv")) for t in loader.TABLE_ORDER):
        pytest.skip("no complete staging directory in this layout")
    frames = loader.read_staged_tables(STAGING)
    assert list(frames) == loader.TABLE_ORDER and all(len(frame.columns) for frame in frames.values())


# ---- policy: the whole current structure is checked ---------------------------------------------------------
def _dated(tables, headers):
    tables["bom_headers"] = pd.DataFrame(headers, columns=["bom_id", "parent_item_id", "effective_from", "effective_to"])
    return tables


def _fg_a(tables):
    return {r.item_id: r for r in pol.recommend_policies(tables, {1: 0.1})}[1]


def test_overlapping_revisions_on_a_subassembly_block_the_policy():
    tables = _tables(tolerance_days=10, fg_days=4, sub_days=6)
    start = dt.date(2020, 1, 1)
    tables = _dated(tables, [(10, 1, start, None), (11, 2, start, None), (12, 4, start, None), (13, 2, start, None)])
    tables["bom_components"] = pd.DataFrame({"bom_id": [10, 11, 12, 13], "component_item_id": [2, 3, 2, 3]})
    rec = _fg_a(tables)
    assert rec.policy == pol.INSUFFICIENT
    assert any("SUB-A has 2 overlapping BOM revisions" in b for b in rec.blockers)


def test_overlapping_revisions_on_the_finished_good_block_the_policy():
    tables = _tables(tolerance_days=10, fg_days=4, sub_days=6)
    start = dt.date(2020, 1, 1)
    tables = _dated(tables, [(10, 1, start, None), (11, 2, start, None), (12, 4, start, None), (14, 1, start, None)])
    tables["bom_components"] = pd.DataFrame({"bom_id": [10, 11, 12, 14], "component_item_id": [2, 3, 2, 2]})
    assert any("FG-A has 2 overlapping BOM revisions" in b for b in _fg_a(tables).blockers)


def test_expired_bom_on_a_required_subassembly_blocks_the_policy():
    tables = _tables(tolerance_days=10, fg_days=4, sub_days=6)
    last_order = pol.current_as_of(tables["sales_orders"])
    start = dt.date(2020, 1, 1)
    tables = _dated(tables, [(10, 1, start, None), (11, 2, start, last_order - dt.timedelta(days=5)), (12, 4, start, None)])
    rec = _fg_a(tables)
    assert rec.policy == pol.INSUFFICIENT
    assert any("No BOM revision of SUB-A is effective" in b for b in rec.blockers)


def test_a_clean_current_structure_still_gets_a_policy():
    tables = _tables(tolerance_days=10, fg_days=4, sub_days=6)
    start = dt.date(2020, 1, 1)
    tables = _dated(tables, [(10, 1, start, None), (11, 2, start, None), (12, 4, start, None),
                             (15, 2, dt.date(2019, 1, 1), dt.date(2019, 12, 31))])   # an old, expired revision is fine
    tables["bom_components"] = pd.DataFrame({"bom_id": [10, 11, 12, 15], "component_item_id": [2, 3, 2, 3]})
    rec = _fg_a(tables)
    assert rec.policy == pol.MTO and not rec.blockers


# ---- BI: a complete verified pair survives an interruption at any point -------------------------------------
def _adapter(tmp_path):
    from app.integrations.file_adapters import BI_REQUIRED_METADATA, FileBIExportAdapter
    return FileBIExportAdapter(tmp_path), {key: "v" for key in BI_REQUIRED_METADATA}


@pytest.mark.parametrize("crash_at", [1, 2, 3, 4])
def test_an_interrupted_export_always_leaves_a_valid_pair(tmp_path, monkeypatch, crash_at):
    adapter, metadata = _adapter(tmp_path)
    adapter.export("backlog", pd.DataFrame({"a": [1]}), metadata)
    real_replace, calls = os.replace, []

    def interrupted(src, dst):
        calls.append(dst)
        if len(calls) == crash_at:
            raise OSError("power cut")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError):
        adapter.export("backlog", pd.DataFrame({"a": [2]}), metadata)
    monkeypatch.setattr(os, "replace", real_replace)
    pair = adapter.latest_valid("backlog")
    assert pair is not None
    assert pd.read_csv(pair[0])["a"].tolist() == [1]      # the last complete export is still readable
    assert not list(tmp_path.glob("*.tmp"))


def test_a_completed_export_keeps_the_previous_one_as_fallback(tmp_path):
    adapter, metadata = _adapter(tmp_path)
    assert adapter.latest_valid("backlog") is None        # nothing exported yet
    adapter.export("backlog", pd.DataFrame({"a": [1]}), metadata)
    adapter.export("backlog", pd.DataFrame({"a": [2]}), metadata)
    assert adapter.latest_valid("backlog") == (tmp_path / "backlog.csv", tmp_path / "backlog.meta.json")
    assert pd.read_csv(tmp_path / "backlog.previous.csv")["a"].tolist() == [1]
    (tmp_path / "backlog.meta.json").write_text("[]")     # a damaged live sidecar
    assert adapter.latest_valid("backlog")[0].name == "backlog.previous.csv"
