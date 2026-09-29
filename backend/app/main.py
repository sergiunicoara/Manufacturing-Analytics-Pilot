"""FastAPI entrypoint for the Executive Story and health check."""
import datetime as dt
from functools import lru_cache

import pandas as pd
from fastapi import Depends, FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.analytics.data_access import load_all_tables
from app.analytics.period_engine import compute_lead_time_for_item
from app.analytics.scenario_demo import (
    cab100_item_ids, run_four_intervention_comparison,
)
from app.db.connection import get_engine
from app.synthetic.timeline import REFERENCE_DATE
from app import security
from app.api import router
from app.config import settings
from app.analytics.dashboard import context
from app.analytics.evidence import evidence, source

app = FastAPI(
    title="Manufacturing Analytics Pilot API",
    description="Synthetic-data manufacturing analytics pilot. All data is "
    "fictional; this is not connected to any real ERP/MES/BI system.",
)

_problems = security.validate_settings()
if _problems:
    raise RuntimeError("Refusing to start the secured profile: " + "; ".join(_problems))

# Demo profile: local synthetic data only, so open CORS avoids hardcoding the dev-server port.
# Secured profile: only the configured origins, and only the methods and header the API uses.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins) if security.is_secured() else ["*"],
    allow_methods=["GET", "POST"] if security.is_secured() else ["*"],
    allow_headers=["Content-Type", "X-API-Key"] if security.is_secured() else ["*"],
)
app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/executive-story", dependencies=[Depends(security.require_reader)])
@lru_cache(maxsize=1)
def executive_story() -> dict:
    """Calibrated 12-week CAB-100 story, with auditable weekly lead-time series."""
    try:
        tables, comparison = context()
        horizon = [REFERENCE_DATE + dt.timedelta(weeks=w) for w in range(12)]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Executive Story data unavailable: {exc}") from exc

    item_ids = cab100_item_ids(tables["items"])
    if not item_ids:
        raise HTTPException(status_code=422, detail="No CAB-100 finished goods are available")
    lead_item = item_ids[0]
    series = {}
    lead_time_kpis = {}
    plant_item_ids = tables["items"].loc[tables["items"]["item_type"] == "FG", "item_id"].astype(int).tolist()
    for case in ("BASELINE", "DEMAND_SHOCK_ONLY", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"):
        result = comparison[case]
        by_period = {(r.work_centre_id, r.period_start_date): r for r in result.work_centre_results}
        points = []
        cab_total = cab_weight = cab_demand = 0.0
        for period in horizon:
            weighted = {key: 0.0 for key in ("processing_days", "queue_days", "transfer_days", "total_days")}
            total_weight = 0.0
            period_demand = 0.0
            for item_id in item_ids:
                lt = compute_lead_time_for_item(item_id, period, tables["routing_headers"],
                    tables["routing_operations"], by_period, tables["items"], tables["bom_headers"], tables["bom_components"])
                rows = result.material_series(item_id)
                weight = next((r.gross_requirement for r in rows if r.period_start_date == period), 0.0)
                cab_demand += max(weight, 0.0)
                period_demand += max(weight, 0.0)
                if lt is None or weight <= 0:
                    continue
                total_weight += weight
                for key in weighted:
                    weighted[key] += getattr(lt, key) * weight
            values = {key: round(value / total_weight, 3) if total_weight else None for key, value in weighted.items()}
            points.append({"week": period.isoformat(), **values,
                           "demand_coverage_pct": round(total_weight / period_demand * 100, 1) if period_demand else None,
                           "evidence": evidence(values["total_days"], "ANALYTICS_METHODS.md#lead-time",
                               [source("processing days", values["processing_days"], f"{case}:{period}", "DERIVED"),
                                source("queue days", values["queue_days"], f"{case}:{period}", "DERIVED"),
                                source("transfer days", values["transfer_days"], f"{case}:{period}", "DERIVED"),
                                source("demand weight", total_weight, f"{case}:{period}", "DERIVED")],
                               ["Follow each CAB-100 BOM critical path using period-entry backlog and effective capacity.",
                                "Weight item lead times by gross requirement in this week.",
                                "Total = processing + queue + transfer."],
                               ["Weekly rough-cut state, not an order-level completion promise."])})
            cab_total += weighted["total_days"]
            cab_weight += total_weight
        series[case] = points
        plant_total = plant_weight = plant_demand = 0.0
        for item_id in plant_item_ids:
            for period in horizon:
                lt = compute_lead_time_for_item(item_id, period, tables["routing_headers"],
                    tables["routing_operations"], by_period, tables["items"], tables["bom_headers"], tables["bom_components"])
                rows = result.material_series(item_id)
                weight = next((r.gross_requirement for r in rows if r.period_start_date == period), 0.0)
                plant_demand += max(weight, 0.0)
                if lt is not None and weight > 0:
                    plant_total += lt.total_days * weight
                    plant_weight += weight
        lead_time_kpis[case] = {"cab100_demand_weighted_days": round(cab_total / cab_weight, 2) if cab_weight else None,
                                "cab100_demand_coverage_pct": round(cab_weight / cab_demand * 100, 1) if cab_demand else None,
                                "plant_demand_weighted_days": round(plant_total / plant_weight, 2) if plant_weight else None,
                                "plant_demand_coverage_pct": round(plant_weight / plant_demand * 100, 1) if plant_demand else None}

    welds = tables["work_centres"].loc[tables["work_centres"]["process_type"] == "WELDING"]
    capacity = []
    multipliers = comparison["per_work_centre_multipliers"]
    for _, wc in welds.iterrows():
        wc_id = int(wc["work_centre_id"])
        mult = float(multipliers.get(wc_id) or 1.0)
        baseline_week = tables["capacity_calendar"].loc[
            (tables["capacity_calendar"]["work_centre_id"] == wc_id)
            & (pd.to_datetime(tables["capacity_calendar"]["week_start_date"]).dt.date == horizon[0])
        ]
        effective = float(baseline_week.iloc[0]["effective_hours"]) if not baseline_week.empty else None
        added_calendar_hours = float(wc["shifts_per_day"] * wc["hours_per_shift"] * wc["days_per_week"] * (mult - 1))
        extra_shift_hours_per_day = added_calendar_hours / max(int(wc["days_per_week"]), 1)
        full_extra_shifts, partial_shift_hours = divmod(extra_shift_hours_per_day, float(wc["hours_per_shift"]))
        if mult <= 1:
            arrangement = "No added shift hours"
        elif full_extra_shifts >= 1:
            arrangement = (f"Add {int(full_extra_shifts)} full {float(wc['hours_per_shift']):g}h shift/day"
                           + (f" plus {partial_shift_hours:g}h/day" if partial_shift_hours > 0.01 else ""))
        else:
            arrangement = f"Add {partial_shift_hours:g}h/day of scheduled availability"
        capacity.append({"work_centre_id": wc_id, "work_centre": wc["name"],
                         "shifts_per_day": int(wc["shifts_per_day"]), "hours_per_shift": float(wc["hours_per_shift"]),
                         "days_per_week": int(wc["days_per_week"]),
                         "scheduled_hours_per_week": float(wc["shifts_per_day"] * wc["hours_per_shift"] * wc["days_per_week"]),
                         "effective_hours_per_week_current": effective,
                         "effective_hours_per_week_target": round(effective * mult, 1) if effective is not None else None,
                         "target_multiplier": mult,
                         "additional_effective_hours_per_week": round(effective * (mult - 1), 1) if effective is not None else None,
                         "additional_scheduled_hours_per_week": round(added_calendar_hours, 1),
                         "calendar_arrangement": arrangement})
    return {"horizon_weeks": 12, "horizon_rationale": "The 12-week story includes the shock and intervention response. The corrected BOM-path calculation reveals baseline deterioration inside this window, so it is visible in the chart and should not be described as a stable control. The prior 16-week calibration showed baseline lead time rising from 5.7 to 13.0 days over weeks 13–16; the period engine still supports 26 weeks.",
            "demand_shock_pct": 40, "intervention_start_week": comparison["intervention_start_week"].isoformat(),
            "lead_time_item_id": lead_item, "lead_time_item_scope": "CAB-100 finished goods, demand-weighted across each BOM critical path", "lead_time_series": series,
            "lead_time_kpis": lead_time_kpis,
            "capacity_intervention": capacity,
            "evidence": {"source": "period-stepped scenario engine", "queue_method": "entry backlog hours / effective hours per operating workday", "cases": list(series)}}
