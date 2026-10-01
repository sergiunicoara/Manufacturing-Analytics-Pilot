"""Reading a simulation: work in progress at each stock point, the effect of a forecast change on it, and the
side-by-side comparison of the shop-floor policies.

A *stock point* is a place where work waits between two operations. The plant has named ones (after laser cutting,
before painting: colour is committed there); every other waiting place is reported in the stage table. Everything is
reconstructed from the operation log of a finished run, so a point's history is exactly what the simulation did.
"""
from __future__ import annotations

import dataclasses
from collections import defaultdict
from dataclasses import dataclass

from app.analytics.flow.model import COATING_PROCESSES, HOURS_PER_WEEK, FlowParameters, FlowPlant, FlowResult, Policy
from app.analytics.flow.sim import FlowSimulation


@dataclass(frozen=True)
class WipPoint:
    name: str
    after: tuple = ()        # process types: work that has finished an operation there and waits for the next step
    before: tuple = ()       # process types: work that is ready for an operation there and waits for the work centre


DEFAULT_WIP_POINTS = (
    WipPoint("After laser cutting", after=("LASER_CUTTING",)),
    WipPoint("Before painting", before=COATING_PROCESSES),
    WipPoint("Between welding and painting", after=("WELDING", "GRINDING")),
)


# ---- reconstructing waiting intervals from the operation log -----------------------------------------------------
def _value(result: FlowResult, job, done_ops: int) -> float:
    """Value built into `job` after `done_ops` operations: operation hours at the work centre's cost, the purchased
    material issued at the first operation, and the finished components it consumed."""
    if done_ops <= 0:
        return 0.0
    plant = result.plant
    log = {entry[0]: entry for entry in job.op_log}
    total = sum(log[i][3] * plant.work_centres[log[i][4]].cost_per_hour for i in range(min(done_ops, len(job.ops))) if i in log)
    for item, qty, source, _ in job.kit_lines:
        if source != "JOB" and item not in plant.bom and item not in plant.routings:      # purchased material issued
            total += qty * plant.material_cost.get(item, 0.0)
    for child_id in job.children:
        child = result.jobs[child_id]
        total += _value(result, child, len(child.ops))
    return total


def _consumer_start(result: FlowResult, job, end_h: float) -> float:
    if job.parent is None:
        return end_h                                      # a finished good leaves the shop floor
    parent = result.jobs[job.parent]
    return parent.op_log[0][1] if parent.op_log else end_h


def waiting_passes(result: FlowResult) -> list:
    """Every interval in which a lot waits. 'after': from the end of an operation (or of the job) until the next
    operation, or the consumer, starts; carries the process it waits for in `next_process`. 'queue': from ready to the
    start of an operation, used for the stage table. For one lot the two cover the same time apart from transfer."""
    plant, params = result.plant, result.params
    passes = []
    for job in result.jobs.values():
        log = {entry[0]: entry for entry in job.op_log}
        coated = [i in log and plant.work_centres[job.ops[i].wc].process in COATING_PROCESSES for i in range(len(job.ops))]
        for index in range(len(job.ops)):
            if index not in log:
                continue
            _, start, end, _, wc_id, _, _ = log[index]
            process = plant.work_centres[wc_id].process
            if index == 0:
                ready = job.kit_ready_h if job.kit_ready_h is not None else start
            else:
                previous = job.ops[index - 1]
                ready = log[index - 1][2] + (previous.transfer_min / 60.0 if params.transfer_between_ops else 0.0)
            passes.append({"kind": "queue", "process": process, "next_process": process, "job": job, "op_index": index,
                           "enter_h": ready, "exit_h": start, "qty": job.qty, "value": _value(result, job, index),
                           "colour_committed": any(coated[:index])})
            if index + 1 in log:
                leave = log[index + 1][1]
                next_process = plant.work_centres[log[index + 1][4]].process
            elif index + 1 < len(job.ops):
                continue
            else:
                leave = _consumer_start(result, job, end)
                next_process = None
            passes.append({"kind": "after", "process": process, "next_process": next_process, "job": job, "op_index": index,
                           "enter_h": end, "exit_h": max(leave, end), "qty": job.qty,
                           "value": _value(result, job, index + 1), "colour_committed": any(coated[:index + 1])})
    return passes


def in_point(point: WipPoint, entry: dict) -> bool:
    """Work is at a stock point while it waits after an operation of the listed processes, or for an operation of them."""
    return entry["kind"] == "after" and (entry["process"] in point.after or entry["next_process"] in point.before)


