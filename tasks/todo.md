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

## CP3.2 — Calibration + flow-conservation pass — COMPLETE

- [x] Isolated capacity-related randomness onto its own `capacity_rng`
      stream (SeedSequence.spawn), decoupled from demand/BOM generation —
      shift-pattern/multiplicity tuning no longer reshuffles unrelated
      numbers (see tasks/lessons.md); shift pattern now drawn once per
      process (not per instance) so parallel lines share real capacity
- [x] Further per-process calibration: LASER_CUTTING/ASSEMBLY/BENDING/
      PACKAGING/POWDER_COATING multiplicity retuned, welding's per-unit time
      tightened again — baseline backlog is now genuinely self-correcting
      (peaks ~week 6, declines, not monotonic) instead of exploding to
      1,256h by week 12
- [x] WELDING now emerges as the natural CAB-100-shock constraint (baseline
      avg utilization 0.89, occasional single-week overloads, never
      persistent; CAB-100 +40% pushes it to PRIMARY_CONSTRAINT) — found
      generically via find_emergent_constraints, never hardcoded
- [x] Flow-conservation reconciliation (analytics/reconciliation.py): two
      identities proven true by construction and verified on real data —
      work-centre hours (required = completed + ending backlog) and
      material flow (gross = from_inventory + from_receipts + net) — both
      hold to discrepancy=0.000000 across baseline/shock/buffer/capacity/
      combined runs
- [x] System-level KPIs beyond single-WC backlog: completed units, ending
      WIP, ending backlog, overdue/unmet estimate, avg/P90/P95 lead time,
      Analytical Service-Risk Indicator (LOW/MEDIUM/HIGH)
- [x] Per-work-centre (not combined) recovery-multiplier search — replaces
      CP3.1's one-shared-multiplier approach that could under-recover an
      individual work centre
- [x] Explicit "where does buffer's improvement go" proof: an exact +40/-40
      unit swing between satisfied_from_inventory and net_requirement for
      the buffered item, with demand (gross) and capacity (effective_hours)
      both bit-identical to the no-buffer run
- [x] 13 new tests (reconciliation identities, buffer-cannot-create-capacity/
      destroy-demand, per-WC intervention sizing, system KPI service-risk
      classification), 84 total passing

## CP4 — COMPLETE

- [x] Reference baseline, shock, buffer, capacity, and combined scenarios
      persisted with parameters and seed; custom runs append results.
- [x] Constraint-gated analytical buffer recommendations with demand CV,
      realized forecast error, utilization, assumptions, and evidence.
- [x] Nine FastAPI page read models with evidence on metrics, chart points,
      and rows; SQL NULL preserves unknown KPI-blocked period results.
- [x] React and Plotly dashboard with all nine pages, scenario controls,
      responsive navigation, and reusable Evidence Drawer.

## CP5 — COMPLETE

- [x] Evidence-gated copilot with deterministic KPI, recommendation, DQ,
      and scenario tools; optional Anthropic prose and raw-evidence fallback.
- [x] Executive Story sequence and five-case Plotly lead-time chart with
      processing/queue/transfer decomposition and calendar translation.
- [x] Ten project docs plus `PILOT_FINDINGS.md` from live SQL-backed results,
      with Mermaid architecture and data-model diagrams.
- [x] Focused CP4/CP5 tests, full backend suite, frontend build, and live
      API/browser verification.

## ERP-pilot completion (docs/IMPLEMENT_REMAINING_PROMPT.md) — PLANNED 2026-09-29

Matrix: docs/REQUIREMENTS_COMPLETION_MATRIX.md. Fresh baseline: 95 tests pass.

### Stage 2 — Historical stage performance + DQ
- [x] analytics/stage_history.py: completed-op cohort, elapsed = actual_finish − actual_start;
      separate open / missing / invalid-order / duplicate / overlapping buckets; DQ exclusions;
      inter-op gaps labelled "elapsed gap", never "queue"; median/P85/P95, coverage, by item/WC/site/period
