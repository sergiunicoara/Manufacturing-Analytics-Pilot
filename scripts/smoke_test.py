"""CP1 smoke test: connects to SQL Server and confirms row counts per table
are within the target scale documented in PLAN.md / app/config.py. Run
inside the api container (has pyodbc + the driver):

  docker compose run --rm api python scripts/smoke_test.py

(mounted at /app/scripts via the same volume as staging/, or run with
PYTHONPATH set appropriately — see README "Running the smoke test.")
"""
from __future__ import annotations

import sys

import sqlalchemy as sa

from app.db.connection import get_engine

EXPECTED_RANGES = {
    "sites": (1, 1),
    "warehouses": (2, 5),
    "customers": (20, 100),
    "suppliers": (5, 30),
    "work_centres": (10, 30),
    "items": (500, 2000),
    "bom_headers": (100, 5000),
    "bom_components": (500, 10000),
    "routing_headers": (100, 5000),
    "routing_operations": (500, 10000),
    "forecast_versions": (26, 60),
    "customer_forecasts": (1000, 100000),
    "sales_orders": (500, 20000),
    "sales_order_lines": (1000, 30000),
    "production_orders": (2000, 8000),
    "production_order_operations": (2000, 50000),
    "purchase_orders": (50, 5000),
    "purchase_order_lines": (50, 5000),
    "capacity_calendar": (100, 5000),
    "standard_costs": (400, 2000),
    "inventory": (1000, 100000),
    "wip": (10, 5000),
}

FG_ITEM_RANGE = (50, 150)


def main() -> int:
    engine = get_engine()
    failures = []

    with engine.connect() as conn:
        for table, (lo, hi) in EXPECTED_RANGES.items():
            count = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            status = "OK" if lo <= count <= hi else "OUT OF RANGE"
            print(f"{table:35s} {count:>7d}   expected [{lo}, {hi}]   {status}")
            if status != "OK":
                failures.append(table)

        fg_count = conn.execute(
            sa.text("SELECT COUNT(*) FROM items WHERE item_type = 'FG'")
        ).scalar_one()
        lo, hi = FG_ITEM_RANGE
        status = "OK" if lo <= fg_count <= hi else "OUT OF RANGE"
        print(f"{'items (FG only)':35s} {fg_count:>7d}   expected [{lo}, {hi}]   {status}")
        if status != "OK":
            failures.append("items (FG only)")

        dq_count = conn.execute(sa.text("SELECT COUNT(*) FROM dq_findings")).scalar_one()
        print(f"{'dq_findings (CP2+)':35s} {dq_count:>7d}   (expected 0 until CP2 runs the DQ engine)")

    if failures:
        print(f"\nFAILED: tables out of expected range: {failures}")
        return 1

    print("\nAll row counts within expected range.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
