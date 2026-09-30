"""Regression tests for the 2026-09-30 code-review findings."""
import dataclasses
import datetime as dt

import pandas as pd
import pytest

from app import security
from app.db import restore as r


# ---- 1: backup never overwrites or appends to an existing file ---------------------------------------
def test_backup_refuses_an_existing_file_and_issues_no_backup(monkeypatch):
    calls = []

    def fake_exec(sql, fetch=False):
        calls.append(sql)
        if "dm_os_file_exists" in sql:
            return [{"file_exists": 1}]
        return [{"id": 5}] if "DB_ID" in sql else None

    monkeypatch.setattr(r, "_exec", fake_exec)
    with pytest.raises(r.RestoreRefused, match="already exists; refusing to overwrite"):
        r.backup("mfg_analytics_pilot", "/var/opt/mssql/backup/client_extract.bak")
    assert not any(sql.startswith("BACKUP") for sql in calls)


def test_backup_to_a_new_file_uses_noinit(monkeypatch):
    calls = []

    def fake_exec(sql, fetch=False):
        calls.append(sql)
        if "dm_os_file_exists" in sql:
            return [{"file_exists": 0}]
        return [{"id": 5}] if "DB_ID" in sql else None

    monkeypatch.setattr(r, "_exec", fake_exec)
    r.backup("mfg_analytics_pilot", "/var/opt/mssql/data/new_copy.bak")
    backup = next(sql for sql in calls if sql.startswith("BACKUP"))
    assert "NOINIT" in backup and ", INIT," not in backup and "FORMAT" not in backup


# ---- 6: one distinct physical file per backup file ------------------------------------------------
def test_move_clauses_are_unique_for_multiple_logs_and_log_first_order():
    files = [{"logical_name": "db_log", "type": "L", "file_id": 2},
             {"logical_name": "db", "type": "D", "file_id": 1},
             {"logical_name": "db_log2", "type": "L", "file_id": 3},
             {"logical_name": "db_data2", "type": "D", "file_id": 4}]
    moves = r.move_clauses(files, "restore_x")
    targets = [m.split(" TO ")[1] for m in moves]
    assert len(set(targets)) == 4
    assert "N'/var/opt/mssql/data/restore_x.mdf'" in targets
    assert "N'/var/opt/mssql/data/restore_x_log2.ldf'" in targets and "N'/var/opt/mssql/data/restore_x_log3.ldf'" in targets
    with pytest.raises(r.RestoreRefused, match="FILESTREAM"):
        r.move_clauses([{"logical_name": "fs", "type": "S", "file_id": 5}], "restore_x")


# ---- 2: no unauthenticated docs in the secured profile ---------------------------------------------
def test_docs_and_schema_are_disabled_in_the_secured_profile(monkeypatch):
    from fastapi import FastAPI
    from app.main import docs_urls
    monkeypatch.setattr(security, "settings", dataclasses.replace(security.settings, app_profile="secured"))
    assert docs_urls() == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    secured_paths = {getattr(route, "path", None) for route in FastAPI(**docs_urls()).routes}
    assert not secured_paths & {"/docs", "/redoc", "/openapi.json"}
    monkeypatch.setattr(security, "settings", dataclasses.replace(security.settings, app_profile="demo"))
    demo_paths = {getattr(route, "path", None) for route in FastAPI(**docs_urls()).routes}
    assert {"/docs", "/openapi.json"} <= demo_paths


# ---- 3: copilot DQ lookup is scoped to the item ------------------------------------------------------
def test_finding_concerns_item_ignores_other_entities_with_the_same_number():
    from app.api import finding_concerns_item
    item = {"entity": "items", "record_id": "5", "affected_entity_type": None, "affected_entity_id": None}
    scoped = {"entity": "bom_components", "record_id": "88", "affected_entity_type": "item", "affected_entity_id": "5"}
    inventory = {"entity": "inventory", "record_id": "5", "affected_entity_type": "item", "affected_entity_id": "41"}
    forecast = {"entity": "customer_forecasts", "record_id": "5", "affected_entity_type": "forecast_record",
                "affected_entity_id": "5"}
    assert finding_concerns_item(item, "5") and finding_concerns_item(scoped, "5")
    assert not finding_concerns_item(inventory, "5") and not finding_concerns_item(forecast, "5")


