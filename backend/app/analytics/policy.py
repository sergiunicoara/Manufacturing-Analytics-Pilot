"""Explainable planning-policy heuristic and decoupling-location candidates.

Policy selection (where to hold stock) is separate from buffer sizing (how
much), which stays in buffers.py as the Analytical Buffer Recommendation.
This is a transparent heuristic informed by decoupling-point ideas; it is not
a DDMRP implementation and uses no buffer-zone terminology.

Core comparison, per finished good:
  customer tolerance  = median(requested_ship_date − order_date) of its orders   (MEASURED)
  own-route time      = median recorded elapsed of its completed production orders (MEASURED)
  cumulative time     = own-route + longest manufactured-component branch        (DERIVED)

  cumulative ≤ tolerance                      → MAKE_TO_ORDER
  own-route ≤ tolerance < cumulative, and a
  manufactured component exists               → ASSEMBLE_TO_ORDER (stock the components)
  own-route > tolerance                       → MAKE_TO_STOCK
  any required input missing                  → INSUFFICIENT_EVIDENCE

Demand variability, intermittency and forecast error do not change the
policy; they set confidence and are listed as reasons.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

MTO, ATO, MTS, INSUFFICIENT = "MAKE_TO_ORDER", "ASSEMBLE_TO_ORDER", "MAKE_TO_STOCK", "INSUFFICIENT_EVIDENCE"
PURCHASED = frozenset({"RAW", "PACKAGING"})


@dataclass(frozen=True)
class PolicyThresholds:
    min_order_count: int = 5              # orders needed to measure customer tolerance
    min_completed_production_orders: int = 3
    min_demand_weeks: int = 8             # weeks of order history needed for variability
    stable_cv_max: float = 0.5            # weekly demand CV at or below → stable
    intermittency_max: float = 0.5        # share of zero-demand weeks at or below → regular
    wape_max: float = 0.5                 # realized forecast WAPE at or below → forecastable
    min_common_parents: int = 2           # component used by ≥ N finished goods → decoupling candidate

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class PolicyRecommendation:
    item_id: int
    item_code: str
    policy: str
    confidence: str
    customer_tolerance_days: float | None
    own_route_days: float | None
    cumulative_days: float | None
    demand_cv: float | None
    intermittency: float | None
    forecast_wape: float | None
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)


def customer_tolerance_days(sales_orders: pd.DataFrame, sales_order_lines: pd.DataFrame) -> pd.DataFrame:
    lines = sales_order_lines.merge(sales_orders[["sales_order_id", "order_date"]], on="sales_order_id")
    days = (pd.to_datetime(lines["requested_ship_date"]) - pd.to_datetime(lines["order_date"])).dt.days
    lines = lines.assign(tolerance_days=days).loc[days >= 0]
    return lines.groupby("item_id").agg(tolerance_days=("tolerance_days", "median"),
                                        order_count=("tolerance_days", "size")).reset_index()


def production_elapsed_days(production_orders: pd.DataFrame) -> pd.DataFrame:
    done = production_orders.loc[(production_orders["status"] == "COMPLETED")
                                 & production_orders["actual_start"].notna() & production_orders["actual_finish"].notna()]
    days = (pd.to_datetime(done["actual_finish"]) - pd.to_datetime(done["actual_start"])).dt.days
    done = done.assign(elapsed_days=days).loc[days >= 0]
    return done.groupby("item_id").agg(own_route_days=("elapsed_days", "median"),
                                       completed_orders=("elapsed_days", "size")).reset_index()


def weekly_demand_stats(sales_orders: pd.DataFrame, sales_order_lines: pd.DataFrame) -> pd.DataFrame:
    lines = sales_order_lines.merge(sales_orders[["sales_order_id", "order_date"]], on="sales_order_id")
    order_date = pd.to_datetime(lines["order_date"])
    lines = lines.assign(week=(order_date - pd.to_timedelta(order_date.dt.weekday, unit="D")).dt.normalize())
    weeks = pd.date_range(lines["week"].min(), lines["week"].max(), freq="7D")
    rows = []
    for item_id, group in lines.groupby("item_id"):
        weekly = group.groupby("week")["qty"].sum().reindex(weeks, fill_value=0.0)
        mean = weekly.mean()
        rows.append({"item_id": item_id, "demand_weeks": len(weeks),
                     "demand_cv": float(weekly.std(ddof=0) / mean) if mean > 0 else None,
                     "intermittency": float((weekly == 0).mean())})
    return pd.DataFrame(rows)


def current_as_of(sales_orders: pd.DataFrame) -> dt.date:
    """The evaluation date for 'current' structure: the latest order in the data."""
    return pd.to_datetime(sales_orders["order_date"]).max().date()


def effective_bom_headers(bom_headers: pd.DataFrame, as_of: dt.date | None) -> pd.DataFrame:
    """The BOM headers whose window covers `as_of` (all of them when no date or no effective dates)."""
    if as_of is None or "effective_from" not in bom_headers.columns:
        return bom_headers

    def to_date(value):
        return None if pd.isna(value) else pd.Timestamp(value).date()

    ends = bom_headers["effective_to"].map(to_date) if "effective_to" in bom_headers.columns else [None] * len(bom_headers)
    window = list(zip(bom_headers["effective_from"].map(to_date), ends))
    return bom_headers.loc[[(s is not None and s <= as_of) and (e is None or e >= as_of) for s, e in window]]


def children_by_parent(bom_headers: pd.DataFrame, bom_components: pd.DataFrame,
                       as_of: dt.date | None = None) -> dict[int, set[int]]:
    """Component sets per parent. With `as_of`, only the BOM revision effective on that date counts, so an
    expired revision's components are not mixed with the current one's."""
    bom_headers = effective_bom_headers(bom_headers, as_of)
    parent = bom_headers.set_index("bom_id")["parent_item_id"]
    comps = bom_components.assign(parent_item_id=bom_components["bom_id"].map(parent)).dropna(subset=["parent_item_id"])
    return {int(p): set(g["component_item_id"].astype(int)) for p, g in comps.groupby("parent_item_id")}


def reaches_cycle(item_id: int, children: dict[int, set[int]]) -> bool:
    """True when the structure below `item_id` contains a cycle (an item that is, directly or not, its own component)."""
    done: set[int] = set()

    def visit(node: int, path: frozenset) -> bool:
        if node in path:
            return True
        if node in done:
            return False
        if any(visit(child, path | {node}) for child in children.get(node, ())):
            return True
        done.add(node)
        return False

    return visit(item_id, frozenset())


def _longest_branch_days(item_id: int, children: dict[int, set[int]], item_type: dict[int, str],
                         own_days: dict[int, float], seen: frozenset = frozenset()) -> float | None:
    """Longest recorded chain below an item through manufactured components; None when any is unmeasured."""
    longest = 0.0
    for child in children.get(item_id, ()):
        if item_type.get(child) in PURCHASED or child in seen:
            continue
        if child not in own_days:
            return None
        below = _longest_branch_days(child, children, item_type, own_days, seen | {item_id})
        if below is None:
            return None
        longest = max(longest, own_days[child] + below)
    return longest


def recommend_policies(tables: dict[str, pd.DataFrame], forecast_wape_by_item: dict[int, float],
                       thresholds: PolicyThresholds = PolicyThresholds()) -> list[PolicyRecommendation]:
    items = tables["items"]
    item_type = items.set_index("item_id")["item_type"].to_dict()
    codes = items.set_index("item_id")["item_code"].to_dict()
    tolerance = customer_tolerance_days(tables["sales_orders"], tables["sales_order_lines"]).set_index("item_id")
    production = production_elapsed_days(tables["production_orders"]).set_index("item_id")
    demand = weekly_demand_stats(tables["sales_orders"], tables["sales_order_lines"]).set_index("item_id")
    as_of = current_as_of(tables["sales_orders"])
    children = children_by_parent(tables["bom_headers"], tables["bom_components"], as_of)
    has_bom = set(tables["bom_headers"]["parent_item_id"].dropna().astype(int))
    has_current_bom = set(effective_bom_headers(tables["bom_headers"], as_of)["parent_item_id"].dropna().astype(int))
    own_days = {int(i): float(r["own_route_days"]) for i, r in production.iterrows()
                if r["completed_orders"] >= thresholds.min_completed_production_orders}

    out = []
    for item_id in items.loc[items["item_type"] == "FG", "item_id"].astype(int):
        blockers, reasons = [], []
        if reaches_cycle(item_id, children):
            blockers.append("The bill of materials contains a cycle, so the cumulative time cannot be measured.")
        if item_id in has_bom and item_id not in has_current_bom:
            blockers.append(f"No BOM revision is effective on {as_of} (the latest order date), so the component "
                            "chain and its cumulative time are unknown.")
        tol = tolerance.loc[item_id] if item_id in tolerance.index else None
        if tol is None or tol["order_count"] < thresholds.min_order_count:
            blockers.append(f"Fewer than {thresholds.min_order_count} sales order lines to measure customer tolerance.")
        if item_id not in own_days:
            blockers.append(f"Fewer than {thresholds.min_completed_production_orders} completed production orders "
                            "with recorded start and finish.")
        manufactured_children = [c for c in children.get(item_id, ()) if item_type.get(c) not in PURCHASED]
        branch = _longest_branch_days(item_id, children, item_type, own_days) if item_id in own_days else None
        if item_id in own_days and branch is None and not reaches_cycle(item_id, children):
            blockers.append("A manufactured component has too few recorded production orders to measure its time.")
        d = demand.loc[item_id] if item_id in demand.index else None
        cv = None if d is None or pd.isna(d["demand_cv"]) else float(d["demand_cv"])
        intermittency = None if d is None else float(d["intermittency"])
        wape = forecast_wape_by_item.get(item_id)
        tol_days = None if tol is None else float(tol["tolerance_days"])
        own = own_days.get(item_id)
        cumulative = None if own is None or branch is None else own + branch

        if blockers:
            out.append(PolicyRecommendation(item_id, codes.get(item_id, str(item_id)), INSUFFICIENT, "NONE", tol_days,
                                            own, cumulative, cv, intermittency, wape, [], blockers,
                                            ["Collect the missing history, then re-run."]))
            continue
        if cumulative <= tol_days:
            policy = MTO
            reasons.append(f"Cumulative recorded time {cumulative:.1f} d ≤ customer tolerance {tol_days:.1f} d: "
                           "the whole chain can start after the order.")
            alternatives = [f"{ATO} if tolerance shrinks below {cumulative:.1f} d."]
        elif own <= tol_days and manufactured_children:
            policy = ATO
            reasons.append(f"Own route {own:.1f} d ≤ tolerance {tol_days:.1f} d < cumulative {cumulative:.1f} d: "
                           "decouple at the manufactured components and assemble to order.")
            alternatives = [f"{MTS} if own-route time exceeds tolerance.", f"{MTO} if tolerance reaches {cumulative:.1f} d."]
        else:
            policy = MTS
            reasons.append(f"Own route {own:.1f} d > customer tolerance {tol_days:.1f} d: finished stock is needed "
                           "to meet the requested dates.")
            alternatives = [f"{ATO} if own-route time can be cut below {tol_days:.1f} d."]

        issues = []
        if d is None or d["demand_weeks"] < thresholds.min_demand_weeks:
            issues.append("short demand history")
        if cv is None:
            issues.append("demand variability unknown")
        elif cv > thresholds.stable_cv_max:
            issues.append(f"volatile weekly demand (CV {cv:.2f} > {thresholds.stable_cv_max})")
        if intermittency is not None and intermittency > thresholds.intermittency_max:
            issues.append(f"intermittent demand ({intermittency:.0%} zero weeks)")
        if wape is None:
            issues.append("no realized forecast error for this item")
        elif wape > thresholds.wape_max:
            issues.append(f"high forecast error (WAPE {wape:.0%})")
        if policy == MTS and cv is not None and cv > thresholds.stable_cv_max:
            alternatives.append(f"{MTO} with a longer quoted lead time, because stock of a volatile item carries "
                                "obsolescence risk.")
        confidence = "HIGH" if not issues else "MEDIUM" if len(issues) == 1 else "LOW"
        reasons += [f"Confidence reduced: {issue}." for issue in issues]
        out.append(PolicyRecommendation(item_id, codes.get(item_id, str(item_id)), policy, confidence, tol_days, own,
                                        cumulative, cv, intermittency, wape, reasons, [], alternatives))
    return out


def decoupling_candidates(tables: dict[str, pd.DataFrame], constrained_work_centres: set[int],
                          thresholds: PolicyThresholds = PolicyThresholds()) -> list[dict]:
    """Manufactured components that are common to several finished goods and/or routed through a constraint."""
    items = tables["items"]
    item_type = items.set_index("item_id")["item_type"].to_dict()
    codes = items.set_index("item_id")["item_code"].to_dict()
    children = children_by_parent(tables["bom_headers"], tables["bom_components"], current_as_of(tables["sales_orders"]))
    fg_ids = set(items.loc[items["item_type"] == "FG", "item_id"].astype(int))

    def descendants(item_id, seen=frozenset()):
        found = set()
        for child in children.get(item_id, ()):
            if child in seen:
                continue
            found.add(child)
            found |= descendants(child, seen | {item_id})
        return found

    used_by: dict[int, set[int]] = {}
    for fg in fg_ids:
        for component in descendants(fg):
            if item_type.get(component) not in PURCHASED:
                used_by.setdefault(component, set()).add(fg)
    routing = tables["routing_headers"][["routing_id", "item_id"]].merge(
        tables["routing_operations"][["routing_id", "work_centre_id"]], on="routing_id")
    constrained_items = set(routing.loc[routing["work_centre_id"].isin(constrained_work_centres), "item_id"].astype(int))

    out = []
    for component, parents in sorted(used_by.items()):
        common = len(parents) >= thresholds.min_common_parents
        constrained = component in constrained_items
        if not (common or constrained):
            continue
        reasons = []
        if common:
            reasons.append(f"Common to {len(parents)} finished goods: stock here serves several products.")
        if constrained:
            reasons.append("Routed through a candidate or primary constraint: stock here protects downstream flow.")
        out.append({"item_id": component, "item_code": codes.get(component, str(component)),
                    "finished_goods_served": len(parents), "routes_through_constraint": constrained,
                    "reasons": reasons})
    return out
