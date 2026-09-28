"""Constraint classification [CORR-6]: NONE -> OVERLOADED -> CANDIDATE_CONSTRAINT
-> PRIMARY_CONSTRAINT, driven by a documented, mechanical rule so "why is
this the constraint" always traces to a rule, never a judgment call.

- OVERLOADED: utilization_pct > 1.0 in *this* period only — a one-off spike.
- CANDIDATE_CONSTRAINT: OVERLOADED in >= N consecutive periods (default 3,
  app.config.settings.candidate_constraint_min_consecutive_periods) — i.e. a
  *recurring* overload, not a blip [CP3 req. 10].
- PRIMARY_CONSTRAINT: exactly one work centre per period — the
  CANDIDATE_CONSTRAINT with the greatest cumulative backlog-hours summed
  over the horizon so far; ties broken by earliest position in the
  canonical process-flow order (app.synthetic.master_data.PROCESS_TYPES_IN_FLOW_ORDER).

A period with no reliable utilization data (NaN — e.g. a KPI_BLOCKING zero-
capacity-week finding) neither extends nor resets a work centre's overload
streak: it is simply excluded from that one period's classification,
consistent with KPI_BLOCKING's scope (CP3 req. 2).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.synthetic.master_data import PROCESS_TYPES_IN_FLOW_ORDER

NONE = "NONE"
OVERLOADED = "OVERLOADED"
CANDIDATE_CONSTRAINT = "CANDIDATE_CONSTRAINT"
PRIMARY_CONSTRAINT = "PRIMARY_CONSTRAINT"


@dataclass
class ConstraintState:
    consecutive_overload_periods: int = 0
    cumulative_backlog_hours: float = 0.0


def step_work_centre(
    utilization_pct: float,
    backlog_hours_end: float,
    state: ConstraintState,
    min_consecutive_periods: int,
) -> tuple[str, ConstraintState]:
    """One work centre, one period. Returns (classification, new_state)."""
    if utilization_pct is None or (isinstance(utilization_pct, float) and math.isnan(utilization_pct)):
        return NONE, state  # no reliable data this period; streak unaffected

    is_overloaded = utilization_pct > 1.0
    new_streak = state.consecutive_overload_periods + 1 if is_overloaded else 0
    new_cumulative = state.cumulative_backlog_hours + max(0.0, backlog_hours_end)
    new_state = ConstraintState(consecutive_overload_periods=new_streak, cumulative_backlog_hours=new_cumulative)

    if new_streak >= min_consecutive_periods:
        return CANDIDATE_CONSTRAINT, new_state
    if is_overloaded:
        return OVERLOADED, new_state
    return NONE, new_state


def _flow_position(process_type: str | None) -> int:
    if process_type in PROCESS_TYPES_IN_FLOW_ORDER:
        return PROCESS_TYPES_IN_FLOW_ORDER.index(process_type)
    return len(PROCESS_TYPES_IN_FLOW_ORDER) + 1


def select_primary_constraint(
    classifications_this_period: dict[int, str],
    states: dict[int, ConstraintState],
    process_type_by_work_centre: dict[int, str],
) -> int | None:
    """Among work centres classified CANDIDATE_CONSTRAINT this period,
    returns the one to upgrade to PRIMARY_CONSTRAINT (or None if there are
    no candidates this period)."""
    candidates = [wc for wc, cls in classifications_this_period.items() if cls == CANDIDATE_CONSTRAINT]
    if not candidates:
        return None
    max_backlog = max(states[wc].cumulative_backlog_hours for wc in candidates)
    tied = [wc for wc in candidates if states[wc].cumulative_backlog_hours == max_backlog]
    if len(tied) == 1:
        return tied[0]
    return min(tied, key=lambda wc: _flow_position(process_type_by_work_centre.get(wc)))
