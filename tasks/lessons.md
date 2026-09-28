# Lessons

## 2026-09-28 — CP3 self-caught bugs and a design refinement

1. **Same root cause as CP2's NaT lesson, different symptom.** `capacity_calendar_df["week_start_date"] == pd.Timestamp(period_start_date)` is silently always False when the column holds plain `datetime.date` (in-memory generator context) rather than `pd.Timestamp` (DB-loaded) — `date == Timestamp` doesn't raise, it just never matches. This produced NaN capacity for *every* period in a test run against in-memory tables, which masked as "backlog never accumulates" rather than an obvious crash. Fix: normalize both sides via `pd.to_datetime(...).dt.date` before comparing, every time a date/Timestamp-typed column might come from either source. **Generalized rule**: any date/Timestamp comparison must normalize first — this is now the second time it's bitten a different function.
2. **`NaN` and `0.0` are different "this value is unusable" signals and conflating them causes a crash, not a wrong answer.** `effective_hours` can legitimately be a real `0.0` (a zero_capacity_weeks KPI_BLOCKING defect) without being `NaN` (a missing calendar row). Gating a division on `math.isnan(effective_hours)` alone let a real zero slip through to a `ZeroDivisionError`. Fix: gate on the *already-computed* unreliability signal (`utilization_pct` being NaN, which `compute_capacity` sets for both cases) rather than re-deriving reliability from a raw input field in a second place.
3. **A formula sketched at the design stage can turn out to be un-implementable as specified, and that's a legitimate mid-build discovery, not a failure to plan.** PLAN.md's CORR-1 called for a bounded curve below a utilization threshold and backlog accumulation above it. Implementing continuity verification (CP3 req. 8) showed the two formulas measure different things near the seam and cannot be made continuous by construction. Replaced with one unified always-stateful backlog formula, documented at length in period_engine.py's module docstring and reported as a deviation rather than silently diverging from the approved plan.

**How to apply going forward**: when a plan-approved formula has a threshold/seam between two different mathematical regimes, treat "prove continuity at the seam" as a task to do *during design*, not just when explicitly asked — if the two sides can't be shown continuous on paper, the seam is a bug waiting to be found, not a detail to fix later.

## 2026-09-28 — CP2 self-caught bugs (verification against real DB, not just fixtures)

Two bugs only surfaced when running against SQL-Server-loaded data, not
against the in-memory generator context or hand-built unit-test fixtures:

1. **`pandas.NaT` is not `None` and is not caught by
   `isinstance(v, float) and pd.isna(v)`.** A DDL `DATE NULL` column comes
   back from `pd.read_sql_table` as `NaT`, not `None`/`NaN`. Code that
   special-cased "value is None or (is a float and NaN)" silently worked on
   hand-built test fixtures (which used literal `None`) and crashed only
   against real SQL-sourced data. Fix: always use bare `pd.isna(value)`,
   which correctly handles `None`, `NaN`, and `NaT` in one call — never
   gate it behind an `isinstance` check.
2. **Sequential random data-quality injections can silently overwrite each
   other on the same row.** `inject_implausible_lead_times` sampled from
   *all* rows and could land on a row `inject_missing_routing_times` had
   already nulled, un-nulling it. Caught by a test asserting "every planted
   defect is still detectable," not by eyeballing row counts. Fix: each
   injection function should sample only from rows still eligible for *that
   specific* defect (e.g. "implausible" requires a present value to begin
   with) rather than the full unfiltered table.

**How to apply going forward**: (a) run new analytics code against the real
Docker-loaded database at least once before declaring a checkpoint done —
an in-memory-only or fixture-only test pass is not sufficient evidence when
the code's actual input comes from a SQL round-trip; (b) when a generator
injects multiple defect types via independent random sampling over the same
table, treat "could sample #2 already be part of injection #1's effect"
explicitly, since seeded-random sampling won't reliably avoid the same rows.

## 2026-09-28 — Plan-review corrections (round 2, before CP1)

The user approved the initial architecture but caught 11 issues, all in the
same family: **static/stateless formulas presented as if they were a real
plant simulation.** Pattern to avoid repeating in later checkpoints:

1. **Any formula with a denominator that can hit zero or go negative under
   normal (not edge-case) operating conditions is wrong**, not just
   incomplete. Utilization ≥ 100% is a normal, expected state for a
   bottlenecked work centre — not an edge case to ignore. Formulas must be
   checked at their boundary (here: utilization → 1) before being accepted,
   not just at typical values.
2. **"Weekly" or "period" analytics implies state carried between periods**,
   unless explicitly a snapshot metric. Backlog, WIP, and scenario
   propagation all needed to carry forward period-to-period; my first draft
   computed each period independently, which silently discards the
   accumulation that is the entire point of a capacity/WIP model.
3. **Don't collapse a multi-stage real-world quantity into one field** when
   the distinction matters downstream. Capacity is calendar → available →
   effective, not one "effective_hours" number — collapsing it hides exactly
   the input someone will ask to see in the Evidence Drawer.
4. **Every "shortage" or "requirement" claim needs netting against what's
   already on hand or inbound** — gross BOM explosion alone overstates need.
5. **A one-off overload is not the same claim as "this is the constraint."**
   Classification needs an explicit, documented rule (consecutive-period
   threshold, tie-break rule) so "why is X the bottleneck" traces to a rule,
   not a judgment call made once and forgotten.
6. **An AI copilot must refuse rather than infer** when the deterministic
   layer returns no matching evidence — an LLM asked a quantitative question
   will produce a plausible-sounding number even with no data behind it
   unless the calling code explicitly gates on evidence presence first.
7. **"Reproducible" for a floating-point simulation should mean tolerance-
   bounded equality, not byte-identical** — byte-identical is either
   trivially true (pure deterministic arithmetic) or a brittle test waiting
   to fail on an unrelated library version bump.

**How to apply going forward**: before presenting any calculation as final
in CP2–CP5, ask (a) what happens at the formula's boundary condition, (b)
does this need to carry state from the previous period, (c) am I collapsing
a multi-step real quantity into a single opaque number, (d) if an LLM will
ever see this result, what does it do when the result is empty.

See `[[architecture-corrections]]` in PLAN.md for the full CORR-1..11 list
these lessons came from.
