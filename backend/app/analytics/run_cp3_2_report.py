"""Produces every CP3.2 acceptance-criteria artifact against the live
SQL-Server-loaded dataset.

Usage: python -m app.analytics.run_cp3_2_report
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.analytics.data_access import load_all_tables
from app.analytics.period_engine import _avg_minutes_per_unit_by_work_centre, run_period_engine
from app.analytics.reconciliation import compute_system_kpis, reconcile_material_flow, reconcile_work_centre_hours
from app.analytics.scenario_demo import (
    build_engine_inputs,
    cab100_item_ids,
    items_loading_work_centres,
    run_cab100_demand_shock,
    run_four_intervention_comparison,
)
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

    # --- 1. Baseline utilization table, all active work centres ---
    section("1. Baseline utilization, all active work centres (16-week avg)")
    rows = []
    for wc_id in sorted(set(r.work_centre_id for r in baseline.work_centre_results)):
        series = baseline.wc_series(wc_id)
        utils = [r.utilization_pct for r in series if r.utilization_pct == r.utilization_pct]
        avg_u = sum(utils) / len(utils) if utils else float("nan")
        rows.append({"work_centre_id": wc_id, "process_type": wc_df.loc[wc_id, "process_type"], "avg_utilization": round(avg_u, 2)})
    dist = pd.DataFrame(rows).sort_values("avg_utilization", ascending=False)
    print(dist.to_string(index=False))
    active = dist.loc[dist["avg_utilization"] > 0.01]
    band_55_85 = ((active["avg_utilization"] >= 0.55) & (active["avg_utilization"] <= 0.85)).sum()
    band_85_95 = ((active["avg_utilization"] > 0.85) & (active["avg_utilization"] <= 0.95)).sum()
    over_100 = (active["avg_utilization"] > 1.0).sum()
    print(f"\nOf {len(active)} active work centres: {band_55_85} in 55-85% band, {band_85_95} in 85-95% band, "
          f"{over_100} averaging above 100%.")

    # --- 2. Plant-wide baseline backlog/WIP/lead-time trend ---
    section("2. Plant-wide baseline backlog/WIP/lead-time trend")
    cab100_ids = cab100_item_ids(items)
    lead_time_item = cab100_ids[0]
    avg_minutes_per_unit = _avg_minutes_per_unit_by_work_centre(tables["routing_operations"])
    wc_results_by_period = {(r.work_centre_id, r.period_start_date): r for r in baseline.work_centre_results}
    from app.analytics.period_engine import compute_lead_time_for_item

    trend_rows = []
    for period in horizon:
        total_backlog = sum(r.backlog_hours_end for r in baseline.work_centre_results if r.period_start_date == period)
        total_wip = sum(r.wip_qty for r in baseline.work_centre_results if r.period_start_date == period)
        lt = compute_lead_time_for_item(lead_time_item, period, tables["routing_headers"], tables["routing_operations"], wc_results_by_period,
            tables["items"], tables["bom_headers"], tables["bom_components"])
        trend_rows.append({
            "week": period, "total_backlog_hours": round(total_backlog, 1), "total_wip_qty": round(total_wip, 1),
            "cab100_lead_time_days": round(lt.total_days, 2) if lt else None,
        })
    print(pd.DataFrame(trend_rows).to_string(index=False))

    # --- 3. Persistent constraint count ---
    section("3. Persistent constraint count (baseline, 16 weeks)")
    persistent = 0
    for wc_id in sorted(set(r.work_centre_id for r in baseline.work_centre_results)):
        classes = [r.constraint_classification for r in baseline.wc_series(wc_id)]
        if "CANDIDATE_CONSTRAINT" in classes or "PRIMARY_CONSTRAINT" in classes:
            persistent += 1
            print(f"  work_centre_id={wc_id} ({wc_df.loc[wc_id, 'process_type']}): {classes}")
    print(f"\n{persistent} of {len(dist)} work centres ever reach CANDIDATE/PRIMARY_CONSTRAINT in 16 weeks.")

    # --- 4/5/6. Shock + emergence + per-WC recovery sizing ---
    shock = run_cab100_demand_shock(tables, horizon, demand_multiplier=1.4)
    emergent = shock["emergent_constraints"]
    section("4. Selected Executive Story scenario")
    print("Demand driver: CAB-100 family, +40% (matches the pilot's established narrative; see PLAN.md).")
    print(f"Naturally emergent constraint work centre(s) (generic diff of baseline vs. scenario classification, "
          f"NOT assumed in advance): {emergent}")
    for wc_id in emergent:
        print(f"  work_centre_id={wc_id}: process_type={wc_df.loc[wc_id, 'process_type']}")
    print("Selected because: it is the only/strongest work centre that goes from never-persistently-constrained "
          "at baseline to persistently constrained under the CAB-100 shock -- exactly the 'healthy baseline -> "
          "localized shock-induced constraint' story, discovered from the calibrated data rather than assumed.")

    section(f"5. Shock-vs-baseline weekly trace, work centre(s) {emergent}")
    for wc_id in emergent:
        rows = []
        for b, s in zip(shock["baseline"].wc_series(wc_id), shock["scenario"].wc_series(wc_id)):
            rows.append({
                "week": b.period_start_date,
                "util_baseline": round(b.utilization_pct, 2) if b.utilization_pct == b.utilization_pct else None,
                "util_scenario": round(s.utilization_pct, 2) if s.utilization_pct == s.utilization_pct else None,
                "backlog_baseline": round(b.backlog_hours_end, 1), "backlog_scenario": round(s.backlog_hours_end, 1),
                "class_baseline": b.constraint_classification, "class_scenario": s.constraint_classification,
            })
        print(f"\n-- work_centre_id={wc_id} ({wc_df.loc[wc_id, 'process_type']}) --")
        print(pd.DataFrame(rows).to_string(index=False))

    section("6. Per-work-centre minimum recovery multiplier(s)")
    comparison = run_four_intervention_comparison(tables, horizon, demand_multiplier=1.4)
    print(f"Intervention start week: {comparison['intervention_start_week']}")
    for wc_id, mult in comparison["per_work_centre_multipliers"].items():
        print(f"  work_centre_id={wc_id} ({wc_df.loc[wc_id, 'process_type']}): smallest recovering multiplier = {mult}")

    # --- 7. System-level KPI comparison ---
    section("7. System-level comparison: BASELINE / SHOCK / BUFFER_ONLY / CAPACITY_ONLY / COMBINED")
    buffer_targets = items_loading_work_centres(tables["routing_headers"], tables["routing_operations"], emergent)
    total_demand = sum(
        qty for period_demand in shock["scenario_inputs"]["top_level_demand"].values()
        for item_id, qty in period_demand.items() if item_id in cab100_ids
    ) * 1.4 / 1.4  # already scaled in scenario_inputs; kept explicit for clarity

    kpi_rows = []
    for label in ("BASELINE", "DEMAND_SHOCK_ONLY", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"):
        output = comparison[label]
        kpis = compute_system_kpis(
            label, output, constrained_work_centre_ids=emergent, total_demand_units=total_demand,
            lead_time_item_id=lead_time_item, routing_headers_df=tables["routing_headers"],
            routing_operations_df=tables["routing_operations"], horizon=horizon,
            avg_minutes_per_unit_by_wc=avg_minutes_per_unit,
            items_df=tables["items"], bom_headers_df=tables["bom_headers"], bom_components_df=tables["bom_components"],
            lead_time_item_ids=cab100_ids,
        )
        kpi_rows.append({
            "case": label, "completed_units_est": round(kpis.completed_units_estimate, 1),
            "ending_wip": round(kpis.ending_system_wip, 1), "ending_backlog_h": round(kpis.ending_total_backlog_hours, 1),
            "overdue_units_est": round(kpis.overdue_unmet_units_estimate, 1),
            "avg_lead_time_d": round(kpis.avg_lead_time_days, 2) if kpis.avg_lead_time_days == kpis.avg_lead_time_days else None,
            "plant_avg_lead_time_d": round(kpis.plant_avg_lead_time_days, 2) if kpis.plant_avg_lead_time_days == kpis.plant_avg_lead_time_days else None,
            "p90_lead_time_d": round(kpis.p90_lead_time_days, 2) if kpis.p90_lead_time_days == kpis.p90_lead_time_days else None,
            "p95_lead_time_d": round(kpis.p95_lead_time_days, 2) if kpis.p95_lead_time_days == kpis.p95_lead_time_days else None,
            "service_risk": kpis.service_risk,
        })
    print(pd.DataFrame(kpi_rows).to_string(index=False))

    for wc_id in emergent:
        print(f"\n-- constrained work_centre_id={wc_id} detail --")
        for label in ("BASELINE", "DEMAND_SHOCK_ONLY", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"):
            series = comparison[label].wc_series(wc_id)
            utils = [r.utilization_pct for r in series if r.utilization_pct == r.utilization_pct]
            avg_u = sum(utils) / len(utils) if utils else float("nan")
            print(f"  {label:18s} avg_util={avg_u:.2f}  ending_backlog={series[-1].backlog_hours_end:.1f}h")

    # --- 8. Flow-conservation reconciliation for all five runs ---
    section("8. Flow-conservation reconciliation (all five runs)")
    for wc_id in emergent:
        print(f"\n-- work_centre_id={wc_id} hours reconciliation --")
        for label in ("BASELINE", "DEMAND_SHOCK_ONLY", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"):
            rec = reconcile_work_centre_hours(comparison[label], wc_id)
            print(f"  {label:18s} required={rec.total_required_hours:9.1f}  completed={rec.total_completed_hours:9.1f}  "
                  f"ending_backlog={rec.final_backlog_hours:8.1f}  discrepancy={rec.discrepancy:+.6f}  holds={rec.holds}")

    if buffer_targets:
        # Pick the buffer-targeted item with the most demand pressure in the
        # shock scenario, not just the first -- otherwise the example can
        # land on a variant with zero demand this horizon and show nothing.
        gross_by_target = {
            item_id: sum(r.gross_requirement for r in comparison["DEMAND_SHOCK_ONLY"].material_series(item_id))
            for item_id in buffer_targets
        }
        example_item = max(gross_by_target, key=gross_by_target.get)
        code = items.set_index("item_id").loc[example_item, "item_code"]
        print(f"\n-- item={code} material-flow reconciliation --")
        for label in ("BASELINE", "DEMAND_SHOCK_ONLY", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"):
            rec = reconcile_material_flow(comparison[label], example_item)
            print(f"  {label:18s} gross={rec.total_gross_requirement:9.1f}  from_inventory={rec.total_satisfied_from_inventory:8.1f}  "
                  f"from_receipts={rec.total_satisfied_from_receipts:8.1f}  net={rec.total_net_requirement:8.1f}  "
                  f"discrepancy={rec.discrepancy:+.6f}  holds={rec.holds}")

        # --- 9. Where BUFFER_ONLY's work goes ---
        section("9. Where BUFFER_ONLY's backlog reduction goes")
        shock_rec = reconcile_material_flow(comparison["DEMAND_SHOCK_ONLY"], example_item)
        buffer_rec = reconcile_material_flow(comparison["BUFFER_ONLY"], example_item)
        print(f"item={code}")
        print(f"  DEMAND_SHOCK_ONLY: satisfied_from_inventory={shock_rec.total_satisfied_from_inventory:.1f}, "
              f"net_requirement={shock_rec.total_net_requirement:.1f}")
        print(f"  BUFFER_ONLY:       satisfied_from_inventory={buffer_rec.total_satisfied_from_inventory:.1f}, "
              f"net_requirement={buffer_rec.total_net_requirement:.1f}")
        print(f"  Delta satisfied_from_inventory: {buffer_rec.total_satisfied_from_inventory - shock_rec.total_satisfied_from_inventory:+.1f}")
        print(f"  Delta net_requirement:          {buffer_rec.total_net_requirement - shock_rec.total_net_requirement:+.1f}")
        print("  Interpretation: the buffer represents stock already produced before week 1 (off-horizon). "
              "Consuming it satisfies demand without new production, which is why less net requirement -- and "
              "hence less work-centre load -- was needed. Capacity (calendar/available/effective hours) is "
              "provably unchanged between these two runs (see test_reconciliation.py); demand "
              "(gross_requirement) is provably unchanged too. Nothing was destroyed or manufactured from capacity.")


if __name__ == "__main__":
    main()
