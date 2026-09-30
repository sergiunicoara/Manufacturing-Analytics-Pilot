"""Profile-dependent security controls.

demo    : local synthetic demo. No API keys; permissive CORS; an Anthropic key alone enables
          explanations. Never use with client data.
secured : startup fails on default or missing credentials; every data route needs an API key
          (reader for reads, operator for mutations and the copilot); the database login must not be
          `sa`; CORS is limited to ALLOWED_ORIGINS; external LLM explanations need an explicit
          ALLOW_EXTERNAL_LLM=true, and the payload is minimised before it leaves the process.
Keys are compared in constant time and never logged or echoed.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from app.config import DEMO_DEFAULT_PASSWORD, settings

SECURED = "secured"
PROFILES = ("demo", SECURED)
# Keys an external model may receive in the secured profile: numbers, labels and method text needed to
# explain a result. Everything else (record ids, descriptions, detected values, free-text assumptions)
# stays in the process. Labels are business codes (item, work centre, scenario), never customer data.
LLM_ALLOWED_KEYS = frozenset({
    "tool", "result", "metrics", "rows", "series", "evidence", "inputs", "name", "label", "value", "case",
    "title", "unit", "units", "formula", "provenance", "time_scope", "coverage", "calculation_trace",
    "classification", "impact_scope", "origin", "rule_id", "findings", "unique_records", "records_affected_pct",
    "period_expense", "buffer_capital", "inventory_carrying_cost", "intervention_cost", "ending_backlog_hours",
    "average_inventory_capital", "policy", "confidence", "n_observations", "coverage_pct",
    "recorded_p50_hours", "recorded_p85_hours", "recorded_p95_hours", "modelled_standard_p50_hours",
    "data_origin", "valid_observations", "eligible_operations",
})
MAX_LIST_ITEMS = 20


def is_secured() -> bool:
    return settings.app_profile == SECURED


def validate_settings() -> list[str]:
    """Problems that must stop a secured start-up. Empty list = OK."""
    if settings.app_profile not in PROFILES:
        return [f"APP_PROFILE must be one of {PROFILES}"]
    if not is_secured():
        return []
    problems = []
    if not settings.mssql_password or settings.mssql_password == DEMO_DEFAULT_PASSWORD:
        problems.append("MSSQL_PASSWORD must be set explicitly (the demo default is not allowed)")
    if settings.mssql_user.lower() == "sa":
        problems.append("MSSQL_USER must be the least-privilege application login, not sa")
    if not settings.operator_api_keys:
        problems.append("PILOT_OPERATOR_API_KEYS must contain at least one key")
    if not settings.reader_api_keys:
        problems.append("PILOT_READER_API_KEYS must contain at least one key")
    if any(len(k) < 24 for k in settings.reader_api_keys + settings.operator_api_keys):
        problems.append("API keys must be at least 24 characters")
    if not settings.allowed_origins:
        problems.append("ALLOWED_ORIGINS must list the permitted browser origins")
    return problems


def _matches(candidate: str | None, keys: tuple[str, ...]) -> bool:
    return bool(candidate) and any(hmac.compare_digest(candidate.encode(), k.encode()) for k in keys)


def authorize(role: str, api_key: str | None) -> None:
    if not is_secured():
        return
    if role == "operator":
        if _matches(api_key, settings.operator_api_keys):
            return
        if _matches(api_key, settings.reader_api_keys):
            raise HTTPException(status_code=403, detail="This action needs an operator key")
    elif _matches(api_key, settings.reader_api_keys + settings.operator_api_keys):
        return
    raise HTTPException(status_code=401, detail="A valid X-API-Key header is required",
                        headers={"WWW-Authenticate": "ApiKey"})


def require_reader(x_api_key: str | None = Header(default=None)) -> None:
    authorize("reader", x_api_key)


def require_operator(x_api_key: str | None = Header(default=None)) -> None:
    authorize("operator", x_api_key)


def llm_allowed() -> bool:
    if not settings.anthropic_api_key:
        return False
    return settings.allow_external_llm if is_secured() else True


def minimise_for_llm(value):
    """In the secured profile, keep only allow-listed keys and cap lists before anything leaves the process."""
    if not is_secured():
        return value
    if isinstance(value, dict):
        return {k: minimise_for_llm(v) for k, v in value.items() if k in LLM_ALLOWED_KEYS}
    if isinstance(value, list):
        return [minimise_for_llm(v) for v in value[:MAX_LIST_ITEMS]]
    return value
