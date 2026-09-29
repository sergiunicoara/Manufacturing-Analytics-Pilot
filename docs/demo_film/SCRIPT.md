# Demo film script

Generated from the speaker notes in `project/slides/*.html`. Edit the slides, then regenerate, so the two never drift.

Placeholders in `[brackets]` and `[__]` must be filled before recording. Scene 15 (lead time) depends on the unfinished lead-time fix.


---

## SCENE 1 · TITLE · 0:00–0:30

**ON SCREEN** This slide, full screen. Slow fade in.


**VOICEOVER**

Hi, I'm [name]. You are running an eight-week pilot to decide where WIP buffers belong, how large they should be, and which planning policy fits a sheet-metal and assembly plant. Before you choose who builds that model, I want to show you one I have already built. It runs end to end on ERP-shaped data: from raw extracts, through data-quality checks, to a buffer-versus-capacity decision. Every number you will see traces back to a rule, an input and a source record.


---

## SCENE 2 · SLIDE · 0:30–0:55

**ON SCREEN** This slide. Optional: highlight each row briefly as you name it.


**VOICEOVER**

Your brief lists seven things the pilot has to deliver. I've mapped each one to a scene in this film, so you can tick them off as we go: data setup and checks, the linked analytical layer, forecast revisions and accuracy, load and lead time, the data-gap register, the buffer decision, and the integration boundary. Before any of that, one slide on what exactly you are looking at.


---

## SCENE 3 · SLIDE · 0:55–1:30

**ON SCREEN** This slide. Let each card appear as you name it.


**VOICEOVER**

Three things, stated plainly. First, this is a synthetic plant. A seeded generator builds a sheet-metal and assembly operation with realistic structure, including thirteen kinds of deliberately planted data defects, so I can prove the checks catch them. Second, the M3, Qlik, Bright Analytics and MES connections are simulated interfaces, not live integrations. Third, and this matters most for a pilot like yours: every value is tagged as measured, derived or assumed. An assumption is never presented as a measurement.


---

## SCENE 4 · SLIDE · 1:30–2:05

**ON SCREEN** This slide. Optional cut-in: the repository tree in VS Code, backend/app/analytics expanded.


**VOICEOVER**

Here is the whole pipeline. ERP-shaped extracts land in SQL Server 2022, running in Docker with a T-SQL schema. A rule engine checks them and records every finding with a scope. Then a pure-Python planning core links everything by article, customer, site and week: BOM explosion, MRP netting, forecast reconstruction, three-tier capacity, and a period-stepped simulation that carries backlog from one week to the next. It is re-runnable: the same seed and the same parameters give the same results.


---

## SCENE 5 · SCREEN RECORDING · 2:05–2:50

**ON SCREEN** Show this title card for 3 seconds, then cut to the terminal.
1. Run: docker compose up -d (show the three containers starting: sqlserver, api, frontend).
2. Run: docker exec mfg_pilot_api python scripts/smoke_test.py
3. Scroll slowly through the row-count table; pause on the last line, "All row counts within expected range."
Optional: open SSMS or Azure Data Studio on localhost,1433 and expand the table list.


**VOICEOVER**

Scene five mirrors week one of your pilot. The client delivers M3 data; I stand it up in SQL Server and check it against the data request. I bring the stack up with Docker: SQL Server 2022 and the analytics service. The smoke test checks every table against the range the request implies: 979 items, BOMs seven levels deep, 1,904 routing operations across 30 work centres, 4,659 production orders, and forty weekly forecast revisions. With your real delivery, this step starts from a restore of the database backup, and the checks stay the same.


---

## SCENE 6 · SLIDE · 2:50–3:30

**ON SCREEN** This slide.


**VOICEOVER**

Your brief asks for every data gap to be documented and classed as tolerable, assumption-based or blocking. The rule engine does exactly that: 641 findings across thirteen defect types. The important design choice is scope. Blocking does not mean the whole analysis stops. A routing with missing times blocks that item's load, not the plant's. A week with zero capacity blocks that work centre's utilization for that week only. And because the defects were planted, I can prove detection: twelve of thirteen types match exactly. Missing cost records came back four higher than planted. I don't hide that; those four stay in the register for review.


---

## SCENE 7 · SCREEN RECORDING · 3:30–4:05

**ON SCREEN** Title card 3 seconds, then terminal.
1. Run: docker exec mfg_pilot_api python -m app.dq.run_dq_engine. Pause on "By classification" and on the "planted -> found" table.
2. Switch to SSMS or Azure Data Studio (localhost,1433, database mfg_analytics_pilot). Run the GROUP BY query on the slide.
3. Pick one row, for example rule_id = 'missing_routing_times'. Copy its record_id, open routing_operations and filter on it to show the empty time field.


