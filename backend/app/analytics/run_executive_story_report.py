"""Print the 12-week, demand-weighted CAB-100 lead-time series for all story cases."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.analytics.data_access import load_all_tables
from app.analytics.leadtime import LeadTimeCalculator
from app.analytics.scenario_demo import cab100_item_ids, run_four_intervention_comparison
from app.db.connection import get_engine
from app.synthetic.timeline import REFERENCE_DATE


def main() -> None:
    tables = load_all_tables(get_engine())
    horizon = [REFERENCE_DATE + dt.timedelta(weeks=i) for i in range(12)]
    cases = run_four_intervention_comparison(tables, horizon, demand_multiplier=1.4)
    cab_ids = cab100_item_ids(tables["items"])
    calculator = LeadTimeCalculator(tables["routing_headers"], tables["routing_operations"], tables["items"],
                                    tables["bom_headers"], tables["bom_components"])
    for label in ("BASELINE", "DEMAND_SHOCK_ONLY", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"):
        output = cases[label]
        state = {(r.work_centre_id, r.period_start_date): r for r in output.work_centre_results}
        rows = []
        for period in horizon:
            vals = {k: 0.0 for k in ("processing_days", "queue_days", "transfer_days", "total_days")}
            total = 0.0
            for item_id in cab_ids:
                lt = calculator.compute(item_id, period, state)
                material = next((x for x in output.material_series(item_id) if x.period_start_date == period), None)
                weight = material.gross_requirement if material else 0.0
                if lt is None or weight <= 0:
                    continue
                total += weight
                for key in vals:
                    vals[key] += getattr(lt, key) * weight
            rows.append({"week": period.isoformat(), **{k: round(v / total, 2) if total else None for k, v in vals.items()}})
        print(f"\n{label}")
        print(pd.DataFrame(rows).to_string(index=False))
    print("\nWELDING CAPACITY CALENDAR")
    wc = tables["work_centres"].loc[tables["work_centres"]["process_type"] == "WELDING"]
    print(wc[["work_centre_id", "name", "shifts_per_day", "hours_per_shift", "days_per_week"]].to_string(index=False))
    print("\nRECOVERY MULTIPLIERS")
    print(cases["per_work_centre_multipliers"])


if __name__ == "__main__":
    main()
