"""Produces every CP3 report artifact against the live SQL-Server-loaded
dataset: forecast reconstruction, consumption worked example, accuracy by
horizon, three-tier capacity example, a multi-week CAB-100 trace, backlog/WIP
continuity evidence, a constraint-classification timeline, a material-
shortage example, and the two named golden scenarios.

Usage: python -m app.analytics.run_cp3_report
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.analytics.data_access import load_all_tables
from app.analytics.forecast import (
    compute_accuracy_by_horizon,
    consume_forecast,
    reconstruct_forecast_history,
)
from app.analytics.scenario_demo import build_engine_inputs, cab100_item_ids, run_golden_scenarios, welding_work_centre_ids
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

    # --- 1. Forecast reconstruction example ---
    section("1. Forecast reconstruction example")
    history = reconstruct_forecast_history(
        tables["customer_forecasts"], tables["forecast_versions"], tables["sales_order_lines"], tables["sales_orders"]
    )
    busiest = history.groupby(["customer_id", "item_id", "site_id", "delivery_period_start"]).size().sort_values(ascending=False)
    top_key = busiest.index[0]
    example = history.loc[
        (history["customer_id"] == top_key[0]) & (history["item_id"] == top_key[1])
        & (history["site_id"] == top_key[2]) & (history["delivery_period_start"] == top_key[3])
    ].sort_values("horizon_weeks", ascending=False)
    item_code = items.set_index("item_id").loc[top_key[1], "item_code"]
    print(f"customer_id={top_key[0]}  item={item_code}  site_id={top_key[2]}  delivery_bucket={top_key[3].date()}")
    print(example[["snapshot_date", "horizon_weeks", "forecast_qty", "actual_qty"]].to_string(index=False))

    # --- 2. Forecast consumption worked example ---
    section("2. Forecast consumption worked example (non-double-counting)")
    consumption = consume_forecast(
        tables["customer_forecasts"], tables["forecast_versions"], tables["sales_order_lines"], tables["sales_orders"]
    )
    remaining = consumption["remaining_forecast"]
    consumed_mask = remaining["forecast_qty"] > remaining["remaining_qty"]
    if consumed_mask.any():
        row = remaining.loc[consumed_mask].iloc[0]
        code = items.set_index("item_id").loc[row["item_id"], "item_code"]
        consumed = row["forecast_qty"] - row["remaining_qty"]
        print(f"item={code}  customer_id={row['customer_id']}  delivery_bucket={row['delivery_period_start'].date()}")
        print(f"  forecast_qty = {row['forecast_qty']:.1f}")
        print(f"  consumed by firm orders = {consumed:.1f}")
        print(f"  remaining_qty (forecast - consumed) = {row['remaining_qty']:.1f}")
        print(f"  planning_demand = remaining_qty + firm_orders = {row['remaining_qty'] + consumed:.1f} "
              f"(NOT forecast_qty + firm_orders = {row['forecast_qty'] + consumed:.1f})")

    # --- 3. Accuracy metrics by horizon ---
    section("3. Forecast accuracy by horizon (WAPE headline)")
    accuracy = compute_accuracy_by_horizon(history)
    print(accuracy.to_string(index=False))

    # --- 4. Three-tier capacity example ---
    section("4. Three-tier capacity example")
    cal = tables["capacity_calendar"]
    sample_row = cal.iloc[len(cal) // 2]
    print(f"work_centre_id={sample_row['work_centre_id']}  week={pd.Timestamp(sample_row['week_start_date']).date()}")
    print(f"  calendar_hours   = {sample_row['calendar_hours']:.2f}")
    print(f"  planned_downtime = {sample_row['planned_downtime_hours']:.2f}")
    print(f"  available_hours  = {sample_row['available_hours']:.2f}  (calendar - planned_downtime)")
    print(f"  availability_pct = {sample_row['availability_pct']:.4f}")
    print(f"  effective_hours  = {sample_row['effective_hours']:.2f}  (available x availability_pct)")

    # --- Engine inputs / golden scenarios ---
    horizon = [REFERENCE_DATE + dt.timedelta(weeks=w) for w in range(10)]
    results = run_golden_scenarios(tables, horizon)
    baseline = results["baseline"]
    weld_ids = results["welding_work_centre_ids"]
    cab100_ids = results["cab100_item_ids"]

    # --- 5. Multi-week CAB-100-related work-centre trace (baseline) ---
    section(f"5. {len(horizon)}-week period-engine trace, welding work centres (baseline)")
    for wc_id in weld_ids:
        print(f"\n-- work_centre_id={wc_id} --")
        rows = []
        for r in baseline.wc_series(wc_id):
            rows.append({
                "week": r.period_start_date, "required_h": round(r.required_hours, 1),
                "effective_h": round(r.effective_hours, 1),
                "util": "nan" if r.utilization_pct != r.utilization_pct else round(r.utilization_pct, 2),
                "backlog_start": round(r.backlog_hours_start, 1), "backlog_end": round(r.backlog_hours_end, 1),
                "wip_qty": round(r.wip_qty, 1),
                "queue_days": "nan" if r.queue_time_days != r.queue_time_days else round(r.queue_time_days, 2),
                "constraint": r.constraint_classification,
            })
        print(pd.DataFrame(rows).to_string(index=False))

    # --- 6. Backlog/WIP continuity evidence ---
    section("6. Backlog continuity evidence (backlog_end(t) == backlog_start(t+1))")
    for wc_id in weld_ids:
        series = baseline.wc_series(wc_id)
        ok = all(abs(series[i].backlog_hours_end - series[i + 1].backlog_hours_start) < 1e-9 for i in range(len(series) - 1))
        print(f"work_centre_id={wc_id}: continuity holds across all {len(series)} periods = {ok}")

    # --- 7. Constraint classification timeline ---
    section("7. Constraint classification timeline (baseline)")
    for wc_id in weld_ids:
        series = baseline.wc_series(wc_id)
        timeline = [r.constraint_classification for r in series]
        print(f"work_centre_id={wc_id}: {timeline}")

    # --- 8. Material shortage example ---
    section("8. Material-shortage-is-the-limiting-factor example")
    shortages = [r for r in baseline.material_results if r.shortage_flag]
    shortages_sorted = sorted(shortages, key=lambda r: r.net_requirement, reverse=True)
    for r in shortages_sorted[:3]:
        print(f"  {r.item_code:30s} gross={r.gross_requirement:>10.1f} inv={r.usable_inventory:>10.1f} "
              f"receipts={r.scheduled_receipts:>10.1f} net(shortage)={r.net_requirement:>10.1f} week={r.period_start_date}")

    # --- 9/10. Golden scenarios ---
    section("9. CAB-100 +40% demand golden scenario")
    scenario = results["scenario_demand_plus_40"]
    for wc_id in weld_ids:
        base_total = sum(r.required_hours for r in baseline.wc_series(wc_id))
        scen_total = sum(r.required_hours for r in scenario.wc_series(wc_id))
        base_peak_util = max((r.utilization_pct for r in baseline.wc_series(wc_id) if r.utilization_pct == r.utilization_pct), default=float("nan"))
        scen_peak_util = max((r.utilization_pct for r in scenario.wc_series(wc_id) if r.utilization_pct == r.utilization_pct), default=float("nan"))
        print(f"work_centre_id={wc_id}: required hours {base_total:.0f}h -> {scen_total:.0f}h "
              f"({(scen_total/base_total - 1)*100:+.1f}%); peak utilization {base_peak_util:.2f} -> {scen_peak_util:.2f}")

        base_classes = [r.constraint_classification for r in baseline.wc_series(wc_id)]
        scen_classes = [r.constraint_classification for r in scenario.wc_series(wc_id)]

        def first_week(classes, target):
            for i, c in enumerate(classes):
                if c == target:
                    return horizon[i]
            return None

        print(f"  first OVERLOADED week: baseline={first_week(base_classes,'OVERLOADED')}, scenario={first_week(scen_classes,'OVERLOADED')}")
        print(f"  first CANDIDATE_CONSTRAINT week: baseline={first_week(base_classes,'CANDIDATE_CONSTRAINT')}, scenario={first_week(scen_classes,'CANDIDATE_CONSTRAINT')}")
        print(f"  first PRIMARY_CONSTRAINT week: baseline={first_week(base_classes,'PRIMARY_CONSTRAINT')}, scenario={first_week(scen_classes,'PRIMARY_CONSTRAINT')}")

    section("10. Welding +30% capacity intervention (on top of +40% demand)")
    intervention = results["scenario_plus_capacity_plus_30"]
    for wc_id in weld_ids:
        scen_series = scenario.wc_series(wc_id)
        interv_series = intervention.wc_series(wc_id)
        print(f"\nwork_centre_id={wc_id}:")
        print(f"{'week':12s} {'backlog(+40% only)':>20s} {'backlog(+40%,+30%cap)':>24s}")
        for s, i in zip(scen_series, interv_series):
            print(f"{str(s.period_start_date):12s} {s.backlog_hours_end:>20.1f} {i.backlog_hours_end:>24.1f}")


if __name__ == "__main__":
    main()
