# CP1 — Foundation

- [x] Repo scaffold (backend/frontend/db/scripts dirs, .gitignore, .env.example)
- [x] Corrected DDL schema (db/ddl/001_schema.sql) — 3-tier capacity, material
      netting output tables, period_engine_results, forecast_consumption audit,
      intentionally-unenforced FK columns documented for DQ testing
- [x] Docker Compose (sqlserver + api + frontend)
- [x] Seeded synthetic ERP data generator (master data, 5-family multi-level
      BOM/routing, demand, execution, capacity/cost, state snapshots)
- [x] 13 categories of injected data-quality defects + manifest
- [x] CSV staging -> SQL Server loader (SQLAlchemy reflection, IDENTITY_INSERT)
- [x] Smoke test (row counts vs. target scale)
- [x] CP1 pytest suite (determinism, structural invariants, injected-DQ presence)
- [x] Minimal frontend scaffold, CORS fix, full docker compose up verified
- [x] git init + CP1 commit

## Next (CP2, per PLAN.md) — COMPLETE

- [x] Data Quality Engine: 20-rule registry (13 INJECTED, 7 ORGANIC), origin +
      manifest_key tagging, writes to dq_findings
- [x] BOM explosion engine (recursive, cycle detection, effective-dating,
      full lineage with source record IDs)
- [x] Material netting (CORR-4): gross requirement - usable inventory -
      scheduled receipts = net requirement / shortage, persisted to
      material_requirements under a baseline scenario_runs row
- [x] Routing analytical layer (folded into DQ rules: missing WC/times,
      invalid batch size/yield)
- [x] Unit tests: recursion, cycles, orphans, overlapping revisions,
      effective-date selection, scrap/yield propagation, netting exclusion/
      timing/shortage, DQ rule cross-check vs. manifest (29 tests total)

## Next (CP3, per PLAN.md) — COMPLETE

- [x] Forecast reconstruction (every revision preserved) + scoped consumption
      (CORR-3, non-double-counting proven) + accuracy metrics (WAPE headline,
      zero-demand-robust, horizon buckets)
- [x] Three-tier capacity (CORR-5) wired into a real engine, KPI_BLOCKING-aware
- [x] period_engine.py: stateful, period-stepped backlog/WIP (CORR-1/2) —
      unified continuous queue/backlog formula (replaces the original
      piecewise CORR-1 sketch; see PLAN.md deviation note)
- [x] Constraint classification (CORR-6): NONE/OVERLOADED/CANDIDATE/PRIMARY,
      demonstrated over time against both the synthetic mini-plant and real data
- [x] DQ impact scoping (GLOBAL/ENTITY/KPI_BLOCKING) so one bad routing/item
      doesn't invalidate unrelated products or work centres
- [x] Period-aware material netting, state carried across periods
- [x] Golden scenarios: CAB-100 +40% demand, welding +30% capacity intervention
- [x] 26 new tests (mini-plant mechanics + real-dataset golden scenarios),
      55 total passing

## CP3.1 — MRP correctness + plant calibration pass — COMPLETE

- [x] Cascading multi-level MRP netting (app/analytics/mrp.py): low-level
      coding + one-level-at-a-time explosion driven by NET (not gross)
      requirement; exact golden tests for the FRAME/80-inventory invariant
- [x] Plant recalibration: demand base_level halved again (5-55 -> 2-20),
      per-process work-centre multiplicity/shift-pattern retuned, round-robin
      (not random) work-centre assignment for parallel lines, welding's
      per-unit time/batch tightened — baseline now 9/20 work centres in the
      55-85% band, only 6/20 ever reach CANDIDATE/PRIMARY_CONSTRAINT and only
      from week 7+, not from week 1
- [x] CAB-100 +40% scenario now runs over 16 weeks with generic emergent-
      constraint detection (diffs baseline vs. scenario classifications —
      never assumes which work centre will be affected)
- [x] Smallest-recovering-capacity-intervention search (tries increasing
      multipliers, returns the first where backlog peaks then declines)
      applied from a dynamically-detected intervention week, not day one
- [x] Four intervention cases (BASELINE/BUFFER_ONLY/CAPACITY_ONLY/COMBINED):
      buffer = one-time inventory boost (proven not to touch effective_hours),
      capacity = time-scoped multiplier; results reported as computed, not
      forced toward any expected ordering
- [x] Historical (fully realized) forecast-revision example alongside the
      future/unrealized one
- [x] 7 new tests (MRP netting golden cases, intervention timing/buffer-vs-
      capacity distinction, historical-example selection), 71 total passing

## Next (CP4, per PLAN.md)

- [ ] Scenario engine: baseline/buffer-only/capacity-only/combined via
      period_engine.py, persisted under scenario_runs
- [ ] Buffer recommendation engine (constraint-aware per CORR-6)
- [ ] FastAPI analytics API with Evidence Drawer payloads (CORR-9)
- [ ] React + Plotly frontend, 9 dashboard pages