def _stock_series(entries: list, start_h: float, end_h: float, step_h: float = 24.0) -> list:
    out, t = [], start_h
    while t <= end_h + 1e-9:
        units = value = 0.0
        for e in entries:
            if e["enter_h"] <= t < e["exit_h"]:
                units += e["qty"]
                value += e["value"]
        out.append({"day": (t - start_h) / 24.0, "units": units, "value": value})
        t += step_h
    return out


def point_history(result: FlowResult, point: WipPoint, passes: list | None = None) -> dict:
    """What happened at a stock point: how much came through, how long it waited, how much sat there and when."""
    passes = passes if passes is not None else waiting_passes(result)
    entries = [e for e in passes if in_point(point, e)]
    start_h = result.params.lead_in_weeks * HOURS_PER_WEEK
    end_h = max((e["exit_h"] for e in entries), default=start_h)
    series = _stock_series(entries, start_h, end_h)
    waits = sorted(((e["exit_h"] - e["enter_h"]) / 24.0, e["qty"]) for e in entries)
    total_qty = sum(q for _, q in waits)
    weighted_wait = sum(w * q for w, q in waits) / total_qty if total_qty else 0.0
    p90 = 0.0
    if waits:
        running, target = 0.0, 0.9 * total_qty
        for wait, qty in waits:
            running += qty
            p90 = wait
            if running >= target:
                break
    return {
        "name": point.name, "passes": len(entries), "units_through": total_qty,
        "value_through": sum(e["value"] for e in entries),
        "mean_wait_days": weighted_wait, "p90_wait_days": p90, "max_wait_days": max((w for w, _ in waits), default=0.0),
        "peak_units": max((s["units"] for s in series), default=0.0), "peak_value": max((s["value"] for s in series), default=0.0),
        "average_units": sum(s["units"] for s in series) / max(len(series), 1),
        "average_value": sum(s["value"] for s in series) / max(len(series), 1), "series": series,
    }


def weekly_stock(history: dict, weeks: int) -> list:
    """Average units and value in stock per week of the point's history (week 0 is the first demand week)."""
    out = []
    for week in range(weeks):
        days = [s for s in history["series"] if week * 7 <= s["day"] < (week + 1) * 7]
        out.append({"week": week, "units": sum(s["units"] for s in days) / 7.0,        # days after the stock empties count as zero
                    "value": sum(s["value"] for s in days) / 7.0})
    return out


def stage_table(result: FlowResult, at_h: float, passes: list | None = None) -> list:
    """The audit at one moment: for every stage, what is waiting there (parts that finished an operation and wait for
    the next one, or for the lot that uses them) and what is being worked on."""
    passes = passes if passes is not None else waiting_passes(result)
    plant = result.plant
    rows: dict = defaultdict(lambda: {"jobs": 0, "units": 0.0, "value": 0.0, "age_days": 0.0})
    for e in passes:
        if e["kind"] == "after" and e["enter_h"] <= at_h < e["exit_h"]:
            label = f"Waiting before {e['next_process']}" if e["next_process"] else "Finished parts waiting for the lot that uses them"
            row = rows[(label, e["next_process"] or "CONSUMER")]
            row["jobs"] += 1
            row["units"] += e["qty"]
            row["value"] += e["value"]
            row["age_days"] += (at_h - e["enter_h"]) / 24.0 * e["qty"]
    for job in result.jobs.values():
        for (index, start, end, work_h, wc_id, _, _) in job.op_log:
            if start <= at_h < end:
                rate = plant.work_centres[wc_id].cost_per_hour
                done = (at_h - start) / max(end - start, 1e-9)
                row = rows[(f"In process at {plant.work_centres[wc_id].process}", plant.work_centres[wc_id].process)]
                row["jobs"] += 1
                row["units"] += job.qty
                row["value"] += _value(result, job, index) + work_h * done * rate
    out = []
    for (label, process), row in sorted(rows.items()):
        out.append({"stage": label, "process": process, "jobs": row["jobs"], "units": row["units"], "value": row["value"],
                    "mean_age_days": row["age_days"] / row["units"] if row["units"] else 0.0})
    return out


