"""Discrete-event simulation of lots through work centres.

Model (every simplification is deliberate and listed in ANALYTICS_METHODS.md#shop-floor-flow):
- A job is one lot of one item. It may start its first operation only when every component is available: purchased
  parts from stock, scheduled receipts or an unplanned purchase; manufactured parts from stock or from child jobs.
  Later operations follow the item's current routing, one work centre per operation, one operation at a time.
- A work centre works inside weekday shift windows (Saturday windows only when overtime is approved). Productive
  time is the work content divided by the work centre's availability.
- Dispatch is earliest due date, except where the policy under test changes it (colour grouping, kit priority).
- Coating setup follows the colour rule (a colour change costs `changeover_minutes`, same colour a small reload);
  every other work centre uses the routing's setup per batch.
- Defects, when enabled, are drawn per unit from a seeded generator, so a run is reproducible.
"""
from __future__ import annotations

import heapq
import math
from collections import defaultdict

import numpy as np

from app.analytics.flow.model import (ASSEMBLY_PROCESSES, COATING_PROCESSES, HOURS_PER_WEEK, INSPECTION, FlowParameters,
                                      FlowPlant, FlowResult, Job, OpTemplate, Policy)

EPS = 1e-9


class FlowSimulation:
    def __init__(self, plant: FlowPlant, params: FlowParameters | None = None, policy: Policy | None = None,
                 seed: int | None = None):
        for wc in plant.work_centres.values():
            if wc.hours_per_day <= 0:
                raise ValueError(f"work centre {wc.wc_id} has no working hours: remove it from the plant")
        self.plant = plant
        self.params = params or FlowParameters()
        self.policy = policy or Policy()
        self.rng = np.random.default_rng(self.params.seed if seed is None else seed)
        self.jobs: dict[int, Job] = {}
        self.stock = dict(plant.stock)
        self.receipts = {item: [list(r) for r in sorted(rows)] for item, rows in plant.receipts.items()}
        self.queue = defaultdict(list)             # wc -> [(job, op_index, ready_h)]
        self.busy = {wc: False for wc in plant.work_centres}
        self.last_colour = {wc: None for wc in plant.work_centres}
        self.wake_scheduled: set = set()
        self.running: dict = {}                    # wc -> the operation in progress (see _dispatch)
        self._tokens = 0
        self.heap: list = []
        self.seq = 0
        self.now = 0.0
        self.ot_windows: dict = {}                 # (wc, week) -> {"start","end","approved_h","busy_h"}
        self.ot_ops: list = []                     # (job id, op index, hours inside an overtime window)
        self.wc_busy = defaultdict(float)
        self.wc_setup = defaultdict(float)
        self.wc_changeovers = defaultdict(int)
        self.wc_ops = defaultdict(int)
        self.lot_done: dict = {}
        self.shortage_lines = 0
        self.remake_rounds: dict = defaultdict(int)
        self.scrap = {"units": 0.0, "value": 0.0, "remakes": 0, "events": 0}
        self._next_jid = 0
        self._desc_cache: dict = {}
        self._value_cache: dict = {}
        self._inspect_wcs = sorted(w for w, c in plant.work_centres.items() if c.process == INSPECTION)

    # ---- helpers --------------------------------------------------------------------------------------------
    def _push(self, t, kind, a=None, b=None):
        self.seq += 1
        heapq.heappush(self.heap, (t, self.seq, kind, a, b))

    def is_manufactured(self, item_id: int) -> bool:
        return item_id in self.plant.bom or item_id in self.plant.routings

    def defect_p(self, item_id: int) -> float:
        """Unit defect probability: the largest BOM scrap_pct on a line that uses the item, else the assumed default."""
        lines = [scrap for parent in self.plant.bom.values() for comp, _, scrap in parent if comp == item_id]
        top = max(lines, default=0.0)
        return top if top > 0 else self.params.defect_rate_default

    def std_unit_value(self, item_id: int) -> float:
        """Standard value of one unit: purchased material at its standard cost, processing hours at each work
        centre's hourly cost, plus the components. Prices scrapped units the same way under every policy."""
        if item_id in self._value_cache:
            return self._value_cache[item_id]
        if not self.is_manufactured(item_id):
            value = self.plant.material_cost.get(item_id, 0.0)
        else:
            value = sum(qpp * self.std_unit_value(comp) for comp, qpp, _ in self.plant.bom[item_id])
            for op in self.plant.routings.get(item_id, ()):
                rate = self.plant.work_centres[op.wc].cost_per_hour
                value += (op.setup_min / max(op.batch, 1.0) + op.run_min / max(op.yield_pct, 0.01)) / 60.0 * rate
        self._value_cache[item_id] = value
        return value

    def descendants(self, item_id: int) -> list:
        """Manufactured descendants as (item_id, units consumed per unit of `item_id`)."""
        if item_id not in self._desc_cache:
            out = []
            for comp, qpp, _ in self.plant.bom.get(item_id, ()):
                if self.is_manufactured(comp):
                    out.append((comp, qpp))
                    out.extend((i, m * qpp) for i, m in self.descendants(comp))
            self._desc_cache[item_id] = out
        return self._desc_cache[item_id]

    # ---- calendar -------------------------------------------------------------------------------------------
    def _windows(self, wc_id: int, t: float):
        width = self.plant.work_centres[wc_id].hours_per_day
        week = max(0, int(t // HOURS_PER_WEEK) - 1)
        while True:
            for day in range(self.params.working_days):
                start = week * HOURS_PER_WEEK + day * 24.0 + self.params.shift_start_hour
                if start + width > t:
                    yield start, start + width
            window = self.ot_windows.get((wc_id, week))
            if window and window["end"] > t:
                yield window["start"], window["end"]
            week += 1

    def next_work_time(self, wc_id: int, t: float) -> float:
        for start, end in self._windows(wc_id, t):
            if end > t:
                return max(start, t)
        raise RuntimeError("no working window")

    def advance(self, wc_id: int, t: float, clock_hours: float) -> float:
        need = clock_hours
        for start, end in self._windows(wc_id, t):
            begin = max(start, t)
            room = end - begin
            if room <= 0:
                continue
            if need <= room + EPS:
                return begin + need
            need -= room
        raise RuntimeError("no working window")

    # ---- plan: lots -> jobs ---------------------------------------------------------------------------------
    def expand(self, item_id, qty, release_h, due_h, colour, level=0, parent=None, root=None, kind="PLAN") -> Job:
        """Create the job for one lot and, recursively, a child job for every manufactured component the stock
        cannot cover. Purchased components are drawn from stock, then scheduled receipts, then an unplanned purchase."""
        job = Job(self._next_jid, item_id, qty, release_h, due_h, colour, level, parent,
                  self._next_jid if root is None else root, kind)
        self._next_jid += 1
        job.ops = list(self.plant.routings.get(item_id, ()))
        self.jobs[job.jid] = job
        offset = self.params.level_offset_days * 24.0
        for comp, qpp, scrap in self.plant.bom.get(item_id, ()):
            if self.is_manufactured(comp):
                need = qty * qpp
                take = min(self.stock.get(comp, 0.0), need)
                if take > EPS:
                    self.stock[comp] -= take
                    job.kit_lines.append((comp, take, "STOCK", 0.0))
                if need - take > EPS:
                    child = self.expand(comp, need - take, max(0.0, release_h - offset), release_h, colour, level + 1,
                                        job.jid, job.root, kind)
                    job.children.append(child.jid)
                    job.pending_children += 1
                    job.kit_lines.append((comp, need - take, "JOB", child.jid))
            else:
                self._draw_purchased(job, comp, qty * qpp / max(1.0 - scrap, 0.01))
        if self.policy.defects and self.policy.scrap_mode == "component" and parent is not None and job.ops:
            job.ops.append(self._component_inspection(job))
        if parent is None:
            job.final_inspection_index = self._final_inspection_index(job)
        return job

    def _draw_purchased(self, job: Job, comp: int, need: float):
        take = min(self.stock.get(comp, 0.0), need)
        if take > EPS:
            self.stock[comp] -= take
            job.kit_lines.append((comp, take, "STOCK", 0.0))
        need -= take
        for entry in self.receipts.get(comp, []):
            if need <= EPS or entry[0] > job.due_h:        # a receipt after the job's due date does not supply it
                break
            use = min(entry[1], need)
            if use > EPS:
                entry[1] -= use
                need -= use
                job.fixed_ready_h = max(job.fixed_ready_h, entry[0])
                job.kit_lines.append((comp, use, "RECEIPT", entry[0]))
        if need > EPS:
            available = job.release_h + self.params.unplanned_purchase_lead_days * 24.0
            job.fixed_ready_h = max(job.fixed_ready_h, available)
            job.kit_lines.append((comp, need, "SHORTAGE", available))
            self.shortage_lines += 1

    def _component_inspection(self, job: Job) -> OpTemplate:
        wc = self._inspect_wcs[job.jid % len(self._inspect_wcs)] if self._inspect_wcs else job.ops[-1].wc
        return OpTemplate(wc, "Component inspection", 0.0, self.params.component_inspection_minutes, 1.0, 1.0, 0.0,
                          "COMP_INSPECT")

    def _final_inspection_index(self, job: Job) -> int | None:
        for index, op in enumerate(job.ops):
            if self.plant.work_centres[op.wc].process == INSPECTION and op.kind == "ROUTING":
                return index
        return len(job.ops) - 1 if job.ops else None

    # ---- run ------------------------------------------------------------------------------------------------
    def run(self) -> FlowResult:
        params = self.params
        colours, weights = zip(*params.colours)
        weights = np.array(weights, dtype=float) / sum(weights)
        for week, item_id, qty in sorted(self.plant.demand, key=lambda d: (d[0], d[1])):
            covered = min(self.stock.get(item_id, 0.0), qty)       # finished stock is netted first, as in the period engine
            if covered > 0:
                self.stock[item_id] -= covered
                qty -= covered
            if qty <= EPS:
                continue
            lots = max(1, math.ceil(qty / params.max_lot_qty - EPS))
            release = (week + params.lead_in_weeks) * HOURS_PER_WEEK
            due = release + HOURS_PER_WEEK + params.due_slack_days * 24.0
            for _ in range(lots):
                self.expand(item_id, qty / lots, release, due, str(self.rng.choice(colours, p=weights)))
        for job in list(self.jobs.values()):
            if job.pending_children == 0:
                self._push(max(job.release_h, job.fixed_ready_h), "kit", job.jid)
        if self.policy.overtime != "none":
            weeks = params.lead_in_weeks + params.horizon_weeks          # supervision covers the demand horizon
            for wc in self.policy.overtime_work_centres:
                if wc in self.plant.work_centres:
                    width = self.plant.work_centres[wc].hours_per_day
                    for week in range(weeks):
                        end = week * HOURS_PER_WEEK + (params.working_days - 1) * 24.0 + params.shift_start_hour + width
                        self._push(end, "otdecide", wc, week)
        while self.heap:
            t, _, kind, a, b = heapq.heappop(self.heap)
            self.now = t
            if kind == "kit":
                self._kit_ready(a, t)
            elif kind == "opready":
                self._enqueue(self.jobs[a], b, t)
            elif kind == "opdone":
                job = self.jobs[a]
                wc = job.ops[b[0]].wc
                if self.running.get(wc) and self.running[wc]["token"] == b[1]:
                    self._op_done(a, b[0], t)
            elif kind == "wcfree":
                if self.running.get(a) and self.running[a]["token"] == b:
                    self.running[a] = None
                    self.busy[a] = False
                    self._dispatch(a, t)
            elif kind == "wake":
                self.wake_scheduled.discard((a, t))
                self._dispatch(a, t)
            elif kind == "otdecide":
                self._ot_decide(a, b)
        return self._result()

    def _kit_ready(self, jid, t):
        job = self.jobs[jid]
        if job.kit_ready_h is not None:
            return
        job.kit_ready_h = t
        if job.ops:
            self._enqueue(job, 0, t)
        else:
            self._finish_job(job, t)

    def _enqueue(self, job, index, t):
        wc = job.ops[index].wc
        self.queue[wc].append((job, index, t))
        self._dispatch(wc, t)

    # ---- dispatch -------------------------------------------------------------------------------------------
    def _boosted(self, job: Job) -> bool:
        if not self.policy.kit_priority or job.parent is None:
            return False
        parent = self.jobs[job.parent]
        waiting = parent.kit_ready_h is None or parent.waiting_for == "rework"
        # only worth pulling forward when this component is all the parent is missing: its purchased parts are
        # already available and its release date has passed
        return (waiting and parent.pending_children <= self.policy.kit_max_missing
                and parent.fixed_ready_h <= self.now and parent.release_h <= self.now)

    def _pick(self, wc_id):
        entries = self.queue[wc_id]
        pool = entries
        last = self.last_colour[wc_id]
        window = self.policy.colour_window_days
        if window is not None and last is not None and self.plant.work_centres[wc_id].process in COATING_PROCESSES:
            earliest = min(job.due_h for job, _, _ in entries)
            same = [e for e in entries if e[0].colour == last and e[0].due_h <= earliest + window * 24.0 + EPS]
            if same:
                pool = same
        return min(pool, key=lambda e: (0 if self._boosted(e[0]) else 1, e[0].due_h, e[0].jid))

    def _op_work_hours(self, job: Job, op: OpTemplate) -> tuple[float, float]:
        """(setup hours, run hours) excluding the colour rule, which depends on the previous job."""
        if op.kind == "ROUTING":
            batches = max(1, math.ceil(job.qty / max(op.batch, 1.0) - EPS))
            return op.setup_min * batches / 60.0, op.run_min * job.qty / max(op.yield_pct, 0.01) / 60.0
        return op.setup_min / 60.0, op.run_min * job.qty / 60.0

    def _dispatch(self, wc_id, t):
        if self.busy[wc_id] or not self.queue[wc_id]:
            return
        start = self.next_work_time(wc_id, t)
        if start > t + EPS:
            if (wc_id, start) not in self.wake_scheduled:
                self.wake_scheduled.add((wc_id, start))
                self._push(start, "wake", wc_id)
            return
        entry = self._pick(wc_id)
        self.queue[wc_id].remove(entry)
        job, index, _ = entry
        op = job.ops[index]
        wc = self.plant.work_centres[wc_id]
        setup_h, run_h = self._op_work_hours(job, op)
        change = False
        if wc.process in COATING_PROCESSES and op.kind == "ROUTING":
            last = self.last_colour[wc_id]
            change = last is not None and last != job.colour
            setup_h = (self.params.changeover_minutes if change else self.params.same_colour_setup_minutes) / 60.0
            self.last_colour[wc_id] = job.colour
        work_h = setup_h + run_h
        finish = self.advance(wc_id, t, work_h / max(wc.availability, 0.05))
        self.busy[wc_id] = True
        job.op_log.append((index, t, finish, work_h, wc_id, setup_h, change))
        self._tokens += 1
        self.running[wc_id] = {"job": job, "index": index, "start": t, "clock_h": work_h / max(wc.availability, 0.05),
                               "finish": finish, "token": self._tokens, "log": len(job.op_log) - 1}
        self.wc_busy[wc_id] += work_h
        self.wc_setup[wc_id] += setup_h
        self.wc_ops[wc_id] += 1
        if change:
            self.wc_changeovers[wc_id] += 1
        inside = 0.0
        for (window_wc, _), window in self.ot_windows.items():
            if window_wc == wc_id:
                overlap = max(0.0, min(finish, window["end"]) - max(t, window["start"]))
                window["busy_h"] += overlap
                inside += overlap
        if inside > EPS:
            self.ot_ops.append((job.jid, index, inside))
        self._push(finish, "opdone", job.jid, (index, self._tokens))
        self._push(finish, "wcfree", wc_id, self._tokens)

    # ---- completion and defects -----------------------------------------------------------------------------
    def _op_done(self, jid, index, t):
        job = self.jobs[jid]
        op = job.ops[index]
        if self.policy.defects:
            if op.kind == "COMP_INSPECT":
                self._component_inspected(job, t)
            elif (job.parent is None and index == job.final_inspection_index and op.kind in ("ROUTING", "REINSPECT")
                  and self.policy.scrap_mode in ("final", "replace")):
                if self._final_inspected(job, index, t):
                    return
        if index + 1 < len(job.ops):
            delay = op.transfer_min / 60.0 if self.params.transfer_between_ops else 0.0
            self._push(t + delay, "opready", job.jid, index + 1)
        else:
            self._finish_job(job, t)

    def _finish_job(self, job: Job, t):
        job.done_h = t
        self.lot_done[job.root] = max(self.lot_done.get(job.root, 0.0), t)
        if job.parent is None:
            return
        parent = self.jobs[job.parent]
        parent.pending_children -= 1
        if parent.pending_children == 0:
            if parent.waiting_for == "kit":
                self._push(max(t, parent.release_h, parent.fixed_ready_h), "kit", parent.jid)
            else:
                self._rework_ready(parent, t)

    def _subtree(self, job: Job):
        stack = [job.jid]
        while stack:
            current = self.jobs[stack.pop()]
            yield current
            stack.extend(current.children)

    def _start_subtree(self, job: Job, t):
        for member in self._subtree(job):
            if member.pending_children == 0 and member.kit_ready_h is None:
                self._push(max(t, member.release_h, member.fixed_ready_h), "kit", member.jid)

    def _component_inspected(self, job: Job, t):
        """Component inspection: bad units are found and only they are remade; the parent keeps waiting for them."""
        if job.rounds >= self.params.max_rework_rounds:
            return                                         # replacements are assumed good after this many rounds
        bad = int(self.rng.binomial(int(round(job.qty)), self.defect_p(job.item_id)))
        if bad <= 0:
            return
        self.scrap["units"] += bad
        self.scrap["value"] += bad * self.std_unit_value(job.item_id)
        self.scrap["events"] += 1
        parent = self.jobs[job.parent]
        parent.pending_children += 1
        remake = self.expand(job.item_id, float(bad), t, t + HOURS_PER_WEEK, job.colour, job.level, parent.jid, job.root,
                             "REMAKE")
        self.scrap["remakes"] += 1
        remake.rounds = job.rounds + 1
        parent.children.append(remake.jid)
        self._start_subtree(remake, t)

    def _final_inspected(self, job: Job, index: int, t) -> bool:
        """Final inspection of a finished good. Returns True when the job now waits for replacement components."""
        if job.replaced:                                   # re-inspection after a replacement
            checked, job.replaced = job.replaced, {}
            bad_by_item = {item: int(self.rng.binomial(n, self.defect_p(item))) for item, n in checked.items()}
            bad_by_item = {item: n for item, n in bad_by_item.items() if n > 0}
            return bool(bad_by_item) and self._replace(job, index, bad_by_item, sum(bad_by_item.values()), t)
        units = int(round(job.qty))
        if units <= 0 or self.remake_rounds[job.root] >= self.params.max_rework_rounds:
            return False
        bad_unit = np.zeros(units, dtype=bool)
        bad_by_item: dict = {}
        for item, per_unit in self.descendants(job.item_id):
            counts = self.rng.binomial(max(1, int(round(per_unit))), self.defect_p(item), size=units)
            bad_unit |= counts > 0
            if counts.sum():
                bad_by_item[item] = bad_by_item.get(item, 0) + int(counts.sum())
        bad_units = int(bad_unit.sum())
        if bad_units == 0:
            return False
        self.scrap["events"] += 1
        if self.policy.scrap_mode == "replace":
            return self._replace(job, index, bad_by_item, bad_units, t)
        # today: the finished good is refused as a whole and every good component in it is lost with it
        self.scrap["units"] += bad_units
        self.scrap["value"] += bad_units * self.std_unit_value(job.item_id)
        job.scrapped_units += bad_units
        self.remake_rounds[job.root] += 1
        remake = self.expand(job.item_id, float(bad_units), t, job.due_h, job.colour, 0, None, job.root, "REMAKE")
        self.scrap["remakes"] += 1
        self._start_subtree(remake, t)
        return False

    def _replace(self, job: Job, index: int, bad_by_item: dict, bad_units: int, t) -> bool:
        """Only the failed components are scrapped and remade; the good ones stay in the finished good."""
        job.rounds += 1
        if job.rounds > self.params.max_rework_rounds:
            return False
        for item, n in bad_by_item.items():
            self.scrap["units"] += n
            self.scrap["value"] += n * self.std_unit_value(item)
        job.replaced = dict(bad_by_item)
        job.waiting_for = "rework"
        job.rework_after = index
        job.rework_units = float(bad_units)
        made = []
        for item, n in bad_by_item.items():
            child = self.expand(item, float(n), t, t + HOURS_PER_WEEK, job.colour, 1, job.jid, job.root, "REMAKE")
            self.scrap["remakes"] += 1
            job.children.append(child.jid)
            job.pending_children += 1
            made.append(child)
        for child in made:
            self._start_subtree(child, t)
        return True

    def _rework_ready(self, job: Job, t):
        index = job.rework_after
        assembly = next((op.wc for op in job.ops if self.plant.work_centres[op.wc].process in ASSEMBLY_PROCESSES),
                        job.ops[index].wc)
        rework = OpTemplate(assembly, "Rework: replace failed component", 0.0, self.params.rework_minutes, 1.0, 1.0, 0.0,
                            "REWORK")
        recheck = OpTemplate(job.ops[index].wc, "Re-inspection", 0.0, self.params.reinspection_minutes, 1.0, 1.0, 0.0,
                             "REINSPECT")
        job.ops[index + 1:index + 1] = [rework, recheck]
        job.waiting_for = "kit"
        job.final_inspection_index = index + 2
        self._push(t, "opready", job.jid, index + 1)

    # ---- overtime -------------------------------------------------------------------------------------------
    def _ot_decide(self, wc_id, week):
        """At the end of the Friday shift decide the Saturday window. Unconditional: the whole window is paid.
        Gated: only as many hours as ready work will be used by a consumer that is itself ready by Monday."""
        wc = self.plant.work_centres[wc_id]
        start = week * HOURS_PER_WEEK + self.params.working_days * 24.0 + self.params.shift_start_hour
        cap = self.policy.overtime_hours
        if self.policy.overtime == "unconditional":
            approved = cap
        else:
            monday_end = (week + 1) * HOURS_PER_WEEK + self.params.shift_start_hour + wc.hours_per_day
            approved = 0.0
            for job, index, _ in sorted(self.queue[wc_id], key=lambda e: (0 if self._boosted(e[0]) else 1, e[0].due_h, e[0].jid)):
                if approved >= cap:
                    break
                useful = index + 1 < len(job.ops) or job.parent is None
                if not useful:
                    parent = self.jobs[job.parent]
                    useful = parent.pending_children == 1 and parent.fixed_ready_h <= monday_end
                if useful:
                    setup_h, run_h = self._op_work_hours(job, job.ops[index])
                    approved += (setup_h + run_h) / max(wc.availability, 0.05)
            approved = min(cap, math.ceil(approved * 2.0) / 2.0) if approved > 0 else 0.0
        if approved <= 0:
            return
        self.ot_windows[(wc_id, week)] = {"wc": wc_id, "week": week, "start": start, "end": start + approved,
                                          "approved_h": approved, "busy_h": 0.0}
        window = self.ot_windows[(wc_id, week)]
        running = self.running.get(wc_id)
        if running and running["finish"] > start:                  # the operation in progress can use the new window
            finish = self.advance(wc_id, running["start"], running["clock_h"])
            if finish < running["finish"] - EPS:
                job = running["job"]
                old = job.op_log[running["log"]]
                job.op_log[running["log"]] = (old[0], old[1], finish, *old[3:])
                running["finish"] = finish
                self._tokens += 1
                running["token"] = self._tokens
                self._push(finish, "opdone", job.jid, (running["index"], self._tokens))
                self._push(finish, "wcfree", wc_id, self._tokens)
            inside = max(0.0, min(running["finish"], window["end"]) - max(running["start"], window["start"]))
            if inside > EPS:
                window["busy_h"] += inside
                self.ot_ops.append((running["job"].jid, running["index"], inside))
        self.wake_scheduled.add((wc_id, start))
        self._push(start, "wake", wc_id)

    # ---- results --------------------------------------------------------------------------------------------
    def _consumer_start(self, job: Job, index: int):
        """When the output of operation `index` is first used: the job's next operation, else its parent's start."""
        if index + 1 < len(job.ops):
            later = [log for log in job.op_log if log[0] == index + 1]
            return later[0][1] if later else None
        if job.parent is None:
            return job.done_h
        parent = self.jobs[job.parent]
        return parent.op_log[0][1] if parent.op_log else None

    def _result(self) -> FlowResult:
        plant, params = self.plant, self.params
        fg_lots = []
        for job in self.jobs.values():
            if job.parent is None and job.kind == "PLAN":
                done = self.lot_done.get(job.root) if job.done_h is not None else None
                fg_lots.append({"jid": job.jid, "item_id": job.item_id, "qty": job.qty, "release_h": job.release_h,
                                "due_h": job.due_h, "finish_h": done,
                                "late_h": None if done is None else max(0.0, done - job.due_h), "colour": job.colour})
        finished = [lot for lot in fg_lots if lot["finish_h"] is not None]
        late = [lot for lot in finished if lot["late_h"] > EPS]
        wait_h = sum(max(0.0, (j.kit_ready_h or 0.0) - j.release_h) for j in self.jobs.values()
                     if j.kit_ready_h is not None and j.kind == "PLAN")
        stranded = 0.0
        for jid, index, inside in self.ot_ops:
            job = self.jobs[jid]
            end = next(log[2] for log in job.op_log if log[0] == index)
            used = self._consumer_start(job, index)
            if used is None or used - end > params.strand_days * 24.0:
                stranded += inside
        overtime = []
        for (wc, week), window in sorted(self.ot_windows.items()):
            paid = window["approved_h"]
            busy = min(window["busy_h"], paid)
            overtime.append({"wc": wc, "week": week, "paid_h": paid, "busy_h": busy, "idle_h": paid - busy,
                             "cost": paid * plant.work_centres[wc].cost_per_hour * params.overtime_premium})
        coating = [w for w, c in plant.work_centres.items() if c.process in COATING_PROCESSES]
        changeovers = sum(self.wc_changeovers[w] for w in coating)
        count = max(len(finished), 1)
        metrics = {
            "lots": len(finished), "late_lots": len(late), "on_time_pct": 100.0 * (1 - len(late) / count),
            "mean_lateness_days": sum(lot["late_h"] for lot in finished) / count / 24.0,
            "max_lateness_days": max((lot["late_h"] for lot in finished), default=0.0) / 24.0,
            "mean_lead_time_days": sum(lot["finish_h"] - lot["release_h"] for lot in finished) / count / 24.0,
            "makespan_days": max((lot["finish_h"] for lot in finished), default=0.0) / 24.0,
            "changeovers": changeovers, "changeover_hours": changeovers * params.changeover_minutes / 60.0,
            "coating_setup_hours": sum(self.wc_setup[w] for w in coating),
            "coating_busy_hours": sum(self.wc_busy[w] for w in coating),
            "component_wait_days": wait_h / 24.0,
            "scrapped_units": self.scrap["units"], "scrap_value": self.scrap["value"],
            "remake_jobs": self.scrap["remakes"], "scrap_events": self.scrap["events"],
            "processing_hours": sum(self.wc_busy.values()),
            "processing_cost": sum(self.wc_busy[w] * plant.work_centres[w].cost_per_hour for w in plant.work_centres),
            "overtime_paid_h": sum(o["paid_h"] for o in overtime), "overtime_busy_h": sum(o["busy_h"] for o in overtime),
            "overtime_idle_h": sum(o["idle_h"] for o in overtime), "overtime_cost": sum(o["cost"] for o in overtime),
            "overtime_stranded_h": stranded,
            "purchased_shortage_lines": self.shortage_lines,
            "unfinished_jobs": sum(1 for j in self.jobs.values() if j.done_h is None), "jobs": len(self.jobs),
        }
        wc_stats = {w: {"busy_h": self.wc_busy[w], "setup_h": self.wc_setup[w], "ops": self.wc_ops[w],
                        "changeovers": self.wc_changeovers[w]} for w in plant.work_centres}
        return FlowResult(self.jobs, wc_stats, fg_lots, metrics, overtime, [], plant, params, self.policy)
