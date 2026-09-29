import pytest

from app.db import restore as r


@pytest.mark.parametrize("name", ["", "1db", "db-name", "db;DROP DATABASE x", "db]", "a" * 65, "db name"])
def test_invalid_database_names_are_refused(name):
    with pytest.raises(r.RestoreRefused):
        r.validate_name(name)


@pytest.mark.parametrize("path", ["relative.bak", "/var/opt/mssql/data/x.txt", "/tmp/x.bak",
                                  "/var/opt/mssql/data/../../etc/x.bak", "/var/opt/mssql/data/x'.bak",
                                  "/var/opt/mssql/data/a b.bak"])
def test_unsafe_backup_paths_are_refused(path):
    with pytest.raises(r.RestoreRefused):
        r.validate_backup_path(path)


def test_valid_inputs_pass():
    assert r.validate_name("pilot_restore_check_20260929") == "pilot_restore_check_20260929"
    assert r.validate_backup_path("/var/opt/mssql/data/pilot.bak")


def test_restore_refuses_an_existing_target_without_touching_it(monkeypatch):
    calls = []
    monkeypatch.setattr(r, "database_exists", lambda name: True)
    monkeypatch.setattr(r, "_exec", lambda sql, fetch=False: calls.append(sql))
    with pytest.raises(r.RestoreRefused, match="already exists"):
        r.restore("/var/opt/mssql/data/pilot.bak", "mfg_analytics_pilot")
    with pytest.raises(r.RestoreRefused, match="already exists"):
        r.roundtrip("mfg_analytics_pilot", "mfg_analytics_pilot")
    assert calls == []


def test_no_statement_uses_replace_or_drop():
    import re
    statements = re.findall(r'_exec\(f?"([^"]*)', open(r.__file__, encoding="utf-8").read())
    assert statements and not any("REPLACE" in s or "DROP" in s for s in statements)
