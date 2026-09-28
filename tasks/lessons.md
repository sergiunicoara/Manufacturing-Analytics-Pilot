# Lessons

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
