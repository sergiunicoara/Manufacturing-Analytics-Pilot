import { useEffect, useMemo, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const CASES = [
  ["BASELINE", "Baseline", "#64748b"],
  ["DEMAND_SHOCK_ONLY", "+40% CAB-100 shock", "#dc2626"],
  ["BUFFER_ONLY", "Buffer only", "#d97706"],
  ["CAPACITY_ONLY", "Capacity recovery", "#2563eb"],
  ["COMBINED", "Combined", "#059669"],
];

type Point = { week: string; processing_days: number | null; queue_days: number | null; transfer_days: number | null; total_days: number | null };
type Story = { horizon_weeks: number; horizon_rationale: string; demand_shock_pct: number; intervention_start_week: string; lead_time_item_id: number; lead_time_item_scope: string; lead_time_series: Record<string, Point[]>; lead_time_kpis: Record<string, { cab100_demand_weighted_days: number | null; cab100_demand_coverage_pct: number | null; plant_demand_weighted_days: number | null; plant_demand_coverage_pct: number | null }>; capacity_intervention: Array<{ work_centre_id: number; work_centre: string; shifts_per_day: number; hours_per_shift: number; days_per_week: number; scheduled_hours_per_week: number; effective_hours_per_week_current: number | null; effective_hours_per_week_target: number | null; target_multiplier: number; additional_effective_hours_per_week: number | null; additional_scheduled_hours_per_week: number; calendar_arrangement: string }>; evidence: { source: string; queue_method: string; cases: string[] } };

function LineChart({ story }: { story: Story }) {
  const width = 840, height = 310, left = 48, right = 18, top = 20, bottom = 42;
  const values = CASES.flatMap(([key]) => story.lead_time_series[key].map(p => p.total_days ?? 0));
  const max = Math.max(1, ...values) * 1.12;
  const x = (i: number) => left + i * (width - left - right) / (story.horizon_weeks - 1);
  const y = (v: number) => top + (height - top - bottom) * (1 - v / max);
  return <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Weekly total lead time by scenario">
    {[0, .25, .5, .75, 1].map(t => <g key={t}><line x1={left} x2={width-right} y1={y(max*t)} y2={y(max*t)} stroke="#e2e8f0"/><text x={left-8} y={y(max*t)+4} textAnchor="end" fill="#64748b" fontSize="11">{(max*t).toFixed(1)}d</text></g>)}
    {CASES.map(([key, label, color]) => { const pts = story.lead_time_series[key]; return <g key={key}><polyline fill="none" stroke={color} strokeWidth={key === "DEMAND_SHOCK_ONLY" ? 3.5 : 2.5} points={pts.map((p,i)=>`${x(i)},${y(p.total_days ?? 0)}`).join(" ")}/>{pts.map((p,i)=><circle key={i} cx={x(i)} cy={y(p.total_days ?? 0)} r="3" fill={color}><title>{label}: {p.total_days?.toFixed(2)} days</title></circle>)}</g>; })}
    {story.lead_time_series.BASELINE.map((p,i)=><text key={p.week} x={x(i)} y={height-13} textAnchor="middle" fill="#64748b" fontSize="10">W{i+1}</text>)}
  </svg>;
}

function Composition({ story }: { story: Story }) {
  const selected = story.lead_time_series.DEMAND_SHOCK_ONLY;
  const max = Math.max(1, ...selected.map(p => p.total_days ?? 0));
  return <div className="composition">{selected.map((p,i)=><div className="comp-row" key={p.week}><span>W{i+1}</span><div className="bar" title={`Processing ${p.processing_days}d · Queue ${p.queue_days}d · Transfer ${p.transfer_days}d`}><i style={{width:`${(p.processing_days ?? 0)/max*100}%`,background:"#2563eb"}}/><i style={{width:`${(p.queue_days ?? 0)/max*100}%`,background:"#f59e0b"}}/><i style={{width:`${(p.transfer_days ?? 0)/max*100}%`,background:"#94a3b8"}}/></div><b>{p.total_days?.toFixed(1)}d</b></div>)}</div>;
}

function ScenarioTable({ story }: { story: Story }) {
  return <div className="table-wrap"><table><thead><tr><th>Scenario</th><th>CAB-100 avg. lead time</th><th>Coverage</th><th>Plant avg. lead time</th><th>Coverage</th></tr></thead><tbody>{CASES.map(([key,label,color])=><tr key={key}><td><i className="dot" style={{background:color}}/>{label}</td><td>{story.lead_time_kpis[key]?.cab100_demand_weighted_days?.toFixed(1) ?? "—"} days</td><td>{story.lead_time_kpis[key]?.cab100_demand_coverage_pct ?? "—"}%</td><td>{story.lead_time_kpis[key]?.plant_demand_weighted_days?.toFixed(1) ?? "—"} days</td><td>{story.lead_time_kpis[key]?.plant_demand_coverage_pct ?? "—"}%</td></tr>)}</tbody></table></div>;
}

export function App() {
  const [story, setStory] = useState<Story | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { fetch(`${API_BASE_URL}/api/executive-story`).then(async r => { if (!r.ok) throw new Error((await r.json()).detail ?? "Could not load story"); return r.json(); }).then(setStory).catch(e => setError(e.message)); }, []);
  const summary = useMemo(() => { if (!story) return null; const last = (key: string) => story.lead_time_series[key].at(-1)?.total_days ?? 0; return { shock: last("DEMAND_SHOCK_ONLY"), capacity: last("CAPACITY_ONLY"), combined: last("COMBINED") }; }, [story]);
  return <main>
    <header><div className="eyebrow">MANUFACTURING ANALYTICS · EXECUTIVE STORY</div><h1>When demand rises, where does flow break?</h1><p>A calibrated CAB-100 demand shock reveals the welding constraint—and shows how capacity and buffer decisions change service risk.</p></header>
    {error && <section className="error"><strong>Story data unavailable</strong><p>{error}. Confirm SQL Server is running and the synthetic dataset has been loaded.</p></section>}
    {!story && !error && <section className="loading">Loading the 12-week scenario comparison…</section>}
    {story && summary && <>
      <section className="metrics"><article><label>Demand scenario</label><strong>+40%</strong><span>CAB-100 finished goods</span></article><article><label>Recovery begins</label><strong>{new Date(story.intervention_start_week).toLocaleDateString(undefined,{month:"short",day:"numeric"})}</strong><span>When overload first appears</span></article><article><label>Week 12 lead time</label><strong>{summary.shock.toFixed(1)} days</strong><span>Shock · {summary.capacity.toFixed(1)}d with capacity recovery</span></article><article><label>Combined response</label><strong>{summary.combined.toFixed(1)} days</strong><span>Capacity recovery plus buffer</span></article></section>
      <section className="panel"><div className="panel-head"><div><div className="eyebrow">FLOW OVER TIME</div><h2>Lead time responds as backlog changes</h2></div><span className="badge">12-week demo horizon</span></div><p className="muted">{story.lead_time_item_scope}. Includes each product's own route and longest manufactured subassembly branch. Historical weeks remain tied to the state that existed then.</p><LineChart story={story}/><div className="legend">{CASES.map(([key,label,color])=><span key={key}><i style={{background:color}}/>{label}</span>)}</div><ScenarioTable story={story}/><p className="note">{story.horizon_rationale}</p></section>
      <div className="two-col"><section className="panel"><div className="eyebrow">LEAD-TIME ANATOMY</div><h2>Queue time is the changing part</h2><div className="legend vertical"><span><i style={{background:"#2563eb"}}/>Processing time</span><span><i style={{background:"#f59e0b"}}/>Queue time</span><span><i style={{background:"#94a3b8"}}/>Transfer time</span></div><Composition story={story}/><p className="note">Shock scenario, decomposed by week. Processing reflects batch-adjusted routing standards; queue uses entry backlog ÷ effective capacity per operating workday, then converts working days to calendar days using the centre's operating days/week.</p></section>
      <section className="panel"><div className="eyebrow">CAPACITY DECISION</div><h2>Translate recovery into hours</h2><p className="muted">Scheduled hours are scaled using the work centre's actual shifts, hours, and operating days. The schedule equivalent is arithmetic; staffing and equipment feasibility need plant validation.</p>{story.capacity_intervention.map(c=><div className="capacity-card" key={c.work_centre_id}><strong>{c.work_centre}</strong><div className="capacity-numbers"><span><b>{c.shifts_per_day} × {c.hours_per_shift}h × {c.days_per_week}</b><small>current scheduled hours/week</small></span><span><b>{c.effective_hours_per_week_current?.toFixed(1) ?? "—"}h</b><small>current effective hours/week</small></span><span><b>{c.effective_hours_per_week_target?.toFixed(1) ?? "—"}h</b><small>target effective hours/week</small></span><span><b>+{c.additional_scheduled_hours_per_week}h/week</b><small>calendar hours to schedule</small></span></div><p>{c.calendar_arrangement} across {c.days_per_week} operating days. The engine applies a {c.target_multiplier.toFixed(2)}× capacity multiplier; this calendar-equivalent is not a feasibility claim.</p></div>)}</section></div>
      <details className="evidence"><summary>Evidence &amp; method</summary><p>Source: {story.evidence.source}. Queue method: {story.evidence.queue_method}. Series report PROCESSING TIME, QUEUE TIME, TRANSFER TIME, and TOTAL LEAD TIME separately.</p><p>Cases: Baseline, demand shock, buffer only, capacity only, and combined.</p></details>
      <footer>All data is synthetic and fictional. Decision support only; validate capacity plans against the plant calendar.</footer>
    </>}
  </main>;
}
