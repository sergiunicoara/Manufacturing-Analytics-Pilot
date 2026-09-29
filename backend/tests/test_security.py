import dataclasses
import sys
import types

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app import security
from app.config import DEMO_DEFAULT_PASSWORD

READER = "r" * 32
OPERATOR = "o" * 32


def _settings(**changes):
    return dataclasses.replace(security.settings, **changes)


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setattr(security, "settings", _settings(
        app_profile="secured", mssql_user="pilot_app", mssql_password="X" * 20,
        reader_api_keys=(READER,), operator_api_keys=(OPERATOR,), allowed_origins=("https://pilot.example",),
        anthropic_api_key=None, allow_external_llm=False))


def test_secured_start_up_rejects_defaults_and_missing_keys(monkeypatch):
    monkeypatch.setattr(security, "settings", _settings(app_profile="secured", mssql_user="sa",
                                                        mssql_password=DEMO_DEFAULT_PASSWORD, reader_api_keys=(),
                                                        operator_api_keys=("short",), allowed_origins=()))
    problems = " | ".join(security.validate_settings())
    for expected in ("MSSQL_PASSWORD", "not sa", "PILOT_READER_API_KEYS", "24 characters", "ALLOWED_ORIGINS"):
        assert expected in problems


def test_valid_secured_settings_pass(secured):
    assert security.validate_settings() == []


def test_demo_profile_needs_no_key(monkeypatch):
    monkeypatch.setattr(security, "settings", _settings(app_profile="demo"))
    security.authorize("operator", None)


@pytest.mark.parametrize("role,key,status", [
    ("reader", None, 401), ("reader", "wrong" * 8, 401), ("operator", None, 401),
    ("operator", READER, 403), ("operator", "wrong" * 8, 401)])
def test_secured_rejects_missing_wrong_or_insufficient_keys(secured, role, key, status):
    with pytest.raises(HTTPException) as err:
        security.authorize(role, key)
    assert err.value.status_code == status
    assert READER not in str(err.value.detail) and OPERATOR not in str(err.value.detail)


@pytest.mark.parametrize("role,key", [("reader", READER), ("reader", OPERATOR), ("operator", OPERATOR)])
def test_secured_accepts_the_right_keys(secured, role, key):
    security.authorize(role, key)


def test_every_data_route_requires_a_key_and_mutations_require_an_operator():
    from app.main import app
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in {"/health"}:
            continue
        calls = {d.call for d in route.dependant.dependencies}
        assert security.require_reader in calls or security.require_operator in calls, route.path
        if "POST" in route.methods:
            assert security.require_operator in calls, route.path


def test_external_llm_needs_explicit_opt_in_when_secured(secured, monkeypatch):
    assert security.llm_allowed() is False
    monkeypatch.setattr(security, "settings", _settings(**{**dataclasses.asdict(security.settings),
                                                           "anthropic_api_key": "k"}))
    assert security.llm_allowed() is False
    monkeypatch.setattr(security, "settings", _settings(**{**dataclasses.asdict(security.settings),
                                                           "allow_external_llm": True}))
    assert security.llm_allowed() is True


def test_payload_is_minimised_before_leaving_the_process(secured):
    bundle = [{"tool": "get_kpi", "result": {"rows": [{"customer_id": 7, "value": 1,
                                                        "evidence": {"inputs": [{"source_record_id": "item:73"}]}}] * 30}}]
    out = security.minimise_for_llm(bundle)
    row = out[0]["result"]["rows"][0]
    assert "customer_id" not in row and "source_record_id" not in row["evidence"]["inputs"][0]
    assert len(out[0]["result"]["rows"]) == security.MAX_LIST_ITEMS


def test_no_provider_call_without_permission_even_with_evidence(secured, monkeypatch):
    from app import api
    calls = []
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda **kw: calls.append(kw)
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setattr(security, "settings", _settings(**{**dataclasses.asdict(security.settings),
                                                           "anthropic_api_key": "k"}))
    monkeypatch.setattr(api, "_deterministic_tool",
                        lambda request: ("get_kpi", [{"value": 1, "evidence": {"formula": "f"}}]))
    answer = api.copilot_ask(api.CopilotRequest(question="what is the backlog?"))
    assert answer["llm_used"] is False and calls == []
