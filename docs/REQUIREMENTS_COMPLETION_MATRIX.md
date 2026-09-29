# Requirements completion matrix

Baseline review: 2026-09-29, working tree on `master` at `8894825` plus uncommitted film work (not modified by this review).
Fresh verification this session: `docker exec mfg_pilot_api python -m pytest -q` → **95 passed in 402.73 s** (2026-09-29). Checkpoint labels and earlier test counts in `tasks/todo.md` are history, not evidence.

**Sources.** Group A: `PLAN.md`. Group B: analytical corrections approved during the build (period-entry lead time, BOM-path lead time, 12-week presentation horizon). Group C: additional ERP-pilot requirements from `docs/IMPLEMENT_REMAINING_PROMPT.md`. The original job description is only available as a paste in a previous session transcript; it is summarised there and not quoted here.

**Status values.** `DONE` = implemented, with the verification shown · `PARTIAL` = some implemented, gap stated · `MISSING` = not implemented · `SUPERSEDED` = replaced by a later approved correction · `EXTERNAL` = needs client systems or approvals.

## A. PLAN.md requirements

| # | Requirement | Implementation | Verification | Status | Gap / limitation |
|---|---|---|---|---|---|
| A1 | Seeded synthetic ERP generator at target scale | `backend/app/synthetic/*` | `test_synthetic_data_generation.py`, `scripts/smoke_test.py` | DONE | Synthetic data only |
| A2 | Staging → SQL Server loader with preserved IDs | `backend/app/loader/load_staging_to_sql.py` | loader run + smoke test | DONE | `001_schema.sql` begins with `DROP TABLE` statements, so it is destructive when re-run (see C7) |
| A3 | Corrected schema (3-tier capacity, engine output tables) | `db/ddl/001_schema.sql`, `002_…sql` | load + tests | DONE | No migration versioning table |
| A4 | Mock `integrations/` Protocols: `ERPAdapter`, `BIExportAdapter`, `MESAdapter` | — | — | **MISSING** | Plan omission; carried into C6 |
| A5 | DQ rule registry, injected + organic, 3-way classification | `backend/app/dq/*` (20 rules) | `test_dq_rules.py`, planted→found cross-check | DONE | Explanation of counts by rule, origin, scope and coverage is missing (C9) |
| A6 | BOM explosion: effective dating, cycles, orphans, scrap | `analytics/bom.py` | `test_bom_explosion.py` | DONE | |
| A7 | Material netting (CORR-4), DQ-excluded inventory | `analytics/netting.py`, `mrp.py` | `test_netting.py`, `test_mrp_netting.py` | DONE | |
| A8 | Three-tier capacity (CORR-5) | `analytics/capacity.py` | `test_capacity.py` | DONE | |
| A9 | Piecewise queue formula (CORR-1) | superseded | — | SUPERSEDED | Replaced by the B1 continuous entry-backlog model; keep only the deviation note |
| A10 | Stateful period engine (CORR-2/7) | `analytics/period_engine.py` | `test_period_engine.py`, `test_reconciliation.py` | DONE | |
| A11 | WIP from engine; Little's Law check; ageing from `wip.stage_entered_at` | engine `wip_qty`; Little's Law in tests | `test_period_engine.py` | PARTIAL | **WIP ageing not implemented** |
| A12 | Forecast reconstruction, MAE/WAPE/bias/volatility per horizon | `analytics/forecast.py` | `test_forecast.py` | DONE | MAPE deliberately not a headline metric (documented) |
| A13 | Scoped consumption ±4 weeks (CORR-3), non-double-counting | `forecast.consume_forecast` | `test_forecast.py` | PARTIAL | **`forecast_consumption` audit table is never written**; nearest-bucket, no-spillover policy not stated in evidence (C9) |
| A14 | Constraint classification (CORR-6) with tie-break | `analytics/constraint.py` | `test_period_engine.py` | DONE | |
| A15 | Constraint-gated analytical buffer recommendation | `analytics/buffers.py` | `test_cp4_cp5.py` | DONE | Policy selection is not separated from sizing (C5) |
| A16 | Cost: unit cost, WIP carrying cost over the weekly series | — | — | **MISSING** | No `cost.py`; `standard_costs` and `cost_per_hour` loaded but unused (C4) |
| A17 | Scenario engine: 4 intervention types + persistence (CORR-7/8) | `scenario_demo.py`, `scenario_store.py`, `dashboard.py` | `test_interventions.py`, `test_golden_scenarios.py` | DONE | Reuse keyed on name + params + seed only (no data-version identity, C9) |
| A18 | Evidence object on every KPI (CORR-9) | `analytics/evidence.py`, `dashboard.py` | `test_cp4_cp5.py` | DONE | Units, time scope, exclusions and coverage are not explicit fields |
| A19 | Evidence Drawer on all 9 pages | `frontend/src/DashboardApp.tsx` | browser check (CP4) | DONE | |
| A20 | Copilot with insufficient-evidence gate (CORR-10) | `api.py:190` | `test_cp4_cp5.py` | DONE | No authorization gate for sensitive-data profile (C8) |
| A21 | Executive Story with live numbers | `/story`, `/api/executive-story` | `test_golden_scenarios.py` | DONE | |
| A22 | Reproducibility within tolerance (CORR-11) | tests | `test_synthetic_data_generation.py` | DONE | |
| A23 | SQL tests: referential integrity, effective-date overlap | DQ rules cover overlap | `test_dq_rules.py` | PARTIAL | No tests run against SQL Server itself |
| A24 | 10 docs + PILOT_FINDINGS from actual results | root `*.md` | review | PARTIAL | `PLAN.md` and `LIMITATIONS.md` still state the 2.5× welding translation (C9) |