- [x] Page read model + UI panel "Recorded (synthetic timestamps)" next to "Projected"
- [x] DQ explanation read model (rule × origin × class × scope, unique entities, coverage) + paginated/CSV export
- [x] Write forecast_consumption audit rows; state nearest-bucket/no-spillover policy in evidence; tests
- [x] WIP ageing from wip.stage_entered_at
- [x] Fixed: write_findings dropped impact_scope/affected_entity_* (regression test added)
- Verified 2026-09-29: 29 targeted tests in container; live endpoints; browser (Stage Performance, drawer, records, Data Quality)
### Stage 3 — Cost, policy, export
- [x] analytics/cost.py: documented currency/valuation date; unit cost breakdown (no double count of rolled-up
      material); buffer capital delta; WIP carrying cost (only where item-level valuation is supported,
      else UNAVAILABLE + coverage); intervention cost from added *scheduled paid* hours × rate (ASSUMED rates);
      five-case summary with residual backlog. Persist with run_id. Copilot tool get_cost.
- [x] analytics/policy.py: MTS/MTO/ATO/INSUFFICIENT_EVIDENCE with configurable thresholds; decoupling candidates
- [x] Versioned parameter package export (CSV + JSON + JSON Schema), unmapped M3 fields marked; round-trip tests
- [x] Engine: additive inventory_end_by_period snapshot (material rows exist only in weeks with demand, so
      idle buffer stock was invisible to valuation); regression test proves physical results unchanged
- [x] db/migrations/ + app.db.migrate (rerunnable, ledger in schema_migrations); 003_cost_results.sql
- Verified 2026-09-29: cost/policy/export tests; five cases persisted to cost_results (runs 2–6); browser pages
- Finding: policy evidence insufficient for 127/140 FG at default thresholds (few orders / completed POs)
### Stage 4 — Integration, SQL, restore, security
- [x] DATA_REQUEST.md + request contract (YAML/JSON) + checker CLI (pass/warn/block)
- [x] M3_MAPPING.md — every M3 name marked HYPOTHESIS/PENDING unless verified
- [x] integrations/: ERPAdapter, BIExportAdapter, MESAdapter protocols + simulated file adapters + contract tests
- [x] INTEGRATION.md (Mermaid, ownership, cadence, late data, retry, incremental extraction)
- [x] db/ddl/100_read_models.sql: CREATE OR ALTER views (forecast accuracy, weekly load, stage elapsed);
      SQL/Python parity tests against the running SQL Server
- [x] scripts/restore/: VERIFYONLY / FILELISTONLY / RESTORE WITH MOVE, refuses existing DB, validated identifiers;
      round trip into a new disposable DB name; RUNBOOK
- [x] Secured profile: compose override without default password, localhost-bound ports, API key/role auth
      on mutations; SQL logins pilot_reader (views only) / pilot_app (scenario tables); least-privilege test;
      LLM disabled unless ALLOW_EXTERNAL_LLM=true; SECURITY.md
- [x] Data-version identity (hash of source table checksums + code version) in scenario_runs reuse key
- Verified 2026-09-29T06:18Z: full suite 182 passed / 0 skipped; restore round trip; secured instance; least privilege
- Note: read models live in db/migrations/100_read_models.sql; restore tool is app/db/restore.py (not scripts/restore/)
### Stage 5 — Verification + docs
- [x] Fix 2.5× wording (PLAN.md, LIMITATIONS.md); refresh PILOT_FINDINGS from live run
- [x] Full backend suite + `npm run build` + browser walkthrough; update matrix with commands/results
### Stage 6 — Film (OUT OF SCOPE per user 2026-09-29: "leave the presentation part")
- [ ] Update SCENES/SCRIPT/deck; capture real footage; captions; render 1080p/720p
      (BLOCKED externally: TTS key, licensed music)

Decisions taken (routine): SQL views are the single authority for the three BI metrics that
Python also computes; parity is asserted, not duplicated logic. No ERP write-back anywhere.

## Audit fixes — 2026-09-30 (docs/REQUIREMENTS_COMPLETION_MATRIX.md, section D)
- [x] Loader applies migrations; 001 drops cost_results and resets the ledger; schema guard message
- [x] Data version includes analytical settings; demo ports on 127.0.0.1
- [x] Tests for the loaders and refresh guards; REQUIRE_DB_TESTS strict mode
- [x] Docs brought up to date; legacy report scripts documented
- [x] Dependency pins raised after pip-audit; verified by the full suite and a clean audit
- [ ] Film (out of scope per the user); its files remain uncommitted
