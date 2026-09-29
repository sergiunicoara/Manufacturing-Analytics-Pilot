# Manufacturing Analytics Pilot — Implementation Plan

## Context

The user wants a portfolio-grade simulation of an 8-week manufacturing analytics
pilot for a European sheet-metal/assembly plant — credible enough to show an
ERP Director, Manufacturing Director, or CTO. The working directory is empty
(no git repo, no code). This is a genuinely large build (13 phases in the
user's own spec, ~10 docs, full test suite, a custom analytics engine, and a
React dashboard) — it will span many implementation turns, not one shot. This
plan fixes the architecture, schema, formulas, and phase breakdown so
implementation can proceed without re-litigating design decisions.

Confirmed with the user:
- **Dashboard**: React + Plotly.
- **AI copilot**: full tool-calling scaffold, feature-flagged on
  `ANTHROPIC_API_KEY`. Present → Claude explains only, over structured
  evidence from deterministic tools. Absent → return the evidence bundle
  directly, no LLM call.

**Plan-approval round 2**: the user approved the overall shape but flagged 11
architectural corrections to the analytical engine, all now folded in below
(marked **[CORR-n]**). These corrections make the engine materially more
rigorous — a stateful, period-stepped simulation with proper MRP-style netting
and constraint classification, instead of a set of independent static
formulas. Nothing from the original spec is weakened; these are additions/
replacements of the specific mechanisms called out.

Local infra confirmed available: Docker Desktop 29.8 (daemon running), Docker
Compose v5.5.1, Python 3.11.9 host / will pin `python:3.12-slim` in containers,
Node 24.12 / npm 11.19, git 2.50.

## Flags: where the spec's own framing could mislead (resolved)

1. **"Infor M3-like ERP", IBM i, Qlik Cloud, Bright Analytics, MES** — none of
   these are real integrations. Mock adapters behind an `integrations/`
   interface layer, producing/consuming sample flat files in shapes that
   resemble each system's typical export. Labeled as such everywhere.
2. **DDMRP language** — heuristic informed by DDMRP concepts but not a
   certified implementation; never uses buffer-zone (red/yellow/green)
   terminology. Always "Analytical Buffer Recommendation."
3. **MAPE near zero** — WAPE is the primary forecast-accuracy metric; MAPE
   shown only with a floor guard and a footnote.
4. **[CORR-1] Queue time at/above 100% utilization** — the original
   `utilization/(1-utilization)` formula is unstable (blows up or goes
   negative at utilization ≥ 1). Replaced with a piecewise model (see
   Formulas below): a bounded delay curve below a utilization threshold, and
   explicit backlog-hours accumulation at/above it. Always `provenance =
   DERIVED`, never `MEASURED`.
5. **"Digital-twin-lite"** — relabeled "deterministic period-stepped
   simulation." No live MES feed, no real-time state.
6. **Cost model** — material costs MEASURED where `standard_costs` has a
   record; overhead %, carrying-cost rate, expedite/overtime multipliers are
   ASSUMED and labeled.
7. **"Production-ready" claims** — explicitly disclaimed in
   README/LIMITATIONS.
8. **Scale** — targeting ~3,000–6,000 production orders, ~120 finished/
   subassembly items, ~800–1,200 total item records: laptop-friendly, still
   populated-looking.
9. **[CORR-11] Reproducibility** — "byte-identical" replaced with
   "deterministic numerical equivalence within defined tolerance" for
   simulation outputs (floating-point results compared at relative tolerance
   1e-9, since the engine is deterministic arithmetic, not stochastic — exact
   equality is in fact achievable for pure Python/Decimal arithmetic on fixed
   inputs, but tests assert via tolerance rather than raw `==` to stay robust
   to library-level float formatting). Discrete artifacts (synthetic data
   generation: row counts, IDs, seeded random draws) are asserted via exact
   hash/checksum, since those are canonical fixtures, not floating-point
   simulation output.

## Architecture

