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

Each scenario stores its parameters, seed, data version, material requirements, weekly work-centre results, and any recommendations in a transaction. The data version combines a content fingerprint of the source tables (row order and load timestamps ignored) with a hash of the analytics and DQ code. The five reference cases are reused only when name, parameter JSON, seed **and** data version all match, so a result computed from different inputs is never served as current; earlier runs stay as audit history. Custom runs are append-only. Changing source data while the API is running still needs an API restart to rebuild the in-process cache.

## data-quality-explanation

Findings are grouped by rule, source entity, origin, classification and impact scope. Each group reports unique affected records and their share of the source table's rows. The share of findings marked BLOCKING is not the share of unusable data: a finding can withhold one KPI or one entity, and several findings can hit one record. The full list is paginated at `/api/dq/findings` and exported without truncation at `/api/dq/findings.csv`. Persisted `dq_findings` rows keep `impact_scope`, `affected_entity_type` and `affected_entity_id`.

## forecast-consumption

Nearest eligible bucket: each order line consumes only the forecast bucket for the same customer, item and site whose delivery week is closest to the requested ship date, within ±4 weeks (`forecast_consumption_window_weeks`). Ties go to the earlier-listed bucket. There is no spillover: quantity above that bucket's remaining forecast stays firm demand only. Orders consume in order-date sequence. Planning demand = firm orders + unconsumed forecast. Every consumption event is written to `forecast_consumption`.

## recorded-stage-elapsed

Source: `production_order_operations.actual_start` / `actual_finish` (synthetic timestamps; most carry a time of day, a minority are day-resolution only, and the evidence reports the mix). Each operation gets exactly one record class: COMPLETED_VALID, COMPLETED_MISSING_START, COMPLETED_MISSING_FINISH, INVALID_ORDERING, STATUS_CONFLICT, DUPLICATE (same order and sequence after the first), DQ_EXCLUDED (order under an ENTITY_BLOCKING finding), OPEN_STARTED, NOT_STARTED or OUTSIDE_WINDOW. Elapsed hours = finish − start, for COMPLETED_VALID only; unfinished or invalid operations are never zero-duration observations. Statistics are count, median, P85 and P95 (linear interpolation). Coverage = valid observations ÷ operations that are COMPLETED or carry a finish time. Recorded elapsed time is calendar time, not productive processing time. The gap between consecutive operations is an inter-operation elapsed gap, not proven queue time; negative gaps are counted as overlaps and never clamped. The modelled comparison is the routing-standard processing time for the same operations: (setup × ceil(qty / batch) + run × qty / yield) / 60. The SQL twin is `vw_stage_elapsed` / `vw_stage_elapsed_by_work_centre`, with parity tested.

## wip-ageing

Age = snapshot_date − stage_entered_at, in days, for the latest recorded `wip` snapshot. A stage entry after the snapshot is INVALID_AGE and excluded. Recorded WIP is separate from the period engine's projected WIP.

## cost

- **Currency and valuation date:** EUR, a synthetic label; standard cost effective on the horizon start.
- **Unit cost:** the effective `standard_costs` record's material + labour + overhead. It is not re-summed over the BOM, because stored material already rolls up the direct components and overhead is already money. Re-summing would double-count.
- **Provenance and unknowns:** a record whose BOM has manufactured components carries approximated component material, so it is ASSUMED. Missing or overlapping records are unknown, never zero.
- **Inventory capital:** the engine's end-of-period inventory state × unit cost. Items with no requirement that week are included, and the exposure convention is that the end-of-week balance is held for one week.
- **Carrying cost:** Σ weekly inventory value × annual rate ÷ 52 (ASSUMED, 20 %).
- **Buffer capital:** one-time boost quantity × unit cost. It is a balance, not an expense or a cash flow.
- **Intervention cost:** added scheduled hours per week = shifts × hours × days × (multiplier − 1), for the weeks the lever is active, × `cost_per_hour` × premium (ASSUMED 1.0). Effective hours are not paid hours; equipment and hiring are not modelled.
- **WIP carrying cost:** unavailable, because engine WIP mixes items per work centre.
- **Headline:** period expense = carrying + added hours, with residual backlog hours shown next to it. No ROI or profit figure is computed. Results are persisted in `cost_results` per run.

## planning-policy

For each finished good:
- **Customer tolerance:** the median of (requested ship − order date).
- **Own route:** the median recorded elapsed days of completed production orders.
- **Cumulative:** own route + the longest manufactured-component branch.

