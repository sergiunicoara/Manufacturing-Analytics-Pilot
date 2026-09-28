# Analytical methods

These anchors match the `formula` references attached to API evidence objects.

## forecast-reconstruction

Preserve each weekly forecast revision and join eventual firm-order quantity by customer, item, site, and delivery bucket. Do not aggregate revisions before measuring error.

## forecast-accuracy

WAPE = sum absolute forecast error / sum actual quantity for each horizon bucket. Report `null` when the denominator is zero. MAE and signed bias are companion measures; MAPE is not the headline metric because near-zero actuals are unstable.

## bom-explosion

Select effective BOM revisions for the requested date, traverse component paths, multiply quantity and scrap factors, and retain lineage. Block orphan, cyclic, and ambiguous paths instead of guessing.

## material-netting

At each level and week: `net = max(0, gross - usable inventory - scheduled receipts)`. Net parent demand explodes to children in low-level-code order. Inventory carries between weeks.

## three-tier-capacity

Calendar hours follow shifts/day × hours/shift × operating days/week. Availability reduces calendar to available hours; efficiency reduces available to effective hours. Utilization = required hours / effective hours × 100. All three tiers appear in capacity evidence.

## period-engine

Weekly backlog end = `max(0, backlog start + required hours - effective hours)`. Next week's start equals this week's end. Completed hours, WIP estimate, and classification are derived from the same state. No arbitrary intervention adjustment is applied to lead time.

## constraint-classification

`OVERLOADED` denotes current overload. Consecutive overload is required for `CANDIDATE_CONSTRAINT`; a primary constraint is selected among candidates by the engine's tie-breaking rule. A single overloaded week alone does not qualify a work centre for buffer recommendations.

## lead-time

For each finished good, include its routing and the longest manufactured subassembly BOM branch. Processing uses setup amortized by routing batch plus run standard, scaled by BOM quantity. Queue comprises routing standard queue and congestion from **backlog hours at period entry / effective hours per operating workday**. Convert operating-day delay to calendar days using operating days/week. Transfer remains a separate calendar-day component. Total = processing + queue + transfer. The comparison weights finished-good lead times by their period demand.

## scenario-comparison

The five reference cases share a seed and 12-week horizon: BASELINE, demand shock, buffer only, capacity only, combined. CAB-100 finished-good demand is increased by 40% in the four shock cases. Capacity starts when overload emerges and is sized per work centre; buffer adds initial inventory to items routed through emergent constraints. Display trajectories and final state without forcing a preferred ordering.

## buffer-recommendations

Only items routed through a work centre classified `CANDIDATE_CONSTRAINT` or `PRIMARY_CONSTRAINT` are eligible. Average daily usage = sum weekly gross requirement / number of calendar days. Exposure time = seven-day replenishment cycle + maximum observed queue days. Base = daily usage × exposure time. The uncertainty spread is capped at 75% and combines 25% of capped demand CV, 25% of capped realized item forecast WAPE when available, and up to 10% for utilization above 85%. These risk signals widen the range without changing base exposure. Missing realized forecast history is disclosed and lowers confidence. The method lacks item-specific supplier lead time, so every recommendation states that assumption.

## data-quality

The registered rules classify findings as tolerable, assumption-based, or blocking. Blocking scope distinguishes global, entity, and KPI effects. Blocked branches are excluded from calculations that require them; findings remain visible in the dashboard.

## scenario-persistence

Each scenario stores its parameters, seed, material requirements, weekly work-centre results, and any recommendations in a transaction. The five reference cases are reused by matching name, parameter JSON, and seed. Custom runs are append-only.
