# Assumptions

- All entities and transactions are seeded, fictional data. The default generator seed is 42.
- The analytical planning horizon is configurable to 26 weeks. The Executive Story uses 12 weeks, starting 2026-06-01, to include the demand shock and response. Baseline lead time deteriorates within that window and is shown as such.
- Weekly rough-cut capacity assigns an item's routed load to the requirement week. This is not a finite, operation-by-operation schedule.
- Firm orders consume forecast in the same customer, item, and site stream within a four-week delivery window. Remaining forecast plus full firm orders becomes planning demand.
- The engine maintains weekly work-centre backlog and material inventory state. Backlog at order entry, rather than ending backlog, determines congestion queue delay.
- A capacity multiplier changes effective hours beginning in its specified week. It does not alter routing processing standards or historical periods.
- Buffer boosts are one-time initial inventory additions. They reduce net production requirement but do not create physical processing capacity.
- Analytical buffer recommendations assume a seven-day replenishment planning cycle plus observed queue exposure because the item master lacks item-specific replenishment lead time. They are ranges for review, not automatic inventory policy.
- The work-centre calendar translation assumes unchanged availability and efficiency. Staffing, equipment, maintenance, overtime law, and safety feasibility are not modeled.
