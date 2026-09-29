# Limitations

- The dataset is synthetic and calibrated for a demonstration; it does not validate a real plant's service level, cycle time, or capacity plan.
- Weekly rough-cut planning does not sequence individual orders across operations or model operator skills, maintenance, setup families, calendars within a week, or equipment downtime beyond aggregate availability.
- Demand-weighted lead time follows the longest manufactured BOM branch plus the finished-good route. It is a projection from period entry state, not an order-level completion promise.
- The 12-week Executive Story contains baseline deterioration. It provides a common comparison horizon, not a stable experimental control.
- Buffer recommendation exposure uses a seven-day planning assumption because item-specific replenishment time is absent. Its confidence flag is heuristic; validate placement and quantity with planners.
- The capacity schedule equivalent is calendar arithmetic on the recomputed 12-week multiplier (currently 1.10× for Welding 1: 40 → 44 scheduled hours/week). The 2.5× figure belongs to an earlier 16-week calibration and is not the current recommendation. Scheduled and effective hours are shown separately; parallel equipment, labour, breaks, maintenance, downtime and feasibility are unverified.
- The copilot explains deterministic evidence. Without an API key it returns the bundle directly; with a key it can still fail closed to raw evidence if the explanation service is unavailable.
- The first API analytics request is compute-heavy and caches results per process. This pilot has no job queue, pagination for all raw data, authentication, or production multi-user concurrency controls.
- Recorded stage times use synthetic timestamps at day resolution. Recorded elapsed time is calendar elapsed time, not productive processing time, and inter-operation gaps are not proven queue time. The recorded values are about ten times the routing standards, which shows the synthetic actuals are not calibrated to them.
- Cost figures use synthetic standard costs and ASSUMED rates (20 % annual carrying rate, added hours paid at the work centre's hourly rate). WIP carrying cost is unavailable because engine WIP mixes items per work centre. No ROI, profit or avoided-loss estimate is produced.
- The planning-policy heuristic returns INSUFFICIENT_EVIDENCE for most finished goods at default thresholds because the synthetic history has few orders and completed production orders per item. That is the intended honest outcome, not a defect to tune away.
- Every Infor M3 table name in docs/M3_MAPPING.md is an unverified hypothesis; no native M3 field is mapped, and the parameter package cannot be imported into M3.
- The secured profile uses service-level API keys, not named-user authorization. EU hosting, TLS, encryption at rest, retention and the data processing agreement are client infrastructure and legal steps that are not evidenced here.
- The engine's in-process cache is rebuilt on API restart. Runs are reused only when the source-data fingerprint and code hash match, but changing source data while the API is running still needs a restart.
