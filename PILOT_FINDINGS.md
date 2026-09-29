# Pilot findings from the live synthetic database

Scenario figures re-verified against the running API on 2026-09-29 (no scenario value changed except one week-6 rounding cell, 3.72 → 3.71). Captured from the rebuilt SQL-backed API on 2026-09-28, using seed 42 and the 12-week horizon beginning 2026-06-01. All quantities are simulated. The five reference cases use the same period engine and persisted scenario parameters.

## What changes in the scenario

| Case | Final backlog hours | CAB-100 demand-weighted mean lead time | Week 12 CAB-100 lead time |
| --- | ---: | ---: | ---: |
| Baseline | 74.4 | 4.24 d | 13.37 d |
| Demand shock only | 212.2 | 10.08 d | 35.48 d |
| Buffer only | 93.1 | 5.27 d | 19.52 d |
| Capacity only | 164.4 | 7.81 d | 25.71 d |
| Combined | 77.3 | 4.58 d | 15.39 d |

Buffer only and capacity only have different physical mechanisms. A buffer supplies inventory before new production is required; capacity increases effective processing hours from the intervention week. In this generated case, buffer only reduces backlog more than the selected 1.10× welding capacity lever over the 12-week horizon. These are computed results, not an enforced ranking.

## Weekly CAB-100 total lead time, days

The values are demand-weighted across CAB-100 finished goods, each evaluated along its own route and longest manufactured BOM branch. Processing, queue, and transfer remain separate in `/api/executive-story`.

| Case | W1 | W2 | W3 | W4 | W5 | W6 | W7 | W8 | W9 | W10 | W11 | W12 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 1.10 | 1.04 | 1.08 | 1.35 | 0.97 | 3.71 | 1.97 | 5.84 | 12.94 | 4.29 | 9.10 | 13.37 |
| Demand shock only | 1.10 | 1.04 | 1.08 | 1.35 | 0.97 | 8.21 | 10.00 | 13.78 | 26.39 | 12.78 | 27.86 | 35.48 |
| Buffer only | 1.10 | 1.04 | 1.08 | 1.31 | 0.97 | 3.71 | 5.25 | 5.29 | 16.69 | 4.91 | 13.51 | 19.52 |
| Capacity only | 1.10 | 1.04 | 1.08 | 1.35 | 0.97 | 7.20 | 7.84 | 10.41 | 21.32 | 8.37 | 21.41 | 25.71 |
| Combined | 1.10 | 1.04 | 1.08 | 1.31 | 0.97 | 3.71 | 4.19 | 5.29 | 15.28 | 3.52 | 10.51 | 15.39 |

The baseline itself deteriorates late in the window, reaching 13.37 days at week 12. The chart must retain that trajectory. Pre-intervention periods remain unchanged in the capacity case; improvement appears after effective capacity has acted on backlog. Buffer begins to change net production need in week 4 and therefore can diverge sooner.

## Physical constraint and intervention

At the last demand-shock week, Welding 1 is the primary constraint at 101.8% required/effective utilization and 146.7 backlog hours. Inspection 1 is a candidate constraint at 114.6% utilization and 35.8 backlog hours. The combined case ends with no primary constraint among its final-week work centres, although backlog is still 77.3 hours. Thus the response reduces modeled risk but does not eliminate all waiting.

The current 12-week recovery search selects 1.10× for Welding 1. Its calendar is one 8-hour shift/day for five days, or 40 scheduled hours/week. At unchanged availability, 1.10× corresponds to 44 scheduled hours/week, adding four hours/week or 0.8 hour/day. The earlier 2.5× long-horizon target corresponds to 100 scheduled hours/week, adding 60 hours/week (one extra 8-hour shift/day plus four more hours/day). Neither translation establishes that staffing or equipment can support the schedule.

## Data quality and recommendations

The live rule engine reports 641 findings, including 563 classified as blocking. Blocking scope is respected by the analytics rather than silently repairing records. The demand-shock run produces 154 analytical buffer candidates upstream of candidate or primary constraints. Each range exposes its gross-usage, queue, demand variation, forecast error where realized, utilization, and replenishment assumptions. The recommendation count is not an instruction to stock all items.

## Practical reading

The generated data demonstrate propagation from CAB-100 demand through subassembly welding, backlog, queue, and lead time. The 12-week baseline is a shared comparison case, not a stable control. The weekly model does not prove order-level delivery dates or operational feasibility; validate those questions with plant calendars and actual item replenishment data before using the outputs for a real decision.

## Added on 2026-09-29: recorded history, economics and policy (synthetic data)

All figures below come from the running API and database. The data is synthetic, and the provenance labels still apply to each value.

### Recorded stage performance
- 5,408 operations are usable completed observations (COMPLETED_VALID), and 95.5 % of the operations that should have produced one did. Excluded and reported separately: 149 completed without a finish time, 178 under a blocking DQ finding on their order, 163 started but unfinished, and 3,790 not started.
- The recorded median is **72 hours** (calendar elapsed). The recorded values run about ten times the routing standard, e.g. Laser Cutting 2 P50 96 h against a standard of 7.8 h. Recorded time includes waiting and non-working time and is not productive processing time. The gap shows the synthetic actuals were not calibrated to the routings; it is not a real plant finding.
- Timestamps are at day resolution, so no same-day observation should be read as zero effort.

### Data-quality explanation
- 641 findings fall into 13 rule groups. 563 are classified blocking, but only 562 unique records carry a blocking finding, and the largest share of any source table is 4.94 % (routing operations). So "88 % of findings are blocking" does not mean 88 % of the data is unusable.

### Decision economics (EUR, synthetic standard costs; carrying rate 20 % assumed)
| Case | Ending backlog (h) | Period expense | Buffer capital (balance) |
| --- | ---: | ---: | ---: |
| Baseline | 74.4 | 359,502 | 0 |
| Demand shock only | 212.2 | 355,030 | 0 |
| Buffer only | 93.1 | 434,038 | 1,771,538 |
| Capacity only | 164.4 | 358,518 | 0 |
| Combined | 77.3 | 437,526 | 1,771,538 |

- Period expense = inventory carrying cost + added paid hours. The capacity case adds 3,488 EUR of paid hours (8 weeks × 4 h × 73 EUR/h at Welding 1 = 2,336, plus 8 weeks × 4 h × 36 EUR/h at work centre 26 = 1,152). Buffer capital is a balance tied up at standard cost and is not added to the expense.
- Baseline carrying cost is slightly higher than the shock case; the cause was not investigated, so no explanation is offered. The table shows expense and residual backlog side by side without ranking the interventions. WIP carrying cost is unavailable, and no ROI is produced.
- Buffer only costs about 79 k EUR more expense and 1.77 M EUR in buffer capital and leaves 93 h of backlog. Capacity only costs about 3.5 k EUR and leaves 164 h. The pilot presents these facts and leaves the choice to planners.

### Planning policy
- At default thresholds 12 finished goods are make-to-order, 1 is assemble-to-order, and 127 have insufficient evidence (mostly fewer than 5 orders or fewer than 3 completed production orders per item). 116 decoupling candidates were found. This shows how thin the synthetic per-item history is; it is not tuned away.
