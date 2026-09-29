"""Small, JSON-safe evidence envelopes shared by API and copilot."""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict, is_dataclass


def json_value(value):
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return json_value(value.item())
    return value


def evidence(value, formula: str, inputs: list[dict] | None = None,
             trace: list[str] | None = None, assumptions: list[str] | None = None,
             provenance: str = "DERIVED", *, units: str | None = None,
             time_scope: str | None = None, coverage: dict | None = None,
             exclusions: list[str] | None = None, data_origin: str | None = None) -> dict:
    """`data_origin` (e.g. SYNTHETIC) is independent of calculation provenance:
    a MEASURED value computed from generated timestamps is still synthetic."""
    result = {"value": json_value(value), "provenance": provenance,
              "formula": formula, "inputs": json_value(inputs or []),
              "calculation_trace": trace or [], "assumptions": assumptions or []}
    extras = {"units": units, "time_scope": time_scope, "coverage": json_value(coverage),
              "exclusions": exclusions, "data_origin": data_origin}
    result.update({key: value for key, value in extras.items() if value is not None})
    return result


def source(name: str, value, record_id=None, provenance: str = "MEASURED") -> dict:
    return {"name": name, "value": json_value(value), "provenance": provenance,
            "source_record_id": str(record_id) if record_id is not None else None}
