"""Produces every CP3.1 acceptance-criteria artifact against the live
SQL-Server-loaded dataset.

Usage: python -m app.analytics.run_cp3_1_report
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.analytics.data_access import load_all_tables
from app.analytics.forecast import find_best_historical_example, reconstruct_forecast_history
from app.analytics.mrp import compute_low_level_codes
from app.analytics.scenario_demo import (
    build_engine_inputs,
    cab100_item_ids,
    run_cab100_demand_shock,
    run_four_intervention_comparison,
    welding_work_centre_ids,
)
from app.analytics.period_engine import run_period_engine
from app.db.connection import get_engine
from app.synthetic.timeline import REFERENCE_DATE

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> None:
    engine = get_engine()
    tables = load_all_tables(engine)
    items = tables["items"]
    wc_df = tables["work_centres"].set_index("work_centre_id")

    horizon = [REFERENCE_DATE + dt.timedelta(weeks=w) for w in range(16)]
    inputs = build_engine_inputs(tables, horizon)
    baseline = run_period_engine(**inputs)

    # --- 1. Baseline utilization distribution ---
    section("1. Baseline utilization distribution across all work centres (12-week avg)")
    rows = []
    for wc_id in sorted(set(r.work_centre_id for r in baseline.work_centre_results)):
        series = [r for r in baseline.wc_series(wc_id) if r.period_start_date <= horizon[11]]
        utils = [r.utilization_pct for r in series if r.utilization_pct == r.utilization_pct]
        avg_u = sum(utils) / len(utils) if utils else float("nan")
        rows.append({"work_centre_id": wc_id, "process_type": wc_df.loc[wc_id, "process_type"], "avg_utilization": round(avg_u, 2)})
    dist = pd.DataFrame(rows).sort_values("avg_utilization", ascending=False)
    print(dist.to_string(index=False))
    band_55_85 = ((dist["avg_utilization"] >= 0.55) & (dist["avg_utilization"] <= 0.85)).sum()
    band_85_95 = ((dist["avg_utilization"] > 0.85) & (dist["avg_utilization"] <= 0.95)).sum()
    print(f"\n{band_55_85}/{len(dist)} work centres in the 55-85% target band; {band_85_95} in the 85-95% band.")

    # --- 2. Persistent-constraint count ---
    section("2. Baseline persistent-constraint count (12 weeks)")
    persistent = 0
    for wc_id in sorted(set(r.work_centre_id for r in baseline.work_centre_results)):
        classes = [r.constraint_classification for r in baseline.wc_series(wc_id) if r.period_start_date <= horizon[11]]
        if "CANDIDATE_CONSTRAINT" in classes or "PRIMARY_CONSTRAINT" in classes:
            persistent += 1
            print(f"  work_centre_id={wc_id} ({wc_df.loc[wc_id, 'process_type']}): {classes}")
    print(f"\n{persistent} of {len(dist)} work centres ever reach CANDIDATE/PRIMARY_CONSTRAINT status in 12 weeks.")

    # --- 3. Baseline WIP/backlog trend ---
    section("3. Baseline WIP/backlog trend (plant-wide totals)")
    trend_rows = []
    for period in horizon[:12]:
        total_backlog = sum(r.backlog_hours_end for r in baseline.work_centre_results if r.period_start_date == period)
        total_wip = sum(r.wip_qty for r in baseline.work_centre_results if r.period_start_date == period)
        trend_rows.append({"week": period, "total_backlog_hours": round(total_backlog, 1), "total_wip_qty": round(total_wip, 1)})
    print(pd.DataFrame(trend_rows).to_string(index=False))

    # --- 4. Multi-level netting golden example ---
    section("4. Multi-level netting example (real data)")
    llc = compute_low_level_codes(items, tables["bom_headers"], tables["bom_components"])
    material_by_item: dict[int, list] = {}
    for r in baseline.material_results:
        material_by_item.setdefault(r.item_id, []).append(r)
    subassy_with_inventory = [
        item_id for item_id in items.loc[items["item_type"] == "SUBASSY", "item_id"]
        if item_id in inputs["initial_inventory"] and inputs["initial_inventory"][item_id] > 0 and item_id in material_by_item
    ]
    if subassy_with_inventory:
        example_item = subassy_with_inventory[0]
        code = items.set_index("item_id").loc[example_item, "item_code"]
        first_result = sorted(material_by_item[example_item], key=lambda r: r.period_start_date)[0]
        print(f"item={code} (low_level_code={llc.get(example_item)}) week={first_result.period_start_date}")
        print(f"  gross_requirement = {first_result.gross_requirement:.2f}")
        print(f"  usable_inventory  = {first_result.usable_inventory:.2f}")
        print(f"  net_requirement   = {first_result.net_requirement:.2f}  "
              f"(cascades to components -- NOT the gross {first_result.gross_requirement:.2f})")

    # --- 5-7. CAB-100 +40% trace, emergence timeline ---
    shock = run_cab100_demand_shock(tables, horizon, demand_multiplier=1.4)
    emergent = shock["emergent_constraints"]
    section(f"5. CAB-100 +40% {len(horizon)}-week trace for emergent-constraint work centre(s) {emergent}")
    for wc_id in emergent:
        rows = []
        for b, s in zip(shock["baseline"].wc_series(wc_id), shock["scenario"].wc_series(wc_id)):
            rows.append({
                "week": b.period_start_date,
                "util_baseline": round(b.utilization_pct, 2) if b.utilization_pct == b.utilization_pct else None,
                "util_scenario": round(s.utilization_pct, 2) if s.utilization_pct == s.utilization_pct else None,
                "backlog_baseline": round(b.backlog_hours_end, 1),
                "backlog_scenario": round(s.backlog_hours_end, 1),
                "class_baseline": b.constraint_classification,
                "class_scenario": s.constraint_classification,
            })
        print(f"\n-- work_centre_id={wc_id} ({wc_df.loc[wc_id, 'process_type']}) --")
        print(pd.DataFrame(rows).to_string(index=False))

    section("6. Primary-constraint emergence timeline (scenario)")
    for wc_id in emergent:
        classes = [r.constraint_classification for r in shock["scenario"].wc_series(wc_id)]
        print(f"work_centre_id={wc_id}: {classes}")

    # --- 8. Four intervention comparison (also produces 7: recovery timeline) ---
    section("7/8. Four-intervention comparison + capacity-recovery timeline")
    comparison = run_four_intervention_comparison(tables, horizon, demand_multiplier=1.4)
    print(f"Emergent constraint work centre(s): {comparison['emergent_constraints']}")
    print(f"Intervention start week: {comparison['intervention_start_week']}")
    print(f"Smallest recovering capacity multiplier found: {comparison['capacity_multiplier']}")

    for wc_id in comparison["emergent_constraints"]:
        rows = []
        for label in ("BASELINE", "DEMAND_SHOCK_ONLY", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"):
            series = comparison[label].wc_series(wc_id)
            final_backlog = series[-1].backlog_hours_end
            peak_backlog = max(r.backlog_hours_end for r in series)
            rows.append({"case": label, "peak_backlog": round(peak_backlog, 1), "final_backlog": round(final_backlog, 1)})
        print(f"\n-- work_centre_id={wc_id} ({wc_df.loc[wc_id, 'process_type']}) --")
        print(pd.DataFrame(rows).to_string(index=False))

        print("\nWeekly backlog by case:")
        weekly_rows = []
        for i, period in enumerate(horizon):
            row = {"week": period}
            for label in ("BASELINE", "DEMAND_SHOCK_ONLY", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"):
                row[label] = round(comparison[label].wc_series(wc_id)[i].backlog_hours_end, 1)
            weekly_rows.append(row)
        print(pd.DataFrame(weekly_rows).to_string(index=False))

    # --- 9. Historical realized forecast example ---
    section("9. Historical realized forecast-revision example")
    history = reconstruct_forecast_history(
        tables["customer_forecasts"], tables["forecast_versions"], tables["sales_order_lines"], tables["sales_orders"]
    )
    key = find_best_historical_example(history)
    if key:
        customer_id, item_id, site_id, delivery = key
        example = history.loc[
            (history["customer_id"] == customer_id) & (history["item_id"] == item_id)
            & (history["site_id"] == site_id) & (history["delivery_period_start"] == delivery)
        ].sort_values("horizon_weeks", ascending=False)
        code = items.set_index("item_id").loc[item_id, "item_code"]
        print(f"customer_id={customer_id}  item={code}  site_id={site_id}  delivery_bucket={pd.Timestamp(delivery).date()}")
        print(example[["snapshot_date", "horizon_weeks", "forecast_qty", "actual_qty"]].to_string(index=False))
        example = example.copy()
        example["abs_error"] = (example["forecast_qty"] - example["actual_qty"]).abs()
        actual = example["actual_qty"].iloc[0]
        example["pct_error"] = example["abs_error"] / actual if actual else float("nan")
        print("\nHorizon-specific error:")
        print(example[["horizon_weeks", "abs_error", "pct_error"]].to_string(index=False))
    else:
        print("No fully historical example with >=4 revisions found in this dataset.")


if __name__ == "__main__":
    main()
