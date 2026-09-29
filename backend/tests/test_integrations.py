import json

import pandas as pd
import pytest

from app.integrations.file_adapters import CsvERPAdapter, FileBIExportAdapter, JsonlMESAdapter
from app.integrations.protocols import AdapterError, BIExportAdapter, ERPAdapter, MESAdapter

META = {"source": "period_engine", "data_origin": "SYNTHETIC", "generated_at": "2026-09-29T00:00:00Z"}


def test_adapters_satisfy_the_protocols(tmp_path):
    assert isinstance(CsvERPAdapter(tmp_path), ERPAdapter)
    assert isinstance(FileBIExportAdapter(tmp_path), BIExportAdapter)
    assert isinstance(JsonlMESAdapter(tmp_path / "events.jsonl"), MESAdapter)


def test_erp_extract_reports_missing_files_then_reads_canonical_tables(tmp_path):
    with pytest.raises(AdapterError) as err:
        CsvERPAdapter(tmp_path).extract()
    assert err.value.problems == ["missing file items.csv", "missing file sales_orders.csv"]
    pd.DataFrame({"item_id": [1]}).to_csv(tmp_path / "items.csv", index=False)
    pd.DataFrame({"sales_order_id": [1]}).to_csv(tmp_path / "sales_orders.csv", index=False)
    assert set(CsvERPAdapter(tmp_path).extract()) == {"items", "sales_orders"}


def test_erp_extract_feeds_the_completeness_checker(tmp_path):
    from app.integration.completeness import load_contract, run_checks
    pd.DataFrame({"item_id": [1], "item_code": ["A"], "item_type": ["FG"], "uom": ["PC"]}).to_csv(
        tmp_path / "items.csv", index=False)
    pd.DataFrame({"sales_order_id": [1]}).to_csv(tmp_path / "sales_orders.csv", index=False)
    report = run_checks(CsvERPAdapter(tmp_path).extract(), load_contract())
    assert report["overall"] == "BLOCK"          # a two-table extract is far from the request


def test_bi_export_writes_provenance_sidecar_and_rejects_bare_or_empty(tmp_path):
    adapter = FileBIExportAdapter(tmp_path)
    path = adapter.export("backlog", pd.DataFrame({"week": ["2026-06-01"], "hours": [1.5]}), META)
    assert pd.read_csv(path)["hours"].iloc[0] == 1.5
    assert json.loads((tmp_path / "backlog.meta.json").read_text())["data_origin"] == "SYNTHETIC"
    with pytest.raises(AdapterError) as err:
        adapter.export("bad", pd.DataFrame(), {"source": "x"})
    assert len(err.value.problems) == 3


def test_mes_events_validate_every_line(tmp_path):
    good = tmp_path / "ok.jsonl"
    good.write_text('{"po_operation_id": 1, "event_type": "START", "event_time": "2026-05-04T08:00:00"}\n')
    assert len(JsonlMESAdapter(good).read_operation_events()) == 1
    bad = tmp_path / "bad.jsonl"
    bad.write_text('not json\n'
                   '{"po_operation_id": 1}\n'
                   '{"po_operation_id": 1, "event_type": "PAUSE", "event_time": "2026-05-04"}\n'
                   '{"po_operation_id": 1, "event_type": "START", "event_time": "nope"}\n')
    with pytest.raises(AdapterError) as err:
        JsonlMESAdapter(bad).read_operation_events()
    assert len(err.value.problems) == 4
