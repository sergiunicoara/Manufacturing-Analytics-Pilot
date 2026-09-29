import { useEffect, useRef, useState } from "react";
import Plotly from "plotly.js-dist-min";
import "./dashboard.css";
import "./responsive.css";
import { ExecutiveStoryVisuals } from "./ExecutiveStoryVisuals";

const API = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const PAGES = [
  ["plant-overview", "Plant Overview", "▦"], ["demand-forecast", "Demand & Forecast", "⌁"],
  ["production-flow", "Production Flow", "⇢"], ["bom-explorer", "BOM Explorer", "⌘"],
  ["capacity", "Capacity", "◫"], ["wip-lead-time", "WIP & Lead Time", "◷"],
  ["scenario-lab", "Scenario Lab", "◈"], ["data-quality", "Data Quality", "◇"],
  ["recommendation", "Recommendation", "✦"], ["stage-performance", "Stage Performance", "⏱"],
  ["decision-economics", "Decision Economics", "€"], ["planning-policy", "Planning Policy", "⚙"],
] as const;
type Evidence = { value: unknown; provenance: string; formula: string; inputs: Array<{name: string; value: unknown; provenance: string; source_record_id: string | null}>; calculation_trace: string[]; assumptions: string[]; units?: string; time_scope?: string; coverage?: Record<string, unknown>; exclusions?: string[]; data_origin?: string };
type Entry = { label: string; value: unknown; evidence: Evidence; [key: string]: unknown };
type Page = { title: string; metrics: Entry[]; series: Entry[]; series_label?: string; unit?: string; rows: Entry[]; total_rows?: number; error?: string };
type Story = { beats: Array<{title: string; narration: string; evidence: Evidence}>; horizon_rationale: string };

function format(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString(undefined, {maximumFractionDigits: 2});
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function EvidenceDrawer({ evidence, onClose }: { evidence: Evidence | null; onClose: () => void }) {
  if (!evidence) return null;
  return <><div className="drawer-shade" onClick={onClose}/><aside className="drawer" role="dialog" aria-modal="true" aria-label="Evidence details">
    <div className="drawer-head"><div><span className="eyebrow">AUDIT TRAIL</span><h2>Evidence</h2></div><button onClick={onClose} aria-label="Close evidence">×</button></div>
    <div className="drawer-value">{format(evidence.value)}</div><span className="provenance">{evidence.provenance}</span>{evidence.data_origin && <span className="provenance origin">{evidence.data_origin} DATA</span>}
    {(evidence.units || evidence.time_scope) && <p className="scope-line">{[evidence.units, evidence.time_scope].filter(Boolean).join(" · ")}</p>}
    <h3>Method</h3><p>{evidence.formula}</p>
    <h3>Inputs</h3><div className="input-list">{evidence.inputs.length ? evidence.inputs.map((input, i) => <div key={i}><strong>{input.name}</strong><span>{format(input.value)}</span><small>{input.provenance}{input.source_record_id ? ` · ${input.source_record_id}` : ""}</small></div>) : <p>No inputs recorded.</p>}</div>
    <h3>Calculation trace</h3><ol>{evidence.calculation_trace.length ? evidence.calculation_trace.map((step,i)=><li key={i}>{step}</li>) : <li>See the method reference and source inputs.</li>}</ol>
    {evidence.coverage && <><h3>Coverage</h3><div className="input-list">{Object.entries(evidence.coverage).map(([k,v])=><div key={k}><strong>{k.replace(/_/g," ")}</strong><span>{format(v)}</span></div>)}</div></>}
    {evidence.exclusions && evidence.exclusions.length > 0 && <><h3>Exclusions</h3><ul>{evidence.exclusions.map((x,i)=><li key={i}>{x}</li>)}</ul></>}
    <h3>Assumptions</h3><ul>{evidence.assumptions.length ? evidence.assumptions.map((a,i)=><li key={i}>{a}</li>) : <li>No additional assumptions recorded.</li>}</ul>
  </aside></>;
}

function EvidenceChart({ points, title, unit, onEvidence, bars = false }: {points: Entry[]; title: string; unit: string; onEvidence: (e: Evidence)=>void; bars?: boolean}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !points.length || !points.every(p => typeof p.value === "number")) return;
    const node = ref.current;
    Plotly.newPlot(node, [{ x: points.map(p=>p.label), y: points.map(p=>p.value as number),
      type: bars ? "bar" : "scatter", mode: bars ? undefined : "lines+markers",
      line: { color: "#315cc7", width: 3 }, marker: { color: points.map((_,i)=>i===points.length-1 ? "#e17148" : "#315cc7"), size: 7 },
      hovertemplate: `%{x}<br>%{y:.2f} ${unit}<extra></extra>` }],
      { title: {text: title, font: {size: 14, color: "#34455e"}}, margin: {l: 62,r: 18,t: 54,b: 68},
        paper_bgcolor: "transparent", plot_bgcolor: "transparent", font: {family: "Inter, system-ui, sans-serif", color: "#5a6880"},
        yaxis: {title: {text: unit}, gridcolor: "#e7edf4", zeroline: false}, xaxis: {tickangle: points.length > 14 ? -45 : 0},
        showlegend: false }, {responsive: true, displayModeBar: false});
    const handler = (event: {points: Array<{pointIndex: number}>}) => { const p = points[event.points[0]?.pointIndex]; if (p?.evidence) onEvidence(p.evidence); };
    const plotNode = node as HTMLDivElement & {on?: (name:string, callback:typeof handler)=>void; removeAllListeners?: (name:string)=>void};
    plotNode.on?.("plotly_click", handler);
    return () => { plotNode.removeAllListeners?.("plotly_click"); Plotly.purge(node); };
  }, [points, title, unit, onEvidence, bars]);
  return <div ref={ref} className="plot" aria-label={title}/>;
}

