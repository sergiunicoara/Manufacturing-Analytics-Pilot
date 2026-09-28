"""Builds period-engine inputs from a full generated/loaded dataset and runs
the named CP3 golden scenarios (CAB-100 +40% demand, welding +30% capacity
intervention). Shared by the pytest golden-scenario tests (in-memory tables)
and the CP3 report demo script (DB-loaded tables) so there is exactly one
implementation of "how to wire the period engine up against a real dataset."
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.analytics.blocking import BlockingIndex, build_blocking_index
from app.analytics.forecast import consume_forecast, derive_planning_demand, to_top_level_demand
from app.analytics.period_engine import run_period_engine
from app.dq.engine import findings_to_dataframe, run_all


def build_engine_inputs(tables: dict[str, pd.DataFrame], horizon: list[dt.date]) -> dict:
    items = tables["items"]

    findings = run_all(tables)
    blocking_index = build_blocking_index(findings_to_dataframe(findings))

    latest_inventory = tables["inventory"].sort_values("snapshot_date").groupby(
        ["item_id", "warehouse_id"], as_index=False
    ).last()
    inv_by_item = latest_inventory.groupby("item_id")["on_hand_qty"].sum()
    initial_inventory = {}
    for item_id, qty in inv_by_item.items():
        if blocking_index.is_entity_blocked("item", item_id):
            continue
        initial_inventory[int(item_id)] = max(0.0, float(qty))

    consumption = consume_forecast(
        tables["customer_forecasts"], tables["forecast_versions"],
        tables["sales_order_lines"], tables["sales_orders"],
    )
    planning_demand = derive_planning_demand(consumption, tables["sales_order_lines"], tables["sales_orders"])
    fg_item_ids = set(items.loc[items["item_type"] == "FG", "item_id"])
    planning_demand = planning_demand.loc[planning_demand["item_id"].isin(fg_item_ids)]
    horizon_dates = set(horizon)
    planning_demand = planning_demand.loc[
        planning_demand["delivery_period_start"].apply(lambda d: d.date() if hasattr(d, "date") else d).isin(horizon_dates)
    ]
    top_level_demand = to_top_level_demand(planning_demand)

    return dict(
        horizon=horizon,
        top_level_demand=top_level_demand,
        items_df=items,
        bom_headers_df=tables["bom_headers"],
        bom_components_df=tables["bom_components"],
        routing_headers_df=tables["routing_headers"],
        routing_operations_df=tables["routing_operations"],
        work_centres_df=tables["work_centres"],
        capacity_calendar_df=tables["capacity_calendar"],
        purchase_order_lines_df=tables["purchase_order_lines"],
        initial_inventory=initial_inventory,
        blocking_index=blocking_index,
    )


def cab100_item_ids(items_df: pd.DataFrame) -> list[int]:
    return items_df.loc[
        (items_df["item_type"] == "FG") & (items_df["product_family"] == "CAB-100"), "item_id"
    ].astype(int).tolist()


def welding_work_centre_ids(work_centres_df: pd.DataFrame) -> list[int]:
    return work_centres_df.loc[work_centres_df["process_type"] == "WELDING", "work_centre_id"].astype(int).tolist()


def run_golden_scenarios(tables: dict[str, pd.DataFrame], horizon: list[dt.date]) -> dict:
    base_inputs = build_engine_inputs(tables, horizon)
    cab100_ids = cab100_item_ids(base_inputs["items_df"])
    weld_ids = welding_work_centre_ids(base_inputs["work_centres_df"])

    baseline = run_period_engine(**base_inputs)

    demand_plus_40 = {item_id: 1.4 for item_id in cab100_ids}
    scenario_inputs = dict(base_inputs)
    scenario_inputs["demand_multiplier_by_item"] = demand_plus_40
    scenario = run_period_engine(**scenario_inputs)

    capacity_plus_30 = {wc_id: 1.3 for wc_id in weld_ids}
    intervention_inputs = dict(scenario_inputs)
    intervention_inputs["capacity_multiplier_by_work_centre"] = capacity_plus_30
    intervention = run_period_engine(**intervention_inputs)

    return {
        "baseline": baseline, "scenario_demand_plus_40": scenario, "scenario_plus_capacity_plus_30": intervention,
        "cab100_item_ids": cab100_ids, "welding_work_centre_ids": weld_ids,
    }