# ---- a change of forecast ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ForecastChange:
    day: float = 14.0                 # days after the first demand week starts
    from_week: int = 2                # demand weeks from this one on are changed ...
    to_week: int | None = None        # ... up to and including this one (None: to the end of the horizon)
    factor: float = 0.0               # 0 cancels, 0.5 halves, 1.3 raises by 30 %
    families: tuple = ()              # empty: every product family
    item_ids: tuple = ()              # empty: every item of the families


def _lot_week(lot: dict, params: FlowParameters) -> int:
    return int(round(lot["release_h"] / HOURS_PER_WEEK)) - params.lead_in_weeks


def _matches(plant: FlowPlant, change: ForecastChange, item_id: int) -> bool:
    if change.item_ids and item_id not in change.item_ids:
        return False
    return not change.families or plant.item_families.get(item_id) in change.families


def _in_weeks(change: ForecastChange, week: int) -> bool:
    return week >= change.from_week and (change.to_week is None or week <= change.to_week)


def changed_plant(plant: FlowPlant, change: ForecastChange) -> FlowPlant:
    demand = [(week, item, qty * change.factor if _in_weeks(change, week) and _matches(plant, change, item) else qty)
              for week, item, qty in plant.demand]
    return dataclasses.replace(plant, demand=[row for row in demand if row[2] > 1e-9])


def _committed(job_log, plant: FlowPlant, upto: int) -> bool:
    return any(plant.work_centres[e[4]].process in COATING_PROCESSES for e in job_log if e[0] < upto)


def _need_key(plant: FlowPlant, job, committed: bool):
    return (job.item_id, job.colour if committed else None)


def change_impact(plant: FlowPlant, params: FlowParameters, policy: Policy, change: ForecastChange,
                  points: tuple = DEFAULT_WIP_POINTS, seed: int | None = None, runs: tuple | None = None,
                  shares: dict | None = None) -> dict:
    """What a forecast change does, point by point. Run A is the plan as it stood; run B is the same plant with the
    changed demand. At the moment of the change (`change.day`) the work already done on the lots that are reduced or
    cancelled is read from run A: the unwanted share of it is split into what other open work still needs (reusable,
    unless it already carries a colour that other work does not share) and what is stranded. B shows the new load."""
    if runs is None:
        a = FlowSimulation(plant, params, policy, seed).run()
        b = FlowSimulation(changed_plant(plant, change), params, policy, seed).run()
    else:
        a, b = runs
    at_h = params.lead_in_weeks * HOURS_PER_WEEK + change.day * 24.0
    unwanted_share = max(0.0, 1.0 - change.factor)
    if shares is None:
        affected_roots = {lot["jid"] for lot in a.fg_lots
                          if _in_weeks(change, _lot_week(lot, params)) and _matches(plant, change, lot["item_id"])}
        shares = {a.jobs[j].root: unwanted_share for j in affected_roots}
    affected_roots = {root for root, share in shares.items() if share > 0}

    passes_a = waiting_passes(a)
    # future need in B: units of work not yet started at the change moment, per item (and colour once committed)
    future: dict = defaultdict(float)
    for job in b.jobs.values():
        started = job.op_log[0][1] if job.op_log else job.release_h
        if started >= at_h:
            future[(job.item_id, None)] += job.qty
            future[(job.item_id, job.colour)] += job.qty
    rows, stranded_total, sunk_total, reusable_total = [], 0.0, 0.0, 0.0
    consumed_need: dict = defaultdict(float)
    holding = [e for e in passes_a if e["kind"] == "after" and e["job"].root in affected_roots
               and e["enter_h"] <= at_h < e["exit_h"]]
    holding.sort(key=lambda e: (e["job"].jid, e["op_index"]))
    stage: dict = defaultdict(lambda: {"units": 0.0, "value": 0.0, "reusable_units": 0.0, "reusable_value": 0.0})
    for e in holding:
        key_c = _need_key(plant, e["job"], e["colour_committed"])
        share = shares[e["job"].root]
        unwanted_units = e["qty"] * share
        unwanted_value = e["value"] * share
        room = max(0.0, future.get(key_c, 0.0) - consumed_need[key_c])
        reuse_units = min(unwanted_units, room)
        consumed_need[key_c] += reuse_units
        reuse_value = unwanted_value * (reuse_units / unwanted_units) if unwanted_units > 0 else 0.0
        label = f"After {e['process']}, waiting for {e['next_process'] or 'its consumer'}"
        slot = stage[(label, e["process"], e["next_process"])]
        slot["units"] += unwanted_units
        slot["value"] += unwanted_value
        slot["reusable_units"] += reuse_units
        slot["reusable_value"] += reuse_value
        sunk_total += unwanted_value
        reusable_total += reuse_value
    # in-process operations at the change moment count as sunk work on the lots that are changed
    in_process_value = 0.0
    for job in a.jobs.values():
        if job.root in affected_roots:
            for (index, start, end, work_h, wc_id, _, _) in job.op_log:
                if start <= at_h < end:
                    partial = work_h * ((at_h - start) / max(end - start, 1e-9)) * plant.work_centres[wc_id].cost_per_hour
                    in_process_value += (_value(a, job, index) + partial) * shares[job.root]
    sunk_total += in_process_value

    named = []
    for point in points:
        hist_a = point_history(a, point, passes_a)
        hist_b = point_history(b, point)
        held = [(k, v) for k, v in stage.items() if k[1] in point.after or k[2] in point.before]
        named.append({
            "name": point.name,
            "units_at_change": sum(v["units"] for _, v in held), "unwanted_value": sum(v["value"] for _, v in held),
            "reusable_value": sum(v["reusable_value"] for _, v in held),
            "stranded_value": sum(v["value"] - v["reusable_value"] for _, v in held),
            "average_units_before": hist_a["average_units"], "average_units_after": hist_b["average_units"],
            "peak_units_before": hist_a["peak_units"], "peak_units_after": hist_b["peak_units"],
            "average_wait_days_before": hist_a["mean_wait_days"], "average_wait_days_after": hist_b["mean_wait_days"],
            "history_before": hist_a, "history_after": hist_b,
        })

    hours_a, hours_b = defaultdict(float), defaultdict(float)
    for wc_id, stats in a.wc_stats.items():
        hours_a[plant.work_centres[wc_id].process] += stats["busy_h"]
    for wc_id, stats in b.wc_stats.items():
        hours_b[plant.work_centres[wc_id].process] += stats["busy_h"]
    resources = [{"process": p, "hours_before": hours_a[p], "hours_after": hours_b[p], "change": hours_b[p] - hours_a[p]}
                 for p in sorted(set(hours_a) | set(hours_b))]
    return {
        "change": dataclasses.asdict(change), "at_h": at_h,
        "metrics_before": a.metrics, "metrics_after": b.metrics,
        "sunk_value": sunk_total, "reusable_value": reusable_total, "stranded_value": sunk_total - reusable_total,
        "in_process_value": in_process_value,
        "stages": [{"stage": k[0], "process": k[1], "next_process": k[2], **v, "stranded_value": v["value"] - v["reusable_value"]}
                   for k, v in sorted(stage.items())],
        "points": named, "resources": resources,
        "audit_at_change": stage_table(a, at_h, passes_a), "lots_changed": len(affected_roots),
    }


