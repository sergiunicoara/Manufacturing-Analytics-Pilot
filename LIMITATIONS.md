# Limitations

- The dataset is synthetic and calibrated for a demonstration; it does not validate a real plant's service level, cycle time, or capacity plan.
- Weekly rough-cut planning does not sequence individual orders across operations or model operator skills, maintenance, setup families, calendars within a week, or equipment downtime beyond aggregate availability.
- Demand-weighted lead time follows the longest manufactured BOM branch plus the finished-good route. It is a projection from period entry state, not an order-level completion promise.
- The 12-week Executive Story contains baseline deterioration. It provides a common comparison horizon, not a stable experimental control.
- Buffer recommendation exposure uses a seven-day planning assumption because item-specific replenishment time is absent. Its confidence flag is heuristic; validate placement and quantity with planners.
- The capacity schedule equivalent for a 2.5× welding target is based on calendar arithmetic. Parallel equipment, labor, breaks, maintenance, and feasibility are unverified.
- The copilot explains deterministic evidence. Without an API key it returns the bundle directly; with a key it can still fail closed to raw evidence if the explanation service is unavailable.
- The first API analytics request is compute-heavy and caches results per process. This pilot has no job queue, pagination for all raw data, authentication, or production multi-user concurrency controls.