```
Synthetic ERP data generator (Python, seeded)
        |
        v
ERP extract files (CSV/parquet, staging/ dir — deliberately dirty)
        |
        v
SQL Server 2022 (Docker) — loaded via SQLAlchemy Core + raw T-SQL DDL
        |
        +--> Data Quality Engine (rule registry -> dq_findings table)
        |
        +--> Analytics core library (pure Python, no I/O side effects):
        |      bom.py | netting.py | routing_capacity.py | leadtime.py |
        |      wip_backlog.py | forecast.py | constraint.py | buffers.py |
        |      cost.py | period_engine.py | scenario.py
        |
        +--> FastAPI service (analytics_api/) — every response includes an
        |      `evidence` block (see Evidence Drawer below), not just a value
        |
        +--> React + Plotly dashboard (frontend/) — Evidence Drawer component
        |      reused across every KPI/recommendation surface
        |
        +--> AI copilot endpoint (/copilot/ask) — tool-calling over the same
               analytics core; explicit insufficient-evidence path
```

**[CORR-2/7] Period-stepped core**: `period_engine.py` is the structural
change from round 1. Instead of independent per-week static calculations, the
engine steps through weekly periods over the planning horizon (e.g. 26 weeks),
carrying forward a `PeriodState` (WIP-by-stage, backlog-hours-by-work-centre,
inventory-by-item, open scheduled receipts) from week *t* to week *t+1*. Both
the baseline run and every scenario run execute this same stepped engine over
the full horizon — there is no more "static before/after snapshot"; every run
produces a full weekly time series, and a scenario's summary numbers are
derived from that series (e.g., peak utilization, week backlog first exceeds
a threshold), not from a single recomputation.

Mock integration boundaries unchanged: `integrations/` Protocols
(`ERPAdapter`, `BIExportAdapter`, `MESAdapter`), one stub implementation each,
docstring-labeled "SIMULATED — not a real integration."

Docker Compose services unchanged: `sqlserver`, `api`, `frontend`. No Redis —
not justified at this scale.

## Database schema (SQL Server)

Core master data: `sites`, `warehouses`, `customers`, `suppliers`, `items`
(`item_type`: FG/SUBASSY/RAW/PACKAGING), `work_centres`.

BOM/routing: `bom_headers`, `bom_components` (effective-dated, revisioned),
`routing_headers`, `routing_operations` (setup/run/queue/transfer times,
yield_pct, batch_size, work_centre FK).

Demand — **[CORR-3]** scoped by customer + item + site + delivery bucket:
- `forecast_versions` (snapshot_date, description)
- `customer_forecasts` (forecast_id, forecast_version_id FK, customer_id FK,
  item_id FK, **site_id FK**, delivery_period_start DATE, qty)
- `sales_orders` (..., **site_id FK**), `sales_order_lines`
- `forecast_consumption` (consumption_id, forecast_id FK, sales_order_line_id
  FK, consumed_qty, consumption_date) — audit trail for the Evidence Drawer;
  consumption logic only matches within a documented **consumption window**
  (config constant, default ±4 weeks around the delivery bucket), stored in
  `ASSUMPTIONS.md` and referenced from every consumption calculation's
  evidence block.

Execution: `production_orders`, `production_order_operations` (actuals per
op), `purchase_orders`, `purchase_order_lines` (**expected_receipt_date,
qty_ordered, qty_received, status** — feeds material netting as scheduled
receipts).

Capacity — **[CORR-5] three explicit tiers, not one collapsed number**:
`capacity_calendar` (work_centre_id, week_start_date,
**calendar_hours** = shifts × hours/shift × days/week,
**planned_downtime_hours**,
**availability_pct**,
**available_hours** = calendar_hours − planned_downtime_hours [stored, not
just derivable, so the Evidence Drawer can show it explicitly],
**effective_hours** = available_hours × availability_pct).

Cost: `standard_costs` (item × effective-dates, components tagged
MEASURED/ASSUMED).

State snapshots: `inventory`, `wip`.

