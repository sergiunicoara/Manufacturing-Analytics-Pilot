# Pilot findings from the live synthetic database

Captured from the rebuilt SQL-backed API on 2026-09-28, using seed 42 and the 12-week horizon beginning 2026-06-01. All quantities are simulated. The five reference cases use the same period engine and persisted scenario parameters.

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
| Baseline | 1.10 | 1.04 | 1.08 | 1.35 | 0.97 | 3.72 | 1.97 | 5.84 | 12.94 | 4.29 | 9.10 | 13.37 |
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
