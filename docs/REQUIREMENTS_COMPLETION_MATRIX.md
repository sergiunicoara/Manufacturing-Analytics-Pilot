# Requirements completion matrix

Baseline review: 2026-09-29, working tree on `master` at `8894825` plus uncommitted film work (not modified by this review).
Baseline at review start: 95 passed. **Final verification (2026-09-30, commit `ca6f083`, nothing changed since): one clean full run in the container with `REQUIRE_DB_TESTS=1` and the login passwords exported → 209 passed, 0 skipped (17:20–17:33Z); `npm run build` and `npm audit` clean; browser pass: all 12 pages, the Executive Story (4 beats, 6 charts), the evidence drawer (units, coverage, SYNTHETIC label) and the unabridged DQ export (641 rows) all work; the real secured compose override was started and probed end to end (see docs/SECURITY.md).** Previous runs: 209 passed after the cold-start fix (the first attempt omitted the login passwords, so two tests failed for that reason only and were re-run), Executive Story 231 s to 1.4 s with identical numbers. Earlier verification (2026-09-30, after the audit fixes, on a freshly rebuilt Docker stack with the README's setup steps): full suite in the container with `REQUIRE_DB_TESTS=1` → 201 passed, 0 skipped, plus one new test whose path bug was fixed and re-run (11/11 in `test_loaders.py`); the same suite on the host with the raised dependency pins → 195 passed, 5 database tests skipped (no ODBC driver); `pip-audit` and `npm audit` report no known vulnerabilities; all 12 pages and the Executive Story return 200 and render; the five scenario backlogs are unchanged (74.4 / 212.2 / 93.1 / 164.4 / 77.3).** Earlier full runs: 189 passed (2026-09-29, after the history calibration), 182 passed (2026-09-29, before it).

**Sources.** Group A: `PLAN.md`. Group B: analytical corrections approved during the build (period-entry lead time, BOM-path lead time, 12-week presentation horizon). Group C: additional ERP-pilot requirements from `docs/IMPLEMENT_REMAINING_PROMPT.md`. The original job description is only available as a paste in a previous session transcript; it is summarised there and not quoted here.

**Status values.** `DONE` = implemented, with the verification shown · `PARTIAL` = some implemented, gap stated · `MISSING` = not implemented · `SUPERSEDED` = replaced by a later approved correction · `EXTERNAL` = needs client systems or approvals.

## A. PLAN.md requirements

| # | Requirement | Implementation | Verification | Status | Gap / limitation |
|---|---|---|---|---|---|
| A1 | Seeded synthetic ERP generator at target scale | `backend/app/synthetic/*` | `test_synthetic_data_generation.py`, `scripts/smoke_test.py` | DONE | Synthetic data only |
| A2 | Staging → SQL Server loader with preserved IDs | `backend/app/loader/load_staging_to_sql.py` | loader run + smoke test | DONE | `001_schema.sql` begins with `DROP TABLE` statements, so it is destructive when re-run (see C7) |
| A3 | Corrected schema (3-tier capacity, engine output tables) | `db/ddl/001_schema.sql`, `002_…sql` | load + tests | DONE | No migration versioning table |
| A4 | Mock `integrations/` Protocols: `ERPAdapter`, `BIExportAdapter`, `MESAdapter` | `backend/app/integrations/` (protocols + simulated file adapters) | `test_integrations.py` (5) | DONE | Simulated only |
| A5 | DQ rule registry, injected + organic, 3-way classification | `backend/app/dq/*` (20 rules) | `test_dq_rules.py`, planted→found cross-check | DONE | Explanation of counts by rule, origin, scope and coverage is missing (C9) |
| A6 | BOM explosion: effective dating, cycles, orphans, scrap | `analytics/bom.py` | `test_bom_explosion.py` | DONE | |
| A7 | Material netting (CORR-4), DQ-excluded inventory | `analytics/netting.py`, `mrp.py` | `test_netting.py`, `test_mrp_netting.py` | DONE | |
| A8 | Three-tier capacity (CORR-5) | `analytics/capacity.py` | `test_capacity.py` | DONE | |
| A9 | Piecewise queue formula (CORR-1) | superseded | — | SUPERSEDED | Replaced by the B1 continuous entry-backlog model; keep only the deviation note |
| A10 | Stateful period engine (CORR-2/7) | `analytics/period_engine.py` | `test_period_engine.py`, `test_reconciliation.py` | DONE | |
| A11 | WIP from engine; Little's Law check; ageing from `wip.stage_entered_at` | engine `wip_qty`; `stage_history.wip_ageing`; WIP & Lead Time page | `test_period_engine.py`, `test_stage_history.py` | DONE | Synthetic snapshots |
| A12 | Forecast reconstruction, MAE/WAPE/bias/volatility per horizon | `analytics/forecast.py` | `test_forecast.py` | DONE | MAPE deliberately not a headline metric (documented) |
| A13 | Scoped consumption ±4 weeks (CORR-3), non-double-counting | `forecast.consume_forecast`, `CONSUMPTION_POLICY`; audit written to `forecast_consumption` (4,213 rows live) | `test_forecast.py` (nearest bucket, no spillover, isolation) | DONE | |
| A14 | Constraint classification (CORR-6) with tie-break | `analytics/constraint.py` | `test_period_engine.py` | DONE | |
| A15 | Constraint-gated analytical buffer recommendation | `analytics/buffers.py` | `test_cp4_cp5.py` | DONE | Policy selection is not separated from sizing (C5) |
| A16 | Cost: unit cost, WIP carrying cost over the weekly series | `analytics/cost.py`, Decision Economics page, `cost_results` | `test_cost.py` (7), live five cases | DONE | WIP carrying cost reported UNAVAILABLE (mixed-item WIP); rates ASSUMED |
| A17 | Scenario engine: 4 intervention types + persistence (CORR-7/8) | `scenario_demo.py`, `scenario_store.py`, `dashboard.py` | `test_interventions.py`, `test_golden_scenarios.py`, `test_data_version.py` | DONE | Reuse now requires matching data version (runs 9–13 live) |
| A18 | Evidence object on every KPI (CORR-9) | `analytics/evidence.py` (+ units, time_scope, coverage, exclusions, data_origin on new results) | `test_cp4_cp5.py::test_every_page_supplies_evidence` | DONE | Older pages do not all carry the optional fields |
| A19 | Evidence Drawer on all pages (now 12) | `frontend/src/DashboardApp.tsx` | browser check (CP4) | DONE | |
| A20 | Copilot with insufficient-evidence gate (CORR-10) | `api.py:190` | `test_cp4_cp5.py` | DONE | No authorization gate for sensitive-data profile (C8) |
| A21 | Executive Story with live numbers | `/story`, `/api/executive-story` | `test_golden_scenarios.py` | DONE | |
| A22 | Reproducibility within tolerance (CORR-11) | tests | `test_synthetic_data_generation.py` | DONE | |
| A23 | SQL tests: referential integrity, effective-date overlap | DQ rules; completeness checker relationships; SQL views | `test_sql_read_models.py` (live), `test_completeness.py` | DONE | |
| A24 | 10 docs + PILOT_FINDINGS from actual results | root `*.md`, `docs/` | review | DONE | Scenario figures re-verified against the running API on 2026-09-29 |

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
| C3 | Historical stage performance from actual operation timestamps | DONE | `stage_history.py`, Stage Performance page + records endpoint, `vw_stage_elapsed*`; 5,408 valid observations, 95.5 % coverage; recorded ≈ 10× routing standard (synthetic) |
| C4 | Cost and decision economics | DONE | Unit cost, buffer capital, carrying, added-hours cost, five-case deltas, persisted; no ROI claimed |
| C5 | Planning policy + decoupling candidates + versioned parameter export | DONE | 127/140 FG INSUFFICIENT_EVIDENCE at default thresholds (honest outcome); all M3 fields UNMAPPED |
| C6 | DATA_REQUEST.md + checker; M3 mapping; adapters; INTEGRATION.md | DONE locally / EXTERNAL | Checker live: WARN 89/5/0. M3 names remain HYPOTHESIS until client schema or vendor docs (EXTERNAL) |
| C7 | T-SQL read models + parity; restore runbook + round trip | DONE | 6 views, parity passed; round trip into `pilot_restore_check_20260929`: 30 tables, 87,205 rows, 6 views, 40 FKs identical; overwrite refused |
| C8 | Secured profile, API auth, least privilege, SECURITY.md, LLM opt-in | DONE locally / EXTERNAL | Live secured instance verified (401/403/200); pilot_reader / pilot_app grants verified; EU hosting, TLS, TDE, DPA are EXTERNAL |
| C9 | Reporting fixes: 2.5× wording, DQ explanation + full export, consumption policy evidence, data-version cache identity, orphan process inspection | DONE | 2.5× reworded in PLAN/LIMITATIONS as a historical 16-week figure; DQ grouped with unique-record coverage and CSV export; consumption policy tested; run reuse keyed on data version; report process had exited on its own (`docker top`) |
| C10 | UI/API wiring, end-to-end verification, updated docs | DONE | See the final gate above. Docs updated: ANALYTICS_METHODS, API, LIMITATIONS, PILOT_FINDINGS, DEMO_GUIDE, PLAN |
| C11 | Film | OUT OF SCOPE | Excluded by the user on 2026-09-29; the uncommitted `docs/demo_film/` files were left untouched |

## D. Audit findings (2026-09-30) and their fixes

| # | Finding | Fix | Verification |
|---|---|---|---|
| D1 | A fresh install or a reload left the schema without the migrations, or failed on the `cost_results` foreign key | `001_schema.sql` drops `cost_results` and resets `schema_migrations`; the loader applies `db/migrations` after the DDL; the API returns an actionable "schema out of date" message | Fresh install from an empty stack by the README steps; destructive reload of a populated, migrated database succeeded; `tests/test_loaders.py` checks drop order statically |
| D2 | Data version ignored `config.py` constants | `cfg:` component hashes the analytical settings | `test_data_version.py` |
| D3 | Demo ports published on all interfaces | Bound to `127.0.0.1` | `docker ps` shows `127.0.0.1:` for 1433, 8000, 5173 |
| D4 | Docs stale (nine pages, migrations, cost tables, secured profile, resolution wording) | README, OPERATIONS, ARCHITECTURE, DATA_MODEL, API, ANALYTICS_METHODS, LIMITATIONS, SECURITY updated | Review |
| D5 | `refresh_execution` and the loader had no tests | Guard logic split into pure functions; `test_loaders.py` (11 tests) | Passing in repo and container layouts |
| D6 | Legacy checkpoint report scripts undocumented | Documented (all read-only except the netting script's own run) in OPERATIONS.md | Import check |
| D7 | Database tests could skip silently | `REQUIRE_DB_TESTS=1` turns a skip into a failure | Strict run: 0 skipped |
| D8 | Local leftovers | Disposable restore database and `.bak` removed; scenario runs kept as audit history; test logins kept for the live tests | Verified absent |
| D10 | Cold start: first page about 50 s, Executive Story about 231 s | `LeadTimeCalculator` (indexed routing/BOM lookups, shared across scenarios); background warm-up at start; `/health` reports progress | Story 231 s to 1.4 s; all five cases' weekly series and KPIs identical to the earlier values; `test_leadtime_equivalence.py` compares more than 1,000 (item, week, scenario) results against a frozen copy of the old code |
| D9 | Vulnerable pins (`starlette` via `fastapi`, `pytest`, `python-dotenv`) | Raised to `fastapi 0.142.2`, `starlette 1.7.0`, `pytest 9.0.3`, `python-dotenv 1.2.2` | `pip-audit` clean; full suite passes on the new pins |

## E. Code-review findings (2026-09-30) and their fixes

| # | Finding | Fix | Verification |
|---|---|---|---|
| E1 | Backup used `INIT` and could overwrite an existing `.bak` | Refuses an existing file (`sys.dm_os_file_exists`); `COPY_ONLY, NOINIT, CHECKSUM` | Live: backup onto an existing file refused, file intact; unit tests |
| E2 | `/docs`, `/redoc`, `/openapi.json` unauthenticated in the secured profile | Disabled when `APP_PROFILE=secured` | Live: 404 with and without a key; demo still 200 |
| E3 | Copilot DQ lookup matched bare record ids across entities | `finding_concerns_item` scopes by entity | Unit test |
| E4 | Table fingerprint depended on row order with ties | Per-row hashes, sorted | Unit test |
| E5 | `security_setup` truncated long passwords and could not rotate | `nvarchar(max)`, 128-char validation, create-or-rotate | Live: rotated; 129 characters rejected |
| E6 | RESTORE MOVE names collided for several files | Named by `FileId`; FILESTREAM/full-text refused | Live restore round trip (100,625 rows, no mismatches) |
| E7 | LLM payload minimisation was a deny-list | Allow-list `LLM_ALLOWED_KEYS` | Unit test |
| E8 | Report scripts rebuilt the lead-time index per call | One `LeadTimeCalculator` per table set | Existing equivalence tests |
| E9 | DQ findings recomputed six times per dashboard build | `findings_for(tables)` cache | Unit test |
| E10 | Buffer row crashed without a computed range | Blocked row, confidence `NONE` | Unit test |

Final strict run (`REQUIRE_DB_TESTS=1`, secured logins exported): 219 passed, 0 skipped.

## F. UI tests

`frontend`: `npm test` runs 66 Vitest tests (`DashboardApp.test.tsx`, `ExecutiveStoryVisuals.test.tsx`) against a mocked API and mocked Plotly. They cover loading, API errors, stale responses, all twelve pages, the evidence drawer, chart click-through, the scenario lab, stage-record filters and paging, the copilot and the executive story. Deliberately breaking three behaviours (stale-response guard, pager step, drawer reset on navigation) made the matching tests fail. Not covered: real Plotly rendering, styling and a real browser.

`npm run test:e2e` (Playwright, system Chrome, against the running stack; read-only) adds a 5-test browser smoke test: every page loads without console errors, a chart renders and a metric opens its evidence, the executive story, stage-record filtering, and the copilot. It found two real defects that are now fixed: a missing favicon (404 in every page load) and a sidebar that could not scroll, which hid the Executive Story link on screens shorter than about 780 px.

## G. Second review round (2026-09-30) and its fixes

| # | Finding | Fix | Verification |
|---|---|---|---|
| G1 | A receipt in a zero-demand week disappeared | Receipts for items not netted that week join the carried inventory | `test_review_fixes_2.py`; the five reference backlogs are unchanged (golden tests) |
| G2 | Receipts after the horizon were credited to the final week | Receipts dated a week or more past the last period are dropped | Unit tests |
| G3 | Missing capacity data gave an optimistic finite lead time | A centre with no usable capacity period makes the route unavailable (`None`) | Unit tests |
| G4 | Capacity load ignored routing effective dates | `compute_required_hours_by_work_centre(..., period_start_date)` uses the revision effective that week | Unit test |
| G5 | Policy mixed expired and current BOM revisions | Only the revision effective on the latest order date counts | Unit test |
| G6 | A cyclic BOM got a confident policy | INSUFFICIENT_EVIDENCE with a cycle blocker | Unit tests |
| G7 | Secured bootstrap could not run on a fresh instance | `security_setup` takes `MSSQL_ADMIN_USER/PASSWORD` explicitly; docs run it before switching the API to the secured profile | Unit test; docs rewritten |
| G8 | A BLOCK completeness report exited 0; the destructive loader never checked | CLI exits 1 on BLOCK; the loader runs the check first and aborts before dropping anything (`--skip-completeness` to override) | Unit tests; synthetic data gives WARN, so the demo load is unaffected |
| G9 | BI export could pair new data with an old sidecar | Both files are prepared as temporary files and swapped in only afterwards | Unit test. Only the two final renames remain as a window |
| G10 | Executive Story crashed on a null lead time | Null shows "n/a" | UI test |
| G11 | Stage-record filters could show stale rows after a failed reload | The table is cleared when the reload fails | UI test |
| G12 | Rebuilding the film removed the burned-in captions | `finish_movie.py` burns the SRT into the canonical MP4s and keeps the selectable encodes; `build_film.py` writes `*_draft_*` files, never the canonical names | Burn-in command tested on a short clip; the full film was not rebuilt. The film stays uncommitted and outside the requirements |

Full run after these fixes: backend 233 passed (strict, none skipped), UI 65, browser 5.

## H. Third round (2026-10-01): known gaps closed

| # | Gap | Fix | Verification |
|---|---|---|---|
| H1 | Blocked stock was overwritten by that week's receipts | Blocked stock is carried forward untouched (still unusable) | `test_review_fixes_3.py`; in the reference cases inventory rises by about 1,290 units from 2026-07-27, and backlogs are unchanged |
| H2 | Past-due open receipts were dropped | Available from the first modelled week (standard MRP treatment) | Unit tests; one real line (item 44, 337 units) now covers its week-1 need of 11.82; backlogs unchanged |
| H3 | Overlapping routing revisions: capacity took the latest, lead time said unavailable | Capacity reports their operations as excluded; both now say "unknown" | Unit test (no overlaps in the synthetic data) |
| H4 | BI CSV and sidecar could not be swapped atomically | The sidecar records the CSV's SHA-256; `FileBIExportAdapter.verify(name)` detects any mismatch, including a crash between the two renames | Unit tests |
| H5 | The five-case build took about 40 s | Engine tables indexed once per run (`BomExploder`, `RoutingLoad`, `CapacityCalendar`, scalar netting) | 39.4 s to 3.6 s; exact comparison of every row of the five cases; helper equivalence tests; warm-up 11.9 s |
| H6 | Charts redrew whenever the evidence drawer opened | Stable evidence handler (`useCallback`) | UI test |

Full run: backend 242 passed (strict, none skipped), UI 66, browser 5. The five reference backlogs are unchanged: 74.4 / 212.2 / 93.1 / 164.4 / 77.3.

