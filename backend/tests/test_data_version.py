import pandas as pd

from app.analytics import data_version as dv


def _items():
    return pd.DataFrame({"item_id": [1, 2], "item_code": ["A", "B"], "created_at": ["t1", "t2"]})


def test_fingerprint_ignores_row_order_and_load_timestamps():
    base = dv.table_fingerprint(_items())
    shuffled = _items().iloc[::-1].assign(created_at=["later", "later"])
    assert dv.table_fingerprint(shuffled) == base


def test_any_content_change_changes_the_version():
    base = dv.source_data_version({"items": _items()}, ["items"])
    changed = _items()
    changed.loc[1, "item_code"] = "B2"
    assert dv.source_data_version({"items": changed}, ["items"]) != base
    assert dv.source_data_version({}, ["items"]) != base


def test_version_combines_data_and_code():
    version = dv.data_version({"items": _items()}, ["items"])
    assert version.startswith("data:") and "|code:" in version and "|cfg:" in version
    assert dv.code_version() == dv.code_version()


def test_ensure_run_does_not_reuse_without_a_matching_version(monkeypatch):
    from app.analytics import scenario_store
    saved = []
    monkeypatch.setattr(scenario_store, "save_run", lambda *args: saved.append(args) or 99)
    assert scenario_store.ensure_run(None, "BASELINE", {}, None, data_version=None) == 99
    assert saved and saved[0][-1] is None


def test_changing_an_analytical_setting_changes_the_version():
    import dataclasses
    from app.config import settings
    changed = dataclasses.replace(settings, forecast_consumption_window_weeks=settings.forecast_consumption_window_weeks + 1)
    assert dv.settings_version(changed) != dv.settings_version(settings)
    infra = dataclasses.replace(settings, mssql_host="elsewhere")
    assert dv.settings_version(infra) == dv.settings_version(settings)
