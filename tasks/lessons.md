# Lessons

## 2026-09-30 — Anything added after the bootstrap must be wired into the bootstrap

Migrations were added later (result tables, views, roles, a column) and tested only by tests that applied them themselves. The destructive DDL bootstrap and the loader never knew about them: a fresh install had no `data_version` column, and a reload of a migrated database failed on a foreign key from a migration-created table. Neither showed up in the suite, because the suite always started from a database it had already prepared.

**How to apply going forward:**
- When adding a versioned layer on top of a bootstrap, update the bootstrap (drop order, ledger reset) and the loader in the same change.
- Test the real fresh path: install from an empty stack by the README steps, then reload a populated database. A static test of drop order catches the FK class of bug without a database.
- Make the application fail with an actionable message when the schema is behind, not with a SQL error.
- Environment resets happen (Docker Desktop was reset mid-session): a documented, reproducible setup path is what makes recovery cheap; the regenerated data had the identical fingerprint.

## 2026-09-29 — Calibrating one domain without moving the rest

The execution history (operation timestamps, completed-order counts) was a generator artefact: whole-day splits of the order span gave recorded times of about 10× the routing standard, and 1–3 completed orders per item starved the policy heuristic. The fix followed the CP3.2 lesson literally. A new final generator step (`execution_history.py`) runs **after** DQ injection and snapshots, on its own spawned stream (`SeedSequence(seed, spawn_key=(2,))`). It only rewrites timestamps of fully COMPLETED orders and appends new completed orders, and it never touches WIP-feeding open orders.

The proof was measured, not argued: fingerprints of all 22 generated tables were taken before and after, only the two execution tables changed, and the five scenario backlogs stayed identical. The local database was refreshed with a guarded, non-destructive loader rather than the destructive DDL reload, so scenario and cost history survived.

**How to apply going forward:**
- Put a new synthetic domain last, on its own spawned stream.
- Fingerprint every table before and after the change.
- Refresh only the changed tables, with guards that refuse if anything else differs.

## 2026-09-28 — CP3.2: isolating RNG streams fixed the whack-a-mole calibration problem

CP3.1's lesson correctly diagnosed that changing an RNG call's *shape*
reshuffles everything downstream, but the fix applied there (just being
aware of it) still meant every capacity-calibration tweak cost a full
regenerate-and-remeasure cycle, and fixing one process's overload routinely
broke another's numbers that had nothing to do with it. The actual fix was
architectural, not procedural: give work-centre generation (shift pattern,
`cost_per_hour` jitter) its own independent RNG stream
(`np.random.SeedSequence(seed).spawn(2)`, not `default_rng(seed)` /
`default_rng(seed + K)` — spawning guarantees independence, offsetting a
seed does not), so calibration changes in `master_data.py` can no longer
touch demand/BOM/routing generation at all. One remaining trap found the
hard way: draw the shift pattern **once per process**, not once per work-
centre *instance* — round-robin work assignment already balances load
evenly across parallel lines, but independently re-randomizing each
instance's shift count was undoing that balance by giving "identical" lines
very different capacity for the same work.

Second finding: even with fully isolated RNG and round-robin assignment,
some genuine per-instance variance remains when few (2-3) parallel lines
split ~30-50 independently-random-demand items — this is real sampling
variance (small-N bin-packing luck), not a bug, and increasing the instance
count (more bins) is the correct lever, but pushed too far in one attempt
(a "safe-looking" capacity bump) it flipped the failure mode entirely
(baseline became so over-provisioned that a demand shock could no longer
push anything into a constraint) — calibration has two failure directions,
not one, and both need checking after any change, not just "did the number
I was fixing get better."

**How to apply going forward**: when a synthetic generator has multiple
independent "domains" (demand, capacity, cost, geometry, ...) that get
calibrated separately, give each domain its own spawned RNG stream from the
start, before any calibration work begins — retrofitting it mid-calibration
still requires one "transition" regenerate-and-remeasure cycle, but every
change after that is isolated and fast. Always remeasure the FULL picture
(not just the one number being tuned) after every calibration change, in
both directions (still too tight? now too loose?).

## 2026-09-28 — CP3.1 calibration process (user rejected "mechanically correct but not demo-credible")

The user accepted CP3's engine mechanics outright but rejected the checkpoint
for demo purposes: the baseline was "plant already broken everywhere" rather
than "healthy, then a shock creates a localized problem." Two lessons:

1. **A single global scaling factor cannot fix a per-process imbalance.**
   The first calibration attempt cut demand by one flat factor and measured
   one aggregate ratio. The real per-process ratios ranged from 0.0 (unused)
   to 10x (deburring) to 0.39x (welding, the one the demo story actually
   needs to be tight) — a flat multiplier moves the average without fixing
   the shape. Fix: measure and tune **per process type**, not once globally.
2. **Changing *how* a seeded RNG is called (not just its bounds) reshuffles
   every later draw, even in unrelated code.** Switching one `rng.choice(list)`
   call to `rng.choice(options, p=weights)` changed work-centre shift
   assignment for a different process entirely three iterations later, which
   looked like unrelated noise until traced back. Changing only a tuple's
   *values* passed into an unchanged call site (e.g. `PROCESS_TIME_DEFAULTS`
   bounds) does NOT reshuffle anything downstream — only a change in call
   pattern/shape does. When iterating on generator calibration, expect either
   "this and only this changes" (same call shape, different constant) or
   "everything downstream changes" (different call shape) and don't waste
   time hunting for a narrower explanation than that.
3. **Random assignment across parallel resources creates artificial
   lopsidedness that reads as a data bug, not a real bottleneck.** Two
   identical-capacity welding lines showed wildly different utilization
   purely from `rng.choice` clustering items onto one of them by chance.
   Round-robin assignment (deterministic, still seeded/reproducible) fixed
   it immediately and is more realistic besides — real plants load-balance
   parallel lines rather than randomly clustering work.

**How to apply going forward**: when a user says a checkpoint's *mechanics*
are right but the checkpoint isn't *demo-ready*, don't re-litigate the
mechanics — treat it as a distinct calibration/tuning task with its own
measure-adjust-remeasure loop, and report the actual per-unit (not
aggregate) numbers at each step so drift is visible immediately rather than
after several compounded changes.

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
