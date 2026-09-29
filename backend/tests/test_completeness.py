import pandas as pd
import pytest

from app.integration import completeness as cc


@pytest.fixture(scope="module")
def contract():
    return cc.load_contract()


def _by(report, check, dataset=None):
    return [r for r in report["results"] if r["check"] == check and (dataset is None or r["dataset"] == dataset)]


def test_generated_extract_is_checked_beyond_row_counts(tables, contract):
    report = cc.run_checks(tables, contract)
    checks = {r["check"].split(":")[0] for r in report["results"]}
    assert {"dataset_present", "required_columns", "key_unique", "relationship", "history_length",
            "forecast_revision_depth", "bom_depth", "date_order"} <= checks
    assert _by(report, "forecast_revision_depth")[0]["status"] == cc.PASS
    # the generator plants orphan BOM components; the checker must see them
    orphan = _by(report, "relationship:bom_components.component_item_id -> items.item_id")[0]
    assert orphan["status"] == cc.WARN and orphan["observed"] > 0


def test_missing_required_table_and_column_block(tables, contract):
    reduced = {k: v for k, v in tables.items() if k != "forecast_versions"}
    reduced["items"] = tables["items"].drop(columns=["item_type"])
    report = cc.run_checks(reduced, contract)
    assert report["overall"] == cc.BLOCK
    assert _by(report, "dataset_present", "forecast_versions")[0]["status"] == cc.BLOCK
    assert _by(report, "required_columns", "item_master")[0]["status"] == cc.BLOCK


def test_short_history_blocks_and_optional_table_only_warns(tables, contract):
    short = dict(tables)
    versions = tables["forecast_versions"].sort_values("snapshot_date")
    short["forecast_versions"] = versions.tail(4)
    short.pop("wip")
    report = cc.run_checks(short, contract)
    assert _by(report, "history_length", "forecast_versions")[0]["status"] == cc.BLOCK
    assert _by(report, "dataset_present", "wip")[0]["status"] == cc.WARN


def test_duplicate_keys_and_reversed_dates_are_reported(contract):
    spec = next(s for s in contract["datasets"] if s["name"] == "production_orders")
    frame = pd.DataFrame({"production_order_id": [1, 1], "order_number": ["A", "B"], "item_id": [1, 1], "site_id": [1, 1],
                          "qty": [1, 1], "status": ["COMPLETED"] * 2, "planned_start": ["2026-01-02", "2026-01-01"],
                          "planned_finish": ["2026-01-01", "2026-01-03"]})
    results = {r.check: r for r in cc.check_dataset(spec, frame)}
    assert results["key_unique"].status == cc.BLOCK
    assert results["date_order:planned_start<=planned_finish"].observed == 1
    assert results["optional_columns"].status == cc.WARN