Rules, applied in order:
1. cumulative ≤ tolerance → MAKE_TO_ORDER
2. own route ≤ tolerance < cumulative, with a manufactured component → ASSEMBLE_TO_ORDER
3. own route > tolerance → MAKE_TO_STOCK
4. any input below its evidence threshold → INSUFFICIENT_EVIDENCE

The thresholds (`PolicyThresholds`) are configurable. Demand CV, zero-demand share and realized WAPE only lower confidence; they never change the policy. Decoupling candidates are manufactured components common to ≥ 2 finished goods, or routed through a candidate or primary constraint. Policy selection is separate from buffer sizing, and this is a heuristic, not a DDMRP implementation.

## parameter-package

The package is a versioned JSON/CSV review package (schema at `/api/parameters/schema.json`). It holds policy, analytical buffer min/max and decoupling-candidate rows, keyed by item code and site code, with effective date, source run, provenance, confidence and blockers. Every native M3 field is `UNMAPPED` until verified, and `erp_write_back` is always false.

## shop-floor-flow

A discrete-event simulation beside the weekly period engine (`backend/app/analytics/flow/`). It takes the same finished-good
demand, usable stock and open purchase-order receipts as the engine (`build_engine_inputs`), releases demand in lots of at
most 60 units, nets each lot against stock down the current BOM, and gives every shortfall a child lot (released one week
earlier per BOM level). A lot starts when all its components exist; its operations follow the item's current routing, one
work centre and one operation at a time, inside the capacity calendar's shifts (productive time = work content divided by the
work centre's average availability). Dispatch is earliest due date. Routing operations that are blocked by data quality or lack
a work centre or standard times are left out and counted, as in the engine; items with overlapping revisions are excluded with
the demand that depends on them. Open production orders are not used: they overload Welding 1 about 15 times because they were
generated independently of capacity.

Assumptions (all listed in every evidence drawer): colour is simulated per finished-good lot from a seeded RAL mix and inherited by
its parts; a colour change on a coating work centre costs 40 minutes, same-colour reload 5 minutes, replacing the routing's batch
setup on those work centres; defects are drawn per unit (2 % unless a BOM line has a scrap_pct) and found by inspection; component
inspection 0.5 min/unit, rework 15 and re-inspection 1 min/unit; overtime is a Saturday window of 8 h paid at 1.5 times the work
centre's hourly cost; a purchased part with no stock or receipt before the lot is due is assumed available at release.

Policies, each compared with "today" on the same plant and seed:
- Colour grouping: at a coating work centre, among ready work, keep the colour on the line while a same-colour job is due within W days of the earliest-due job.
- Scrap handling (averaged over seeded runs): defects found at final inspection with the whole product scrapped and remade (today); found at final inspection with only the failed components replaced (remade, reworked, re-inspected); or found by inspecting each component, remaking only the bad units while the parent waits.
- Kit priority: a component whose parent lacks at most one component, with its other parts available and its release date passed, is served first.
- Overtime: a Saturday window always paid, or only when the queued work will be used (it feeds a later operation, or its consumer is missing nothing else by Monday). Idle hours are paid hours without work; output waiting more than three days for its consumer is counted as left waiting.

Scrapped units are priced at standard value: purchased material at standard cost plus routing hours at each work centre's hourly cost, plus
components. Calibration: simulated processing hours are about 0.9 of the period engine's required hours over the same weeks
(a regression test keeps them within 0.6-1.15); the difference is lot-by-lot netting and an empty shop at the start.

## order-change-impact

Stock points are places where work waits between operations. The named ones are after laser cutting and before painting (colour is
committed there); every other waiting place, and work in process, appears in the audit at the moment of the change. Everything is
reconstructed from the operation log of the simulated plan, so what is reported at a stock point (units through, wait, average and peak
stock, value) is what the simulation did. Value built into a lot = operation hours at the work centre's hourly cost, purchased material
issued at its first operation, and the finished components it consumed.

A forecast change (a factor applied to demand from a week onward, optionally for a family) is applied at a chosen day. Run A is the plan as
it stood; run B re-simulates the plant with the changed demand. At the change moment the unwanted share (1 minus the factor) of the work
that exists on the changed lots is read from run A. It is reusable up to the quantity other open work still needs (run B jobs not started at
that moment), per item, and after painting only in the same colour; the rest is stranded. Work running on a machine is counted as sunk and
not reusable. B shows the new load per process (hours), mean lead time, late lots and stock-point stock. B re-plans from the start, so it
shows the new steady state, not a re-sequencing of work already started.
