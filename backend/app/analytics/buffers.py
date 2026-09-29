"""Constraint-aware analytical buffer ranges, not inventory mandates."""
from __future__ import annotations

import math
import statistics

import pandas as pd

from app.analytics.evidence import evidence, source
from app.analytics.forecast import realized_wape_by_item
from app.analytics.period_engine import PeriodEngineOutput


def recommend_buffers(tables: dict[str, pd.DataFrame], result: PeriodEngineOutput) -> list[dict]:
    qualifying = {"CANDIDATE_CONSTRAINT", "PRIMARY_CONSTRAINT"}
    constrained = {}
    for row in result.work_centre_results:
        if row.constraint_classification in qualifying:
            constrained.setdefault(row.work_centre_id, []).append(row)
    if not constrained:
        return []
    ops = tables["routing_operations"]
    headers = tables["routing_headers"]
    items = tables["items"].set_index("item_id")
    forecast_wape = realized_wape_by_item(tables)
    recommendations = []
    for wc_id, periods in sorted(constrained.items()):
        utilization = max(float(r.utilization_pct) for r in periods if math.isfinite(float(r.utilization_pct)))
        routing_ids = ops.loc[ops["work_centre_id"] == wc_id, "routing_id"].unique()
        item_ids = headers.loc[headers["routing_id"].isin(routing_ids), "item_id"].unique()
        for item_id in item_ids:
            item_id = int(item_id)
            if item_id not in items.index:
                continue
            weekly = [float(r.gross_requirement) for r in result.material_series(item_id)]
            if not weekly or sum(weekly) <= 0:
                continue
            daily = sum(weekly) / (len(weekly) * 7.0)
            cv = statistics.pstdev(weekly) / statistics.mean(weekly) if statistics.mean(weekly) else 0.0
            wape = forecast_wape.get(item_id)
            # No item-level replenishment-time master exists. Use the observed
            # queue exposure at this centre plus one weekly planning cycle.
            queue_days = max(float(r.queue_time_days) for r in periods)
            replenishment_days = 7.0 + queue_days
            base = daily * replenishment_days
            # Three observed risk signals widen the uncertainty interval;
            # they do not alter the demand × replenishment base.
            utilization_risk = min(1.0, max(0.0, (utilization - 0.85) / 0.15))
            forecast_spread = 0.25 * min(wape, 1.0) if wape is not None else 0.0
            spread = min(0.75, 0.25 * min(cv, 1.0) + forecast_spread + 0.10 * utilization_risk)
            low = max(0.0, base * (1.0 - spread))
            high = base * (1.0 + spread)
            if not all(math.isfinite(v) for v in (low, high)):
                continue
            inputs = [source("weekly gross requirements", weekly, f"material:{item_id}"),
                      source("constraint classification", [r.constraint_classification for r in periods], f"work_centre:{wc_id}", "DERIVED"),
                      source("maximum queue exposure days", queue_days, f"work_centre:{wc_id}", "DERIVED"),
                      source("demand coefficient of variation", cv, f"material:{item_id}", "DERIVED"),
                      source("realized forecast WAPE", wape, f"forecast:{item_id}", "DERIVED"),
                      source("maximum constrained utilization ratio", utilization, f"work_centre:{wc_id}", "DERIVED")]
            reason = (f"{items.loc[item_id, 'item_code']} feeds work centre {wc_id}, classified as a candidate or primary constraint. "
                      "The range covers one planning week plus observed queue exposure.")
            assumptions = ["One weekly replenishment cycle is assumed; no item-specific supplier lead time is available.",
                           "Gross requirements are a demand proxy; operational placement and feasibility need validation."]
            if wape is None:
                assumptions.append("No realized item-level forecast history was available; forecast error is omitted from the range.")
            recommendations.append({"item_id": item_id, "item_code": str(items.loc[item_id, "item_code"]),
                                    "location_work_centre_id": wc_id, "recommended_min": round(low, 2),
                                    "recommended_max": round(high, 2), "reason": reason,
                                    "confidence": "LOW" if cv > 0.75 or wape is None else "MEDIUM",
                                    "assumptions": assumptions, "inputs": inputs,
                                    "evidence": evidence({"min": round(low, 2), "max": round(high, 2)},
                                        "ANALYTICS_METHODS.md#buffer-recommendations", inputs,
                                        [f"Daily usage = {sum(weekly):.2f} / {len(weekly) * 7}",
                                         f"Base = {daily:.3f} × {replenishment_days:.3f} days",
                                         f"Range = base × (1 ± {spread:.3f})"], assumptions)})
    return recommendations
