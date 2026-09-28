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

## Known calibration gap (flagged for CP4/CP5, not an engine defect)

Plant-wide demand currently outstrips capacity at almost every work centre
within 2-3 weeks of the horizon (INSPECTION, a singleton line serving all 140
FG variants, is the plant's actual PRIMARY_CONSTRAINT — not welding). The
engine's cross-work-centre primary-constraint selection is verified correct;
the *inputs* need calibration (likely: more inspection/packaging capacity,
and/or a further demand reduction) before CP5's "plant currently appears
healthy" executive story premise holds plant-wide. Do this pass when building
the CP4/CP5 dashboard, where the baseline narrative can be tuned iteratively
against the live charts.

## Next (CP4, per PLAN.md)

- [ ] Scenario engine: baseline/buffer-only/capacity-only/combined via
      period_engine.py, persisted under scenario_runs
- [ ] Buffer recommendation engine (constraint-aware per CORR-6)
- [ ] FastAPI analytics API with Evidence Drawer payloads (CORR-9)
- [ ] React + Plotly frontend, 9 dashboard pages