Engine output tables — **[CORR-2/6/7] replacing the flatter round-1 design**:
- `material_requirements` (run_id FK, item_id, period_start_date,
  gross_requirement, usable_inventory, scheduled_receipts, net_requirement,
  shortage_flag, provenance) — **[CORR-4]** the netting result:
  `net_requirement = gross_requirement − usable_inventory −
  scheduled_receipts`, floored at 0, with `shortage_flag` when gross demand
  exceeds inventory + receipts within the required timeframe.
- `period_engine_results` (run_id FK, work_centre_id, period_start_date,
  calendar_hours, available_hours, effective_hours, required_hours,
  utilization_pct, backlog_hours_start, backlog_hours_end, wip_qty,
  queue_time_days, **constraint_classification** ENUM
  {NONE, OVERLOADED, CANDIDATE_CONSTRAINT, PRIMARY_CONSTRAINT}) — the core
  weekly time series per work centre per run (baseline is `run_id` with
  `is_baseline = true` in `scenario_runs`).
- `dq_findings`, `scenario_runs` (run_id, name, created_at, parameters_json,
  is_baseline, intervention_type ENUM {NONE, BUFFER_ONLY, CAPACITY_ONLY,
  COMBINED}), `buffer_recommendations`.

All tables: surrogate int PK + business key, `created_at`/`updated_at` where
mutable. Full DDL + ER diagram in `DATA_MODEL.md`.

## Analytical formulas

- **BOM explosion**: unchanged from round 1 — recursive walk of
  `bom_components` selecting the revision valid at a given effective date;
  `gross_requirement = net_requirement × quantity_per / (1 − scrap_pct)`.
  Cycle detection via DFS recursion-stack; missing/orphan components produce
  a DQ finding, never a silent zero.

- **[CORR-4] Material netting** (new stage, runs after BOM explosion, before
  routing load): for each item/period,
  `net_requirement = max(0, gross_requirement − usable_inventory −
  scheduled_receipts)`. `usable_inventory` excludes any inventory row already
  flagged BLOCKING/negative by the DQ engine (documented exclusion, not a
  silent drop — the exclusion itself is a DQ-linked evidence entry).
  `scheduled_receipts` = open `purchase_order_lines` with
  `expected_receipt_date` inside the period. `shortage_flag = true` when
  `net_requirement > 0` and no scheduled receipt covers it before the
  need-by date. Feeds a `material_requirements` row with full provenance.

- **[CORR-5] Capacity — three tiers**:
  `calendar_hours = shifts × hours_per_shift × days_per_week`
  `available_hours = calendar_hours − planned_downtime_hours`
  `effective_hours = available_hours × availability_pct`
  `required_hours = Σ_ops [ (setup_min × batches) + (run_min_per_unit ×
  qty/yield_pct) ] / 60`, `batches = ceil(qty / batch_size)`.
  `utilization_pct = required_hours / effective_hours`.
  All three capacity tiers are stored and surfaced in the Evidence Drawer —
  "why is utilization 138%?" must be answerable by showing calendar → available
  → effective → required, not just the final ratio.

- **[CORR-1/2] Queue time / backlog — piecewise, stateful**:
  Below a utilization threshold `U* = 0.95`:
  `queue_time_days = base_queue_time_days × (utilization_pct / (1 −
  utilization_pct))`, capped at a configured maximum (documented constant).
  At/above `U*`, switch to explicit backlog accumulation, carried period to
  period by `period_engine.py`:
  `backlog_hours_end(t) = max(0, backlog_hours_start(t) + required_hours(t) −
  effective_hours(t))`
  `backlog_hours_start(t+1) = backlog_hours_end(t)`
  `queue_time_days(t) = backlog_hours_end(t) / (effective_hours(t) /
  days_per_week)` — i.e., how many days of queue exist ahead of new work,
  expressed as days-of-backlog at current throughput. This never blows up or
  goes negative, and it is explicitly stateful across weeks rather than
  recomputed independently each period.

