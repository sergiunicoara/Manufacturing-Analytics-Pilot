"""Decision economics over the period-engine outputs. Pure functions, no I/O.

Conventions (also in ANALYTICS_METHODS.md#cost):
- Currency: CostAssumptions.currency (synthetic data — no real prices).
- Valuation: the standard_costs record effective on the valuation date. The
  stored record is the unit cost; it is NOT re-summed over the BOM, because
  the record's material_cost already rolls up direct components and its
  overhead_cost is already money (18 % of material + labour in the
  generator). Re-summing would count component material twice.
- A record whose BOM has manufactured (non-RAW/PACKAGING) components carries
  approximated component material, so its valuation is ASSUMED, not MEASURED.
- Missing or overlapping effective cost records give UNKNOWN, never zero.
- Capital tied up (a balance at standard cost), period expense (carrying cost,
  added paid hours) and cash flow are reported separately. No ROI, profit or
  avoided-loss figure is produced: the inputs for those do not exist.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

import pandas as pd

from app.analytics.period_engine import PeriodEngineOutput

VALUED = "VALUED"
MISSING = "MISSING"
OVERLAPPING = "OVERLAPPING"
PURCHASED_TYPES = frozenset({"RAW", "PACKAGING"})


@dataclass(frozen=True)
class CostAssumptions:
    currency: str = "EUR"
    annual_carrying_rate: float = 0.20        # ASSUMED share of inventory value per year
    added_hour_rate_multiplier: float = 1.0   # ASSUMED premium on cost_per_hour for added scheduled hours
    weeks_per_year: int = 52

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class UnitCost:
    item_id: int
    status: str
    material: float | None = None
    labour: float | None = None
    overhead: float | None = None
    total: float | None = None
    provenance: str | None = None
    standard_cost_id: int | None = None
    reason: str = ""


def _as_ts(value) -> pd.Timestamp:
    return pd.Timestamp(value)


def unit_costs(standard_costs: pd.DataFrame, items: pd.DataFrame, bom_headers: pd.DataFrame,
               bom_components: pd.DataFrame, valuation_date: dt.date) -> dict[int, UnitCost]:
    """Effective-dated unit cost per item, with status and provenance."""
    when = _as_ts(valuation_date)
    costs = standard_costs.copy()
    costs["effective_from"] = pd.to_datetime(costs["effective_from"])
    costs["effective_to"] = pd.to_datetime(costs["effective_to"])
    effective = costs.loc[(costs["effective_from"] <= when)
                          & (costs["effective_to"].isna() | (costs["effective_to"] >= when))]
    by_item = {int(k): g for k, g in effective.groupby("item_id")}

    item_type = items.set_index("item_id")["item_type"].to_dict()
    parents = bom_headers.set_index("bom_id")["parent_item_id"]
    comps = bom_components.assign(parent_item_id=bom_components["bom_id"].map(parents))
    approximated_parents = {int(p) for p, g in comps.groupby("parent_item_id")
                            if any(item_type.get(int(c)) not in PURCHASED_TYPES for c in g["component_item_id"])}

    result = {}
    for item_id in items["item_id"].astype(int):
        records = by_item.get(item_id)
        if records is None or records.empty:
            result[item_id] = UnitCost(item_id, MISSING, reason=f"No standard cost effective on {valuation_date}.")
            continue
        if len(records) > 1:
            result[item_id] = UnitCost(item_id, OVERLAPPING,
                                       reason=f"{len(records)} standard cost records overlap {valuation_date}.")
            continue
        r = records.iloc[0]
        material, labour, overhead = (float(r["material_cost"]), float(r["labour_cost"]), float(r["overhead_cost"]))
        assumed = r["cost_source"] == "ASSUMED" or item_id in approximated_parents
        reason = ("Material includes approximated manufactured-component values." if item_id in approximated_parents
                  else "Record marked ASSUMED at source." if r["cost_source"] == "ASSUMED" else "")
        result[item_id] = UnitCost(item_id, VALUED, material, labour, overhead, material + labour + overhead,
                                   "ASSUMED" if assumed else "MEASURED", int(r["standard_cost_id"]), reason)
    return result


def inventory_value_series(output: PeriodEngineOutput, costs: dict[int, UnitCost]) -> pd.DataFrame:
    """Per period: value of the engine's end-of-period inventory state, plus unvalued quantity.
    Exposure convention: the end-of-week balance is held for one week."""
    rows = []
    for period, state in sorted(output.inventory_end_by_period.items()):
        value = valued_qty = unvalued_qty = 0.0
        for item_id, qty in state.items():
            unit = costs.get(item_id)
            if unit is not None and unit.status == VALUED:
                value += qty * unit.total
                valued_qty += qty
            else:
                unvalued_qty += qty
        rows.append({"period_start_date": period, "inventory_value": value, "valued_qty": valued_qty,
                     "unvalued_qty": unvalued_qty})
    return pd.DataFrame(rows, columns=["period_start_date", "inventory_value", "valued_qty", "unvalued_qty"])


def buffer_capital(buffer_boost_by_item: dict[int, float], costs: dict[int, UnitCost]) -> dict:
    """Capital tied up by the one-time buffer boost, at standard cost."""
    valued = {i: q for i, q in buffer_boost_by_item.items() if costs.get(i) and costs[i].status == VALUED}
    unvalued = {i: q for i, q in buffer_boost_by_item.items() if i not in valued}
    return {"capital": sum(q * costs[i].total for i, q in valued.items()),
            "valued_items": len(valued), "unvalued_items": len(unvalued),
            "unvalued_qty": sum(unvalued.values()),
            "assumed_items": sum(costs[i].provenance == "ASSUMED" for i in valued)}


def added_scheduled_hours_per_week(work_centre: pd.Series | dict, multiplier: float) -> float:
    """Calendar arithmetic: scheduled hours scale with the effective multiplier at
    unchanged availability and efficiency. Effective hours are not paid hours."""
    scheduled = float(work_centre["shifts_per_day"]) * float(work_centre["hours_per_shift"]) * float(work_centre["days_per_week"])
    return scheduled * max(0.0, multiplier - 1.0)


def intervention_cost(capacity_lever: dict[int, tuple[float, dt.date]], work_centres: pd.DataFrame,
                      horizon: list[dt.date], assumptions: CostAssumptions) -> dict:
    """Added paid hours × cost_per_hour × premium, for each week the lever is active."""
    by_id = work_centres.set_index("work_centre_id")
    lines, total = [], 0.0
    for wc_id, (multiplier, start) in capacity_lever.items():
        wc = by_id.loc[wc_id]
        weeks = sum(1 for period in horizon if period >= start)
        hours_per_week = added_scheduled_hours_per_week(wc, multiplier)
        rate = float(wc["cost_per_hour"]) * assumptions.added_hour_rate_multiplier
        cost = hours_per_week * weeks * rate
        total += cost
        lines.append({"work_centre_id": int(wc_id), "multiplier": multiplier, "active_weeks": weeks,
                      "added_scheduled_hours_per_week": round(hours_per_week, 2),
                      "hourly_rate": rate, "cost": round(cost, 2)})
    return {"cost": total, "lines": lines}


def case_economics(output: PeriodEngineOutput, costs: dict[int, UnitCost], horizon: list[dt.date],
                   assumptions: CostAssumptions, buffer_boost_by_item: dict[int, float] | None = None,
                   capacity_lever: dict | None = None, work_centres: pd.DataFrame | None = None) -> dict:
    series = inventory_value_series(output, costs)
    weekly_rate = assumptions.annual_carrying_rate / assumptions.weeks_per_year
    final = [r for r in output.work_centre_results if r.period_start_date == max(horizon)]
    buffer = buffer_capital(buffer_boost_by_item or {}, costs)
    intervention = (intervention_cost(capacity_lever, work_centres, horizon, assumptions)
                    if capacity_lever else {"cost": 0.0, "lines": []})
    return {
        "average_inventory_capital": float(series["inventory_value"].mean()) if len(series) else 0.0,
        "ending_inventory_capital": float(series["inventory_value"].iloc[-1]) if len(series) else 0.0,
        "inventory_carrying_cost": float(series["inventory_value"].sum() * weekly_rate),
        "unvalued_inventory_qty_avg": float(series["unvalued_qty"].mean()) if len(series) else 0.0,
        "buffer_capital": buffer["capital"], "buffer_detail": buffer,
        "intervention_cost": intervention["cost"], "intervention_lines": intervention["lines"],
        "wip_carrying_cost": None,
        "wip_carrying_cost_reason": ("Unavailable: engine WIP is aggregated per work centre across mixed items; "
                                     "valuing it would need an unsupported allocation."),
        "ending_backlog_hours": sum(r.backlog_hours_end for r in final),
        "inventory_series": series,
    }


def five_case_summary(economics: dict[str, dict], comparison_case: str = "DEMAND_SHOCK_ONLY") -> list[dict]:
    base = economics[comparison_case]
    keys = ("average_inventory_capital", "inventory_carrying_cost", "buffer_capital", "intervention_cost",
            "ending_backlog_hours")
    rows = []
    for case, e in economics.items():
        rows.append({"case": case, **{k: round(e[k], 2) for k in keys},
                     **{f"delta_{k}": round(e[k] - base[k], 2) for k in keys},
                     "wip_carrying_cost": None})
    return rows
