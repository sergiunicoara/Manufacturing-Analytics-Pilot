"""Transactional persistence of period-engine scenarios and recommendations."""
from __future__ import annotations

import json
import math

import sqlalchemy as sa

from app.analytics.evidence import json_value
from app.config import settings


def _sql_number(value):
    """SQL NULL carries an unreliable KPI; zero would assert false certainty."""
    return None if value is None or not math.isfinite(float(value)) else float(value)


def save_run(engine: sa.Engine, name: str, parameters: dict, output,
             intervention_type: str = "NONE", recommendations: list[dict] | None = None) -> int:
    """Append an auditable run. A failed insert rolls back all result tables."""
    allowed = {"NONE", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"}
    if intervention_type not in allowed:
        raise ValueError(f"Unknown intervention type: {intervention_type}")
    parameters_json = json.dumps(json_value(parameters), sort_keys=True)
    metadata = sa.MetaData()
    metadata.reflect(engine, only=["scenario_runs", "material_requirements",
                                   "period_engine_results", "buffer_recommendations"])
    runs = metadata.tables["scenario_runs"]
    materials = metadata.tables["material_requirements"]
    periods = metadata.tables["period_engine_results"]
    buffers = metadata.tables["buffer_recommendations"]
    with engine.begin() as conn:
        run_id = conn.execute(runs.insert().values(
            name=name, parameters_json=parameters_json, is_baseline=(name == "BASELINE"),
            intervention_type=intervention_type, seed=settings.synthetic_seed)).inserted_primary_key[0]
        if output.material_results:
            conn.execute(materials.insert(), [{"run_id": run_id, "item_id": r.item_id,
                "period_start_date": r.period_start_date, "gross_requirement": r.gross_requirement,
                "usable_inventory": r.usable_inventory, "scheduled_receipts": r.scheduled_receipts,
                "net_requirement": r.net_requirement, "shortage_flag": r.shortage_flag,
                "provenance": r.provenance} for r in output.material_results])
        if output.work_centre_results:
            conn.execute(periods.insert(), [{"run_id": run_id, "work_centre_id": r.work_centre_id,
                "period_start_date": r.period_start_date, "calendar_hours": _sql_number(r.calendar_hours),
                "available_hours": _sql_number(r.available_hours), "effective_hours": _sql_number(r.effective_hours),
                "required_hours": _sql_number(r.required_hours), "utilization_pct": _sql_number(r.utilization_pct),
                "backlog_hours_start": r.backlog_hours_start, "backlog_hours_end": r.backlog_hours_end,
                "wip_qty": r.wip_qty, "queue_time_days": _sql_number(r.queue_time_days),
                "constraint_classification": r.constraint_classification} for r in output.work_centre_results])
        if recommendations:
            conn.execute(buffers.insert(), [{"run_id": run_id, "item_id": r["item_id"],
                "location_work_centre_id": r["location_work_centre_id"],
                "recommended_min": r["recommended_min"], "recommended_max": r["recommended_max"],
                "reason": r["reason"], "inputs_json": json.dumps(r["inputs"]),
                "confidence": r["confidence"], "assumptions": " ".join(r["assumptions"])}
                for r in recommendations])
    return int(run_id)


def ensure_run(engine: sa.Engine, name: str, parameters: dict, output,
               intervention_type: str = "NONE", recommendations: list[dict] | None = None) -> int:
    """Reuse a matching seeded run so API restarts do not duplicate golden cases."""
    parameters_json = json.dumps(json_value(parameters), sort_keys=True)
    with engine.connect() as conn:
        existing = conn.execute(sa.text("SELECT TOP 1 run_id FROM scenario_runs WHERE name = :name "
                                        "AND parameters_json = :parameters AND seed = :seed ORDER BY run_id DESC"),
                                {"name": name, "parameters": parameters_json,
                                 "seed": settings.synthetic_seed}).scalar()
    return int(existing) if existing is not None else save_run(engine, name, parameters, output,
                                                                intervention_type, recommendations)