def forecast_revisions(plant: FlowPlant, params: FlowParameters, policy: Policy, ratios_by_revision: list,
                       seed: int | None = None) -> list:
    """Replay weekly forecast updates. `ratios_by_revision[j-1]` maps (week, item) to new/old forecast for the update
    received on the Sunday that starts demand week j. Work for weeks up to j is kept as planned; later weeks are
    re-planned. Each update is compared with the plan before it, as a forecast change at that moment."""
    plans = [plant]
    runs = [FlowSimulation(plant, params, policy, seed).run()]
    out = []
    for j, ratios in enumerate(ratios_by_revision, start=1):
        before = plans[-1]
        demand, shares = [], {}
        for week, item, qty in before.demand:
            ratio = ratios.get((week, item), 1.0) if week > j else 1.0
            demand.append((week, item, qty * ratio))
        after = dataclasses.replace(before, demand=[row for row in demand if row[2] > 1e-9])
        run_after = FlowSimulation(after, params, policy, seed).run()
        old = {(w, i): q for w, i, q in before.demand}
        new = {(w, i): q for w, i, q in after.demand}
        for lot in runs[-1].fg_lots:
            key = (_lot_week(lot, params), lot["item_id"])
            if old.get(key, 0.0) > 0:
                shares[runs[-1].jobs[lot["jid"]].root] = max(0.0, 1.0 - new.get(key, 0.0) / old[key])
        change = ForecastChange(day=7.0 * j, from_week=j + 1, factor=1.0)
        impact = change_impact(before, params, policy, change, seed=seed, runs=(runs[-1], run_after),
                               shares=shares or {runs[-1].fg_lots[0]["jid"] if runs[-1].fg_lots else 0: 0.0})
        up = sum(max(0.0, new.get(k, 0.0) - old.get(k, 0.0)) for k in set(old) | set(new))
        down = sum(max(0.0, old.get(k, 0.0) - new.get(k, 0.0)) for k in set(old) | set(new))
        impact["revision"] = j
        impact["units_added"] = up
        impact["units_removed"] = down
        out.append(impact)
        plans.append(after)
        runs.append(run_after)
    return out


