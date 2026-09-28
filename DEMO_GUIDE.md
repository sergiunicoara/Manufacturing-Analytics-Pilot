# Demo guide

1. Start Docker Compose and wait for SQL Server health. Generate and load the seeded dataset if needed.
2. Open Plant Overview and click a KPI card. In the Evidence Drawer, inspect the method, source IDs, calculation trace, and assumptions.
3. Open Demand & Forecast, Production Flow, and BOM Explorer to follow forecast revisions through material netting and component paths.
4. Open Capacity and WIP & Lead Time. Inspect utilization, backlog, and the named constraint classifications.
5. Open Scenario Lab. Compare all five reference cases, then choose a custom demand, buffer, and capacity input and select **Run & save**. Its run ID identifies persisted result rows.
6. Open Recommendation to inspect constraint-gated buffer ranges. Show the stated replenishment assumption.
7. Open Executive Story, then `/api/executive-story` for the complete weekly lead-time decomposition. The baseline deteriorates inside the 12-week story window; do not describe it as stable.
8. Ask the copilot about a valid KPI, then about a nonexistent item ID to show the insufficient-evidence gate.

For welding work centre 9, the calendar is one 8-hour shift across five days: 40 scheduled hours/week. The current 12-week recovery search selects 1.10×, equivalent to four more scheduled hours/week, or 0.8 hour/day at unchanged availability. The earlier 2.5× long-horizon target would imply 100 scheduled hours/week, or 60 additional hours: one full extra eight-hour shift plus four hours per day. Both are calendar arithmetic, not operational feasibility judgments.