**VOICEOVER**

Here is the register being built. One command runs all twenty rules and writes each finding to SQL Server, with its rule, its entity, the exact record, a severity, a class and an origin. At the end the run cross-checks against the list of planted defects. In SQL, this is an ordinary table your ERP director can query and challenge. Every finding points at a record: here is one routing operation with a missing run time, and here it is in the source table.


---

## SCENE 8 · SLIDE · 4:05–4:40

**ON SCREEN** This slide. Optional cut-in: tests/test_mrp_netting.py, the golden test for the 80-frames case.


**VOICEOVER**

A common mistake in this kind of model is exploding gross demand through the whole BOM, as if every level were always built from scratch. That overstates load at every work centre. Here, each level is netted first. A hundred parent units need a hundred frames; eighty are already on hand, so only twenty are built, and only twenty frames' worth of parts cascade further down. Shared parts collect demand from every parent before they are netted once. Revisions are effective-dated, and a cycle or an orphan component raises a finding. The model never quietly substitutes a zero.


---

## SCENE 9 · SLIDE · 4:40–5:25

**ON SCREEN** This slide. Optional cut-in: terminal running python -m app.analytics.run_cp3_report, sections 1 to 3.


**VOICEOVER**

Your brief asks for about six months of weekly forecast revisions and accuracy per horizon. Every revision is kept, so for any customer, item and delivery week I can show how the forecast moved as delivery approached. Consumption is scoped by customer, item, site and a four-week window. A forecast of 9.4 and firm orders of 9.0 plan 9.4 units, not 18.4, so demand is never counted twice. Accuracy is scored as weighted absolute percentage error, because a simple percentage error breaks on zero-demand weeks. The shape matters for the policy decision. Inside two weeks the error is about ten percent; beyond eight weeks it exceeds half of actual demand, and it runs high at every horizon. That tells you which forecast horizons a buffer should react to.


---

## SCENE 10 · SLIDE · 5:25–6:00

**ON SCREEN** This slide.


**VOICEOVER**

Load per work centre starts with honest capacity. Calendar hours come from shifts, hours and days. Planned downtime gives available hours; availability gives effective hours. All three tiers are stored, so when someone asks why utilization reads a hundred and thirty-eight percent, the answer is on screen, not in someone's head. Constraints follow a written rule. Above a hundred percent in one week is overloaded, nothing more. Three weeks in a row makes a candidate. One work centre per run becomes the primary constraint: the one with the largest cumulative backlog. A one-week spike never earns a standing buffer recommendation.


---

## SCENE 11 · SLIDE (or SCREEN) · 6:00–6:40

**ON SCREEN** This slide. Optional cut-in: terminal, python -m app.analytics.run_cp3_2_report, sections 4 and 5, the shock-vs-baseline weekly trace.


**VOICEOVER**

Now the question your pilot is really about: what happens when a customer's forecast changes? I raise demand for the CAB-100 cabinet family by forty percent and run the weekly simulation. The engine is not told which work centre to watch. It compares the shocked run with the baseline and reports what newly becomes a constraint. Here that is welding: required hours up nearly seventy percent, peak weekly utilization from 186 to 279 percent, and from mid-August WC-WELD-01 is the primary constraint. Notice the grey line too. The baseline itself is not perfectly flat; I show that rather than hide it.


---

## SCENE 12 · SLIDE · 6:40–7:25

**ON SCREEN** This slide. Optional cut-in: section 7 of run_cp3_2_report, the system-level comparison table.


**VOICEOVER**

Four responses to the same shock, each run through the same weekly engine. A buffer alone, forty units of standing stock on every item routed through welding, cuts the week-16 backlog from 341 hours to 239. It helps, but the backlog is still growing: the buffer buys time. Adding capacity at the constraint, sized as the smallest step that makes welding's backlog peak and then fall, brings it down to 15 hours. Buffer and capacity together drain it completely. This is the distinction the pilot has to make for the ERP director: where a buffer is the right tool, and where it only hides a capacity problem for a few weeks.


---

## SCENE 13 · SLIDE · 7:25–7:55

**ON SCREEN** This slide. Optional cut-in: section 8 and 9 of run_cp3_2_report, the reconciliation lines ending in holds=True.


**VOICEOVER**

A decision model is only trustworthy if nothing leaks. Two identities are checked on every run. For hours: what was required equals what was completed plus what is still waiting. For material: demand equals what came from stock, plus what came from receipts, plus what still has to be built. Both hold to six decimals in all five runs. And they explain the buffer result exactly. Forty more units came from stock, forty fewer had to be built, demand did not change, and capacity did not change.


---