# ---- policy comparison ------------------------------------------------------------------------------------------
METRIC_KEYS = ("late_lots", "on_time_pct", "mean_lateness_days", "mean_lead_time_days", "makespan_days", "changeovers",
               "changeover_hours", "coating_setup_hours", "component_wait_days", "scrapped_units", "scrap_value",
               "remake_jobs", "processing_hours", "processing_cost", "overtime_paid_h", "overtime_busy_h",
               "overtime_idle_h", "overtime_cost", "overtime_stranded_h", "monday_idle_h", "monday_idle_after_overtime_h",
               "purchased_shortage_lines", "lots")


def _mean_std(runs: list) -> tuple[dict, dict]:
    mean = {k: sum(r[k] for r in runs) / len(runs) for k in METRIC_KEYS}
    std = {k: (sum((r[k] - mean[k]) ** 2 for r in runs) / len(runs)) ** 0.5 for k in METRIC_KEYS}
    return mean, std


def policy_suite(plant: FlowPlant, params: FlowParameters, replications: int = 20, windows: tuple = (0.0, 2.0, 5.0, 10.0),
                 overtime_work_centres: tuple = (), overtime_hours: float = 8.0) -> list:
    """Every policy against today's practice on the same plant, demand and seed. Colour grouping, kit priority and
    overtime are deterministic given the seed; scrap handling is averaged over `replications` seeded runs."""
    out: list = []

    def single(group, key, label, policy):
        result = FlowSimulation(plant, params, policy).run()
        out.append({"group": group, "key": key, "label": label, "policy": dataclasses.asdict(policy), "reps": 1,
                    "metrics": {k: result.metrics[k] for k in METRIC_KEYS}, "std": None})

    def repeated(group, key, label, policy):
        runs = [FlowSimulation(plant, params, policy, seed=params.seed + r).run().metrics for r in range(replications)]
        mean, std = _mean_std(runs)
        out.append({"group": group, "key": key, "label": label, "policy": dataclasses.asdict(policy), "reps": replications,
                    "metrics": mean, "std": std})

    watch = dict(overtime_work_centres=overtime_work_centres, overtime_hours=overtime_hours)
    single("baseline", "today", "Today: earliest due date, colour ignored", Policy(name="today", **watch))
    for window in windows:
        single("colour", f"colour_{window:g}", f"Group colours, window {window:g} days", Policy(name=f"colour {window:g}", colour_window_days=window, **watch))
    single("priority", "kit_priority", "Prioritise the last missing component", Policy(name="kit priority", kit_priority=True, **watch))
    for mode, label in (("final", "Defects found at final inspection: whole product scrapped (today)"),
                        ("replace", "Defects found at final inspection: replace only the failed component"),
                        ("component", "Inspect each component: remake only the bad one")):
        repeated("scrap", f"scrap_{mode}", label, Policy(name=f"scrap {mode}", defects=True, scrap_mode=mode, **watch))
    if overtime_work_centres:
        single("overtime", "overtime_unconditional", "Saturday overtime, always paid",
               Policy(name="overtime", overtime="unconditional", overtime_work_centres=overtime_work_centres, overtime_hours=overtime_hours))
        single("overtime", "overtime_gated", "Saturday overtime only when its work will be used",
               Policy(name="overtime gated", overtime="gated", overtime_work_centres=overtime_work_centres, overtime_hours=overtime_hours))
    combined = Policy(name="combined", colour_window_days=windows[-1] if windows else None, kit_priority=True, defects=True,
                      scrap_mode="component", overtime="gated" if overtime_work_centres else "none",
                      overtime_work_centres=overtime_work_centres, overtime_hours=overtime_hours)
    repeated("combined", "combined", "All together", combined)
    repeated("combined", "today_with_defects", "Today, with defects", Policy(name="today with defects", defects=True, scrap_mode="final", **watch))
    return out