# ---- 4: fingerprint independent of row order even with ties ------------------------------------------
def test_fingerprint_ignores_row_order_when_the_first_column_has_ties():
    from app.analytics.data_version import table_fingerprint
    frame = pd.DataFrame({"availability_pct": [0.9, 0.9, 0.9], "work_centre_id": [1, 2, 3], "calendar_hours": [40, 80, 120]})
    assert table_fingerprint(frame) == table_fingerprint(frame.iloc[[2, 0, 1]])
    changed = frame.assign(calendar_hours=[40, 80, 121])
    assert table_fingerprint(changed) != table_fingerprint(frame)


# ---- 5: passwords are validated, not truncated --------------------------------------------------------
def test_long_passwords_are_rejected_before_any_sql():
    from app.db.security_setup import MAX_PASSWORD_LENGTH, password_problems
    assert password_problems({"A": "x" * MAX_PASSWORD_LENGTH}) == []
    assert password_problems({"A": "x" * (MAX_PASSWORD_LENGTH + 1)}) == [f"A is longer than {MAX_PASSWORD_LENGTH} characters"]


# ---- 7: allow-list, not deny-list, for the external model ---------------------------------------------
def test_llm_payload_keeps_only_allow_listed_fields(monkeypatch):
    monkeypatch.setattr(security, "settings", dataclasses.replace(security.settings, app_profile="secured"))
    bundle = [{"tool": "get_dq_findings", "result": [{
        "rule_id": "negative_inventory", "classification": "BLOCKING", "description": "Customer C-0042 order ...",
        "detected_value": "C-0042", "recommended_action": "call the customer", "entity": "inventory",
        "evidence": {"value": "BLOCKING", "formula": "ANALYTICS_METHODS.md#data-quality",
                     "assumptions": ["Customer C-0042 ..."], "inputs": [{"name": "rule", "value": "x", "source_record_id": "7"}]}}]}]
    sent = security.minimise_for_llm(bundle)
    row = sent[0]["result"][0]
    assert set(row) == {"rule_id", "classification", "evidence"}
    assert "C-0042" not in str(sent)
    assert row["evidence"] == {"value": "BLOCKING", "formula": "ANALYTICS_METHODS.md#data-quality",
                               "inputs": [{"name": "rule", "value": "x"}]}


# ---- 9: DQ findings computed once per dataset ---------------------------------------------------------
def test_findings_are_computed_once_per_dataset(monkeypatch):
    from app.analytics import dashboard
    calls = []
    monkeypatch.setattr(dashboard, "run_all", lambda tables: calls.append(1) or ["f"])
    monkeypatch.setattr(dashboard, "_findings_cache", None)
    first, second = {"t": 1}, {"t": 2}
    assert dashboard.findings_for(first) == ["f"] and dashboard.findings_for(first) == ["f"]
    assert len(calls) == 1
    dashboard.findings_for(second)
    assert len(calls) == 2


# ---- 10: a buffer without a range is a blocked row, not a crash --------------------------------------
def test_buffer_without_a_range_becomes_a_blocked_row():
    from app.analytics.parameter_export import build_package
    package = build_package([], [{"item_code": "X", "recommended_min": None, "recommended_max": None,
                                  "confidence": "LOW", "reason": "no usage"}], [], "SITE", dt.date(2026, 6, 1),
                            1, "DEMAND_SHOCK_ONLY", dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc))
    assert [(row.parameter, row.proposed_value, row.confidence, row.blockers) for row in package.rows] == [
        ("analytical_buffer_min_qty", "", "NONE", ["No range computed."]),
        ("analytical_buffer_max_qty", "", "NONE", ["No range computed."])]