## SCENE 14 · SLIDE · 7:55–8:35

**ON SCREEN** This slide.


**VOICEOVER**

"Two and a half times capacity" is not something a plant manager can act on, so here it is translated using the welding cell's own calendar. Today WC-WELD-01 runs one shift, eight hours, five days: forty calendar hours, about thirty-three effective. Two and a half times means roughly eighty-two effective hours a week. A second shift gets you to about two times, which the engine showed is not enough. Two shifts plus Saturdays gets close, at 2.4 times, but I have not run that case, so I won't claim it. Three shifts clears the tested threshold. The footnote is an assumption, and it is labelled as one: that a night shift keeps today's availability.

BEFORE RECORDING: if you run the 2 shifts × 6 days case (multiplier 2.42) through the engine, replace "Untested" with the actual result.


---

## SCENE 15 · SLIDE · 8:35–9:15

DO NOT RECORD YET. This scene depends on the lead-time correction, which is not in the code yet. The current CP3.2 report shows the same average lead time, 6.40 days, for SHOCK and CAPACITY_ONLY, because it walks only the finished good's own routing and misses welding. Record this scene only after that fix lands, and replace the [__ d] placeholders with the real figures.

**ON SCREEN** This slide, then optionally a cut-in of the lead-time time series for the five runs.


**VOICEOVER**

Your brief asks for lead times per production stage. Here they are not typed-in parameters; they come from the state of the plant. Each stage has three parts. Processing time comes from the routing: setup and run for the lot. Adding capacity never changes it. Queue time is the backlog already ahead of the order when it arrives, divided by the effective hours that work centre delivers per workday. Transfer time is the move between operations. And the path follows the BOM, so a cabinet order includes the wait for its welded frame. That is why adding welding capacity shortens cabinet lead time, from [__] to [__] days, and it does so gradually, as the backlog drains. It never rewrites weeks that have already happened.


---

## SCENE 16 · SCREEN RECORDING · 9:15–9:40

**ON SCREEN** Title card 3 seconds, then terminal.
1. Run: docker exec mfg_pilot_api python -m pytest -q. Let it finish and hold on the final "N passed" line. Speed the middle up 4x in editing.
2. Run: git log --oneline and hold on the checkpoint commits (CP1 to CP3.2 and beyond).
BEFORE RECORDING: rebuild the api image first (docker compose build api) so the container runs the current code, then fill [__] with the actual count. It was 84 at CP3.2.


**VOICEOVER**

Everything you've seen is covered by automated tests: BOM recursion and cycles, netting golden cases, forecast consumption that never double-counts, a queue formula with no jump at a hundred percent utilization, the constraint rules, flow conservation, and the proof that a buffer cannot create capacity. One command re-proves the whole model. It was built in checkpoints, each one ending with a green test suite and a commit, which is how I would run your pilot too.


---

## SCENE 17 · SLIDE · 9:40–10:15

**ON SCREEN** This slide. Optional cut-in: the integrations adapter interfaces in the repo, labelled SIMULATED.


**VOICEOVER**

The integration concept follows the pilot's rules: read-only access, and nothing productive changes. M3 data arrives from IBM i as a SQL Server backup and is restored in a secured EU environment, where the checks, the gap register and the planning model run. Out go curated extracts for Qlik Cloud and Bright Analytics, and planning parameters for the ERP as a reviewed proposal that the client applies. They never go in as a direct update. The MES is a boundary: WIP and actuals come in as snapshots, with no real-time claim. In this demo each connection is an adapter interface with a stub, clearly labelled as simulated.


---

## SCENE 18 · SLIDE · 10:15–10:45

**ON SCREEN** This slide.


**VOICEOVER**

Here is how I would propose to run the eight weeks, to be agreed with your ERP director. Weeks one and two: restore the delivery in the EU environment, check it against the data request, and publish the first gap register. Weeks two to four: the linked layer, plus forecast revisions and accuracy. Weeks four to six: lead times, load and the constraint map on your real calendars. Weeks six to eight: buffer positions and sizes, the policy comparison, and ERP parameter proposals. Because the engine already exists, the pilot's time goes to your data and your decisions, not to building tooling.


---

## SCENE 19 · CLOSE · 10:45–11:05

**ON SCREEN** This slide. Hold 3 seconds after the last word, then fade to black.


**VOICEOVER**

That's the model: ERP data in, every gap classified, forecasts rebuilt and scored, capacity and lead time derived from the plant's state, and a buffer-versus-capacity decision whose numbers add up. Every number traces to a rule, an input and a source record, and the assumptions are labelled as assumptions. I'd be glad to walk your ERP director through the code, or run it on a sample of your own extracts. Thank you.