## B. Approved analytical corrections

| # | Correction | Implementation | Verification | Status |
|---|---|---|---|---|
| B1 | Lead time from entry backlog ÷ effective h/workday → calendar days; lagged response | `analytics/leadtime.py`, `period_engine.py` | `test_period_engine.py::test_lead_time_uses_entry_backlog…`, `::test_capacity_changes_queue_not_intrinsic_processing_time` | DONE |
| B2 | FG route + longest manufactured BOM branch | `analytics/leadtime.py` | `test_period_engine.py::test_lead_time_follows_routed_subassembly…`, `test_golden_scenarios.py` | DONE |
| B3 | 12-week presentation horizon; baseline deterioration shown; 26-week engine | `dashboard.HORIZON`, `ASSUMPTIONS.md` | `test_golden_scenarios.py` | DONE |
| B4 | Five reference cases on one engine | `main.py` executive story | `test_golden_scenarios.py` | DONE |

## C. Additional ERP-pilot requirements

| # | Requirement | Status | Notes |
|---|---|---|---|
| C3 | Historical stage performance from actual operation timestamps | MISSING | Actual columns exist; no analytics read them |
| C4 | Cost and decision economics (unit, buffer capital, WIP carrying, intervention cost, 5-case deltas) | MISSING | See A16 |
| C5 | Planning-policy recommendation (MTS/MTO/ATO/insufficient) + decoupling candidates + versioned parameter export | MISSING | |
| C6 | `DATA_REQUEST.md` + executable completeness checker; M3 mapping (pending verification); adapters; `INTEGRATION.md` | MISSING | M3 table names must stay hypotheses until verified against vendor material or a client schema (EXTERNAL) |
| C7 | Versioned T-SQL read models with SQL/Python parity; restore runbook + disposable round trip | MISSING | |
| C8 | Secured profile: no default password, API auth, least-privilege SQL identities, `SECURITY.md`, LLM opt-in | MISSING | EU hosting and legal compliance are EXTERNAL |
| C9 | Reporting fixes: 2.5× wording, DQ explanation + full export, consumption policy evidence, data-version cache identity, orphan process inspection | PARTIAL | Orphan process inspected 2026-09-29 with `docker top`: the `run_cp3_2_report` run had exited by itself; only uvicorn remains, so no action was taken. The rest are missing; DQ findings are truncated at `dashboard.py:216` (`findings[:200]`) |
| C10 | UI/API wiring, end-to-end verification, updated docs | MISSING | |
| C11 | Film updated with new capabilities from real captures | MISSING | Needs TTS key + licensed music (EXTERNAL) |