function ScenarioControls({ onResult }: {onResult: (result: {metrics: Entry[]; run_id: number})=>void}) {
  const [demand, setDemand] = useState(1.4), [buffer, setBuffer] = useState(0), [capacity, setCapacity] = useState(1);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function run() {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${API}/api/scenarios/run`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:`UI ${new Date().toISOString()}`,demand_multiplier:demand,buffer_boost_per_item:buffer,capacity_multiplier:capacity,intervention_start_week:4})});
      const payload = await response.json(); if (!response.ok) throw new Error(payload.detail ?? "Scenario failed"); onResult(payload);
    } catch (e) {setError(String(e));} finally {setBusy(false);}
  }
  return <div className="controls"><h3>Run a persisted scenario</h3><div className="control-grid"><label>Demand multiplier<input type="number" min="0.5" max="3" step="0.1" value={demand} onChange={e=>setDemand(Number(e.target.value))}/></label><label>Buffer units per item<input type="number" min="0" max="10000" step="10" value={buffer} onChange={e=>setBuffer(Number(e.target.value))}/></label><label>Capacity multiplier<input type="number" min="1" max="3" step="0.1" value={capacity} onChange={e=>setCapacity(Number(e.target.value))}/></label></div><button onClick={run} disabled={busy}>{busy ? "Running…" : "Run & save"}</button>{error && <p className="error-text">{error}</p>}</div>;
}

function Copilot({ onEvidence }: {onEvidence: (e: Evidence)=>void}) {
  const [question, setQuestion] = useState("Why does capacity matter after the demand shock?");
  const [answer, setAnswer] = useState(""); const [payload, setPayload] = useState<unknown>(null); const [busy,setBusy]=useState(false);
  async function ask() {setBusy(true); try {const r=await fetch(`${API}/copilot/ask`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question})}); const data=await r.json(); if(!r.ok) throw new Error(data.detail); setAnswer(data.answer);setPayload(data.evidence);} catch(e){setAnswer(String(e));} finally{setBusy(false);}}
  return <section className="copilot"><div className="eyebrow">EVIDENCE-GATED COPILOT</div><h2>Ask about the model</h2><div className="ask-row"><input value={question} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>e.key==="Enter"&&ask()} aria-label="Question"/><button onClick={ask} disabled={busy}>{busy?"Checking…":"Ask"}</button></div>{answer && <><p>{answer}</p><details><summary>Raw deterministic evidence</summary><pre>{JSON.stringify(payload,null,2)}</pre></details></>}</section>;
}

const RECORD_CLASSES = ["COMPLETED_VALID","COMPLETED_MISSING_START","COMPLETED_MISSING_FINISH","INVALID_ORDERING","STATUS_CONFLICT","DUPLICATE","DQ_EXCLUDED","OPEN_STARTED","NOT_STARTED","OUTSIDE_WINDOW"];
type StageRecord = {po_operation_id:number;production_order_id:number;seq_no:number;work_centre_id:number;item_id:number;status:string;actual_start:string|null;actual_finish:string|null;record_class:string;elapsed_hours:number|null;standard_processing_hours:number|null};

function StageRecords() {
  const [wc,setWc]=useState(""), [cls,setCls]=useState("OPEN_STARTED"), [offset,setOffset]=useState(0);
  const [data,setData]=useState<{total:number;records:StageRecord[]}|null>(null), [error,setError]=useState(""), [busy,setBusy]=useState(false);
  useEffect(()=>{let active=true;setBusy(true);setError("");const q=new URLSearchParams({offset:String(offset),limit:"25"});if(wc)q.set("work_centre_id",wc);if(cls)q.set("record_class",cls);
    fetch(`${API}/api/stage-performance/records?${q}`).then(async r=>{const p=await r.json();if(!r.ok)throw new Error(p.detail??"Records unavailable");return p;}).then(p=>{if(active)setData(p);}).catch(e=>{if(active)setError(String(e));}).finally(()=>{if(active)setBusy(false);});return()=>{active=false;};},[wc,cls,offset]);
  return <section className="content-card"><div className="card-heading"><div><span className="eyebrow">UNDERLYING RECORDS</span><h2>Inspect operations by record class</h2></div><span className="hint">{data?`${data.total} matching operations`:""}</span></div>
    <div className="control-grid"><label>Record class<select value={cls} onChange={e=>{setCls(e.target.value);setOffset(0);}}><option value="">All</option>{RECORD_CLASSES.map(c=><option key={c}>{c}</option>)}</select></label><label>Work centre id<input type="number" min="1" value={wc} onChange={e=>{setWc(e.target.value);setOffset(0);}} placeholder="all"/></label></div>
    {error&&<p className="error-text">{error}</p>}{busy&&<p className="hint">Loading records…</p>}
    {data&&!busy&&<div className="table-scroll"><table className="record-table"><thead><tr><th>Op</th><th>Order</th><th>Seq</th><th>WC</th><th>Item</th><th>Status</th><th>Actual start</th><th>Actual finish</th><th>Class</th><th>Recorded h</th><th>Standard h</th></tr></thead><tbody>{data.records.map(r=><tr key={r.po_operation_id}><td>{r.po_operation_id}</td><td>{r.production_order_id}</td><td>{r.seq_no}</td><td>{r.work_centre_id}</td><td>{r.item_id}</td><td>{r.status}</td><td>{r.actual_start?.slice(0,10)??"—"}</td><td>{r.actual_finish?.slice(0,10)??"—"}</td><td>{r.record_class}</td><td>{format(r.elapsed_hours)}</td><td>{format(r.standard_processing_hours)}</td></tr>)}</tbody></table>
      {data.records.length===0&&<p className="hint">No operations in this class.</p>}
      <div className="pager"><button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-25))}>← Previous</button><button disabled={offset+25>=data.total} onClick={()=>setOffset(offset+25)}>Next →</button></div></div>}
  </section>;
}

export function DashboardApp() {
  const [slug,setSlug]=useState<string>("plant-overview"), [page,setPage]=useState<Page|null>(null), [story,setStory]=useState<Story|null>(null);
  const [evidence,setEvidence]=useState<Evidence|null>(null), [error,setError]=useState(""), [loading,setLoading]=useState(false);
  const [scenarioResult,setScenarioResult]=useState<{metrics:Entry[];run_id:number}|null>(null);
  useEffect(()=>{let active=true;setLoading(true);setError("");setPage(null); const url=slug==="story"?`${API}/story`:`${API}/api/pages/${slug}`;fetch(url).then(async r=>{const p=await r.json();if(!r.ok)throw new Error(p.detail??"API unavailable");return p;}).then(p=>{if(active){if(slug==="story")setStory(p);else setPage(p);}}).catch(e=>{if(active)setError(String(e));}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[slug]);
  const choose = (next:string) => {setEvidence(null);setSlug(next);};
  const openEvidence=(next:Evidence)=>setEvidence(next);
  const bars=["capacity","scenario-lab","demand-forecast","stage-performance","decision-economics"].includes(slug);
  return <div className="app-shell"><aside className="sidebar"><div className="brand"><span className="brand-mark">M</span><div><strong>Manufacturing<br/>Analytics</strong><small>Decision pilot</small></div></div><div className="nav-caption">WORKSPACE</div><nav>{PAGES.map(([id,name,icon])=><button key={id} onClick={()=>choose(id)} className={slug===id?"selected":""}><span>{icon}</span>{name}</button>)}</nav><div className="nav-caption">PRESENT</div><button className={`story-link ${slug==="story"?"selected":""}`} onClick={()=>choose("story")}>▶ Executive Story</button><div className="side-foot">Synthetic data · 12-week story<br/>Evidence attached to each result</div></aside>
    <main className="dashboard-main"><div className="topbar"><span>OPERATIONS INTELLIGENCE / {slug==="story"?"EXECUTIVE STORY":page?.title?.toUpperCase()??"LOADING"}</span><span className="live-pill">● Synthetic pilot</span></div>
      <header className="dashboard-header"><div className="eyebrow">DECISION SUPPORT · WEEKLY PLANNING</div><h1>{slug==="story"?"Executive Story":page?.title??"Loading analytics"}</h1><p>{slug==="story"?"Follow the demand shock, physical constraint, and intervention response.":"Select any result to inspect its method, inputs, and assumptions."}</p></header>
      {loading&&<div className="state-card">Computing the scenario comparison and evidence…</div>}{error&&<div className="state-card error-text">{error}</div>}
      {slug==="story"&&story&&!loading&&<><ExecutiveStoryVisuals onEvidence={openEvidence}/><div className="story-note">{story.horizon_rationale}</div><div className="story-grid">{story.beats.map((beat,i)=><button key={i} className="story-beat" onClick={()=>openEvidence(beat.evidence)}><span>0{i+1}</span><h2>{beat.title}</h2><p>{beat.narration}</p><small>View evidence →</small></button>)}</div><button className="primary-link" onClick={()=>choose("scenario-lab")}>Explore the scenarios →</button></>}
      {page&&!loading&&<><div className="metric-grid">{page.metrics.map((m,i)=><button className="metric-card" key={i} onClick={()=>openEvidence(m.evidence)}><span>{m.label}</span><strong>{format(m.value)}</strong><small>View evidence ↗</small></button>)}</div>
        {page.series.length>0&&<section className="content-card"><div className="card-heading"><div><span className="eyebrow">TIME SERIES / COMPARISON</span><h2>{page.series_label}</h2></div><span className="hint">Click a point for evidence</span></div><EvidenceChart points={page.series} title={page.series_label??page.title} unit={page.unit??""} onEvidence={openEvidence} bars={bars}/></section>}
        {slug==="scenario-lab"&&<><ScenarioControls onResult={setScenarioResult}/>{scenarioResult&&<button className="metric-card scenario-result" onClick={()=>openEvidence(scenarioResult.metrics[0].evidence)}><span>Saved run #{scenarioResult.run_id}</span><strong>{format(scenarioResult.metrics[0].value)} backlog hours</strong><small>View evidence ↗</small></button>}</>}
        {page.rows.length>0&&<section className="content-card"><div className="card-heading"><div><span className="eyebrow">AUDITABLE DETAIL</span><h2>{slug==="recommendation"?"Analytical buffer recommendations":"Result detail"}</h2></div><span className="hint">{page.total_rows??page.rows.length} rows · click to inspect</span></div><div className="rows">{page.rows.map((row,i)=><button key={i} onClick={()=>openEvidence(row.evidence)}><span><strong>{format(row.label??row.item_code)}</strong>{row.reason&&<small>{format(row.reason)}</small>}{row.classification&&<small>{format(row.classification)}</small>}{row.records_affected_pct!==undefined&&<small>{format(row.unique_records)} unique records · {format(row.records_affected_pct)}% of the table · {format(row.origin)} · scope {format(row.impact_scope)}</small>}{row.wip_records!==undefined&&<small>{format(row.wip_records)} WIP records · {format(row.wip_qty)} units · oldest {format(row.max_age_days)} days</small>}{row.delta_intervention_cost!==undefined&&<small>Buffer capital {format(row.buffer_capital)} · carrying {format(row.inventory_carrying_cost)} · added hours {format(row.intervention_cost)} · WIP carrying unavailable · residual backlog {format(row.ending_backlog_hours)} h (Δ {format(row.delta_ending_backlog_hours)} h vs shock){row.run_id!=null?` · run #${row.run_id}`:""}</small>}{row.confidence!==undefined&&row.policy!==undefined&&<small>Confidence {format(row.confidence)}</small>}{row.recorded_p95_hours!==undefined&&<small>Recorded P85 {format(row.recorded_p85_hours)} h · P95 {format(row.recorded_p95_hours)} h · n={format(row.n_observations)} · coverage {format(row.coverage_pct)}% — Modelled routing standard {format(row.modelled_standard_p50_hours)} h</small>}</span><b>{row.recommended_min!==undefined?`${format(row.recommended_min)}–${format(row.recommended_max)}`:format(row.value)}</b><i>↗</i></button>)}</div></section>}
        {slug==="data-quality"&&<div className="story-note">Rows group the findings by rule, origin, classification and impact scope. The share of findings marked blocking is not the share of unusable data: each group shows how many unique records it touches and what share of that source table they are. <a href={`${API}/api/dq/findings.csv`} download>Export all findings (CSV)</a></div>}
        {slug==="decision-economics"&&<div className="story-note">Headline = period expense (inventory carrying cost + added paid hours). Buffer capital is a balance tied up at standard cost and is shown separately, never added to the expense. WIP carrying cost is unavailable because engine WIP mixes items. No ROI or profit figure is computed. Rates are configurable assumptions; the data is synthetic.</div>}
        {slug==="planning-policy"&&<div className="story-note">Policy (where to hold stock) is separate from buffer sizing (how much, see Recommendation). Heuristic for planner review; not a DDMRP implementation and no ERP write-back. Download the review package: <a href={`${API}/api/parameters/package.json`} download>JSON</a> · <a href={`${API}/api/parameters/package.csv`} download>CSV</a> · <a href={`${API}/api/parameters/schema.json`} target="_blank" rel="noreferrer">schema</a>. Every native M3 field is unmapped until verified.</div>}
        {slug==="stage-performance"&&<><div className="story-note"><strong>Recorded</strong> = actual finish − actual start from production order operations (synthetic timestamps; resolution stated in each evidence drawer). It is elapsed calendar time, not productive processing time. <strong>Modelled</strong> = routing-standard processing hours for the same operations. Unfinished, invalid and DQ-excluded operations are counted separately, never as zero.</div><StageRecords/></>}
        {slug==="recommendation"&&<div className="story-note">Recommendations are analytical ranges upstream of candidate or primary constraints. Confirm replenishment time and practical placement with the plant team.</div>}
        <Copilot onEvidence={openEvidence}/></>}
      <footer>Fictional manufacturing data. Capacity arrangements and service risk are analytical estimates.</footer>
    </main><EvidenceDrawer evidence={evidence} onClose={()=>setEvidence(null)}/></div>;
}