- **Lead time**: per operation, `processing_time` from setup+run for the
  order qty; `queue_time` from the piecewise model above; `transfer_time`
  from routing. Total lead time = sum across the operation sequence.
  Processing vs. waiting split always shown separately.

- **[CORR-2] WIP**: now a direct output of the stateful period engine
  (`wip_qty` in `period_engine_results`), driven by the same backlog
  accumulation logic (WIP grows when upstream release rate exceeds downstream
  effective capacity, carried period-to-period) rather than a separate
  static calculation. Cross-checked against Little's Law
  (`WIP ≈ throughput × lead time`) as a sanity constraint, not an independent
  derivation. Ageing from `wip.stage_entered_at` snapshots.

- **Forecast accuracy**: per (customer, item, site, horizon) bucket — MAE,
  WAPE (primary), bias, revision volatility. Unchanged from round 1 other than
  the added customer/site dimensions per CORR-3.

- **[CORR-3] Forecast consumption**: scoped by (customer_id, item_id,
  site_id, delivery_period). An order line consumes the matching forecast row
  only if the order's requested delivery date falls inside the documented
  **consumption window** around that forecast's delivery bucket (default ±4
  weeks, configurable, always cited in the evidence block).
  `remaining_forecast = max(0, forecast_qty − Σ consumed_qty)`; planning
  demand = firm orders + remaining unconsumed forecast. Every consumption
  event is logged to `forecast_consumption` for audit. Test asserts
  `forecast(1200) + orders(900) ≠ 2100` under consumption.

- **[CORR-6] Constraint classification** (new stage, runs on
  `period_engine_results` after each period): for each work centre/period,
  - `OVERLOADED`: `utilization_pct > 1.0` in that period only (a one-off spike
    is not yet a constraint).
  - `CANDIDATE_CONSTRAINT`: `OVERLOADED` in ≥ N consecutive periods (default
    N=3, configurable) — i.e., a *recurring* overload, not a blip.
  - `PRIMARY_CONSTRAINT`: exactly one work centre per run — the
    `CANDIDATE_CONSTRAINT` with the greatest cumulative backlog-hours summed
    over the horizon; ties broken by earliest position in the routing
    sequence (a constraint earlier in the flow blocks more downstream value).
  This selection rule is documented verbatim in `ANALYTICS_METHODS.md` so the
  "why is WC-WELD-01 THE constraint" answer is always traceable to a rule,
  not a judgment call.

- **Buffer heuristic**: unchanged shape from round 1 (`base = avg_daily_usage
  × avg_replenishment_time`, range scaled by demand CV / forecast error /
  utilization), output as `[min, max]` with `reason/inputs/confidence/
  assumptions` — but now `utilization` and `constraint_classification` inputs
  come from the period-engine time series, and a buffer is only recommended
  upstream of a `PRIMARY_CONSTRAINT` or `CANDIDATE_CONSTRAINT`, never an
  `OVERLOADED`-only work centre (a one-off spike doesn't justify a standing
  buffer recommendation).

- **Cost**: unchanged formula shape (`unit_cost = material_cost + Σ_ops
  (time × work_centre cost_per_hour) + overhead_pct`; WIP carrying cost =
  `WIP_value × annual_rate/365 × days`), now evaluated against the
  period-engine's weekly WIP/backlog series for carrying cost, not a static
  snapshot.

- **[CORR-7/8] Scenario engine**: `period_engine.py` runs the full stepped
  simulation over the horizon for: **BASELINE**, **BUFFER_ONLY** (buffer size
  changed, no capacity change), **CAPACITY_ONLY** (shifts/hours/availability
  changed, no buffer), **COMBINED**. All four comparisons preserved exactly
  as originally specified — this requirement is not touched by the
  corrections, only the underlying engine each of the four now runs on.
  Each run persisted in `scenario_runs` with full parameters for
  reproducibility (see CORR-11 above). Fixed seed threaded through; no hidden
  randomness.

## Data Quality Engine

Unchanged from round 1: rule-registry pattern, findings written to
`dq_findings` with full classification, deliberately-injected problems from
the seeded generator (full list per spec §7), TOLERABLE/ASSUMPTION_BASED/
BLOCKING classification.

