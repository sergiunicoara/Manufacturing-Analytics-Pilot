"""Versioned planning-parameter package for planner review.

This is a review/export artefact, not an ERP import. There is no write-back.
No native Infor M3 field has been verified for any parameter, so every row is
`mapping_status = UNMAPPED` with `m3_field = None` until the client's M3
schema is reviewed (see docs/M3_MAPPING.md).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PACKAGE_SCHEMA_VERSION = "1.0.0"
CSV_COLUMNS = ("item_code", "site_code", "parameter", "proposed_value", "units", "effective_date", "provenance",
               "confidence", "blockers", "mapping_status", "m3_field", "rationale")


class ParameterRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_code: str = Field(min_length=1)
    site_code: str = Field(min_length=1)
    parameter: Literal["planning_policy", "analytical_buffer_min_qty", "analytical_buffer_max_qty",
                       "decoupling_candidate"]
    proposed_value: str
    units: str
    effective_date: dt.date
    provenance: Literal["MEASURED", "DERIVED", "ASSUMED"]
    confidence: Literal["HIGH", "MEDIUM", "LOW", "NONE"]
    blockers: list[str] = Field(default_factory=list)
    mapping_status: Literal["UNMAPPED", "PENDING_CLIENT_VERIFICATION", "VERIFIED"] = "UNMAPPED"
    m3_field: str | None = None
    rationale: str = ""


class ParameterPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = PACKAGE_SCHEMA_VERSION
    package_id: str
    generated_at: dt.datetime
    source_run_id: int | None
    source_scenario: str
    data_origin: Literal["SYNTHETIC", "CLIENT"]
    erp_write_back: Literal[False] = False
    notes: list[str]
    rows: list[ParameterRow]


def build_package(policies: list, buffers: list[dict], candidates: list[dict], site_code: str,
                  effective_date: dt.date, source_run_id: int | None, source_scenario: str,
                  generated_at: dt.datetime, data_origin: str = "SYNTHETIC") -> ParameterPackage:
    rows = []
    for p in policies:
        rows.append(ParameterRow(
            item_code=p.item_code, site_code=site_code, parameter="planning_policy", proposed_value=p.policy,
            units="policy", effective_date=effective_date,
            provenance="DERIVED",
            confidence=p.confidence, blockers=list(p.blockers), rationale=" ".join(p.reasons)))
    for b in buffers:
        low, high = b.get("recommended_min"), b.get("recommended_max")
        computed = low is not None and high is not None
        common = dict(item_code=b["item_code"], site_code=site_code, units="units", effective_date=effective_date,
                      provenance="ASSUMED", confidence=b["confidence"] if computed else "NONE",
                      blockers=[] if computed else ["No range computed."], rationale=b.get("reason", ""))
        rows.append(ParameterRow(parameter="analytical_buffer_min_qty",
                                 proposed_value=f"{low:.2f}" if computed else "", **common))
        rows.append(ParameterRow(parameter="analytical_buffer_max_qty",
                                 proposed_value=f"{high:.2f}" if computed else "", **common))
    for c in candidates:
        rows.append(ParameterRow(item_code=c["item_code"], site_code=site_code, parameter="decoupling_candidate",
                                 proposed_value="true", units="flag", effective_date=effective_date,
                                 provenance="DERIVED", confidence="MEDIUM", rationale=" ".join(c["reasons"])))
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    return ParameterPackage(
        package_id=f"planning-parameters-{source_scenario.lower()}-{stamp}", generated_at=generated_at,
        source_run_id=source_run_id, source_scenario=source_scenario, data_origin=data_origin,
        notes=["For planner review only; not an import-ready Infor M3 transaction format.",
               "No native M3 field is mapped: every row is UNMAPPED until verified against the client's M3 schema.",
               "Analytical buffer ranges are review ranges, not automatic inventory policy.",
               "Policy selection and buffer sizing are separate recommendations."],
        rows=rows)


def to_json(package: ParameterPackage) -> str:
    return package.model_dump_json(indent=2)


def from_json(text: str) -> ParameterPackage:
    return ParameterPackage.model_validate_json(text)


def to_csv(package: ParameterPackage) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in package.rows:
        data = row.model_dump(mode="json")
        data["blockers"] = " | ".join(data["blockers"])
        data["m3_field"] = data["m3_field"] or ""
        writer.writerow({k: data[k] for k in CSV_COLUMNS})
    return buffer.getvalue()


def rows_from_csv(text: str) -> list[ParameterRow]:
    rows = []
    for record in csv.DictReader(io.StringIO(text)):
        record["blockers"] = [b for b in record["blockers"].split(" | ") if b]
        record["m3_field"] = record["m3_field"] or None
        rows.append(ParameterRow.model_validate(record))
    return rows


def json_schema() -> str:
    return json.dumps(ParameterPackage.model_json_schema(), indent=2)