## Evidence Drawer — **[CORR-9]**, new cross-cutting concept

Every KPI and recommendation returned by the FastAPI layer carries an
`evidence` object:
```
{
  "value": ...,
  "provenance": "MEASURED" | "DERIVED" | "ASSUMED",
  "formula": "<name/reference into ANALYTICS_METHODS.md>",
  "inputs": [ { "name": ..., "value": ..., "provenance": ..., "source_record_id": ... } ],
  "calculation_trace": [ "step 1 ...", "step 2 ...", ... ],
  "assumptions": [ ... ]
}
```
The React frontend implements one reusable `<EvidenceDrawer>` component
(slide-out panel) wired to every KPI card, chart tooltip, and recommendation
card across all 9 dashboard pages — not just the Recommendation/Decision
page. This directly implements spec §18 (Data Lineage/Explainability) as a
first-class, reused UI primitive rather than a one-off page.

## AI copilot — **[CORR-10]** insufficient-evidence gate

`/copilot/ask` — tool-calling loop, tools wrap the analytics core (`get_kpi`,
`explain_recommendation`, `get_dq_findings`, `run_scenario`). Before any
answer is generated (LLM or fallback), the endpoint checks whether the
deterministic tools actually returned matching evidence for the question's
entities (item/work centre/period/etc.). If a tool returns empty/no-match,
the endpoint short-circuits to a fixed response: *"Insufficient evidence to
answer — no matching data was returned by \[tool name\] for \[entities\]."*
The LLM is never invoked to paper over a data gap with a plausible-sounding
guess, and the LLM never computes a KPI itself. If `ANTHROPIC_API_KEY` is
set and evidence exists, Claude produces prose explanation strictly from tool
outputs; response always includes the raw evidence bundle. If unset, the
evidence bundle is returned directly with `llm_used: false`.

## Executive story mode

Unchanged: `/story` scripted sequence, every narration number read live from
computed scenario results (now the period-engine's actual weekly series),
never hardcoded text.

## Implementation phases / checkpoints

Each checkpoint ends with `pytest` run and a git commit (git init in CP1).

**CP1 — Foundation**: repo scaffold, `docker-compose.yml` (sqlserver + api +
frontend), SQL DDL (including the corrected schema above), SQLAlchemy models,
seeded synthetic-data generator at target scale, ERP extract → staging → SQL
Server loader.

**CP2 — Data quality + BOM/routing/netting**: DQ rule engine + injected
problems, recursive BOM explosion, **material netting (CORR-4)**, routing
analytical layer. Unit tests for recursion, cycles, effective-dating, netting
correctness (shortage detection).

**CP3 — Demand + capacity + flow (period-stepped)**: forecast reconstruction,
**scoped consumption with documented window (CORR-3)**, accuracy metrics;
**three-tier capacity (CORR-5)**; **`period_engine.py` stepped simulation with
stateful backlog/WIP (CORR-1/2)**; **constraint classification (CORR-6)**.
Golden-scenario tests over the full weekly series, not single-point checks.

**CP4 — Scenario/buffer engine + API + dashboard**: **scenario engine running
all 4 intervention types through the period-stepped core (CORR-7/8)**, buffer
recommendation engine (constraint-aware per CORR-6), FastAPI analytics API
with **Evidence Drawer payloads on every endpoint (CORR-9)**, React + Plotly
frontend with the reusable `<EvidenceDrawer>` component across all 9 pages.

**Executive Story demo calibration (CP4):** use a 12-week presentation horizon
to include the demand shock and the full intervention response while keeping the
story focused. The original 16-week calibration showed baseline lead time rising
from 5.7 to 13.0 days over weeks 13–16, inspection becoming persistently
constrained from week 12, and packaging briefly constrained in week 10. The
corrected BOM-path series now shows baseline deterioration within the 12-week
view too, so it is shown and labeled rather than presented as a stable control.
There is no horizon that both ends before baseline deterioration and includes
the intervention response; a genuinely stable-control story would require
recalibrating the underlying demand/capacity data. The analytical engine still
accepts the configured 26-week horizon.

**Lead-time method correction:** estimate an FG's route as its own routing plus
the longest manufactured subassembly branch in its effective-dated BOM. Keep
processing, queue, transfer, and total days separate. Processing uses the
routing standard (setup amortized over batch plus run time, scaled by BOM
quantity). Congestion queue uses the work-centre backlog at period entry divided
by effective hours per operating workday; convert that workday wait to calendar
days using the centre's operating days/week before adding it to routing processing
and transfer calendar days. Therefore an intervention cannot rewrite earlier
periods, and downstream welding affects finished-good lead time through its
subassembly path.

**Welding capacity translation:** express each multiplier against the actual
calendar. Work centre 9 is scheduled for 1 shift/day × 8 hours × 5 days = 40
scheduled hours/week. Scheduled hours are shown separately from effective hours:
at unchanged availability and efficiency, scheduled hours scale with the
multiplier. The multiplier is **recomputed for the current 12-week horizon** by
the per-work-centre recovery search (currently 1.10× for Welding 1, i.e. 44
scheduled hours/week, +4 h/week or +0.8 h per operating day; see
PILOT_FINDINGS.md). An earlier 16-week calibration selected 2.5× (100 scheduled
hours/week, +60 h/week); that figure is a historical result of a different
horizon and is not the current recommendation. Both are calendar arithmetic
only; staffing, equipment, maintenance, downtime and safety feasibility require
plant validation.

**CP5 — AI copilot + story mode + docs + polish**: copilot endpoint with
**insufficient-evidence gate (CORR-10)**, executive story mode, full test
suite pass (including **tolerance-based reproducibility per CORR-11**), all
10 docs + `PILOT_FINDINGS.md` generated from actual results, Mermaid
diagrams, final demo polish.

Progress updates posted after each checkpoint, not after every file.

## Testing strategy

- **Unit**: BOM recursion/cycle detection, **material netting (shortage
  detection, exclusion of DQ-flagged inventory)**, three-tier capacity math,
  **piecewise queue/backlog formula (continuity at U\*, non-negativity, no
  blow-up at utilization ≥ 1)**, **constraint classification rule
  (OVERLOADED → CANDIDATE → PRIMARY selection, tie-breaking)**, buffer formula
  bounds, forecast consumption non-double-counting within the documented
  window.
- **Integration**: full generator → SQL Server load → analytics API round
  trip inside Docker Compose; **period engine run over a full horizon with
  state correctly carried week-to-week (backlog/WIP continuity assertions)**.
- **Golden scenarios**: CAB-100 +40% demand → material requirements increase
  (net of inventory/receipts) → welding load↑ → welding becomes
  `CANDIDATE_CONSTRAINT` then `PRIMARY_CONSTRAINT` → WIP/backlog↑ over
  successive weeks → lead time↑. Then welding capacity +30% → utilization↓,
  backlog stops growing, lead time improves. Then the 4-way intervention
  comparison (baseline/buffer-only/capacity-only/combined) asserted against
  expected qualitative ordering (buffer-only ≠ capacity relief; combined best).
- **[CORR-11] Reproducibility**: same scenario params + seed → results equal
  within relative tolerance 1e-9 for computed floats; synthetic-data
  generation artifacts (row counts, generated IDs) asserted via exact
  checksum/hash.
- **SQL tests**: referential integrity, effective-date overlap detection.

## Verification

After CP1: `docker compose up -d sqlserver`, run loader, confirm row counts
match target scale via a smoke-test script.
After each subsequent checkpoint: `pytest -q` (full suite by CP5, targeted
files during iteration), then `docker compose up` end-to-end and hit
`/health`, `/kpi/plant-overview`, and (from CP4) load the dashboard in the
browser pane to confirm Plant Overview, Scenario Lab, and the Evidence
Drawer render against live data — not just that tests pass.
