import { useEffect, useRef, useState } from "react";
import Plotly from "plotly.js-dist-min";
import "./story.css";

const API = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
type Evidence = { value: unknown; provenance: string; formula: string; inputs: Array<{name:string;value:unknown;provenance:string;source_record_id:string|null}>; calculation_trace:string[]; assumptions:string[] };
type LeadPoint = {week:string;processing_days:number;queue_days:number;transfer_days:number;total_days:number;evidence:Evidence};
type LeadStory = {horizon_rationale:string;lead_time_series:Record<string,LeadPoint[]>;capacity_intervention:Array<{work_centre:string;scheduled_hours_per_week:number;effective_hours_per_week_current:number;effective_hours_per_week_target:number;target_multiplier:number;additional_scheduled_hours_per_week:number;calendar_arrangement:string}>};
const CASES = [["BASELINE","Baseline","#6d7d92"],["DEMAND_SHOCK_ONLY","Demand shock","#d55750"],["BUFFER_ONLY","Buffer only","#d09a3c"],["CAPACITY_ONLY","Capacity only","#315cc7"],["COMBINED","Combined","#328c76"]] as const;

export function ExecutiveStoryVisuals({onEvidence}: {onEvidence:(e:Evidence)=>void}) {
  const [data,setData]=useState<LeadStory|null>(null),[error,setError]=useState("");
  const lineRef=useRef<HTMLDivElement>(null), stackRef=useRef<HTMLDivElement>(null);
  useEffect(()=>{let active=true;fetch(`${API}/api/executive-story`).then(async r=>{const p=await r.json();if(!r.ok)throw new Error(p.detail??"Lead-time data unavailable");return p;}).then(p=>{if(active)setData(p);}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[]);
  useEffect(()=>{if(!data||!lineRef.current)return;const node=lineRef.current;
    Plotly.newPlot(node,CASES.map(([key,label,color])=>({x:data.lead_time_series[key].map((p,i)=>`W${i+1}`),y:data.lead_time_series[key].map(p=>p.total_days),type:"scatter",mode:"lines+markers",name:label,line:{color,width:3},marker:{size:6},hovertemplate:`${label}<br>%{x}: %{y:.2f} days<extra></extra>`})),
      {margin:{l:55,r:20,t:18,b:50},paper_bgcolor:"transparent",plot_bgcolor:"transparent",font:{family:"Inter, system-ui",color:"#607086",size:11},yaxis:{title:{text:"Calendar days"},gridcolor:"#e7edf4"},legend:{orientation:"h",y:-.22},showlegend:true},
      {responsive:true,displayModeBar:false});
    const plotNode=node as HTMLDivElement&{on?:(name:string,handler:(e:{points:Array<{curveNumber:number;pointIndex:number}>})=>void)=>void;removeAllListeners?:(name:string)=>void};
    plotNode.on?.("plotly_click",e=>{const key=CASES[e.points[0]?.curveNumber]?.[0];const p=key&&data.lead_time_series[key][e.points[0]?.pointIndex];if(p)onEvidence(p.evidence);});
    return()=>{plotNode.removeAllListeners?.("plotly_click");Plotly.purge(node);};
  },[data,onEvidence]);
  useEffect(()=>{if(!data||!stackRef.current)return;const node=stackRef.current;const points=data.lead_time_series.DEMAND_SHOCK_ONLY;
    Plotly.newPlot(node,[["processing_days","Processing","#315cc7"],["queue_days","Queue","#d09a3c"],["transfer_days","Transfer","#91a0b2"]].map(([field,label,color])=>({x:points.map((_,i)=>`W${i+1}`),y:points.map(p=>p[field as keyof LeadPoint]),type:"bar",name:label,marker:{color}})),
      {barmode:"stack",margin:{l:55,r:20,t:18,b:50},paper_bgcolor:"transparent",plot_bgcolor:"transparent",font:{family:"Inter, system-ui",color:"#607086",size:11},yaxis:{title:{text:"Calendar days"},gridcolor:"#e7edf4"},legend:{orientation:"h",y:-.22}},
      {responsive:true,displayModeBar:false});
    const plotNode=node as HTMLDivElement&{on?:(name:string,handler:(e:{points:Array<{pointIndex:number}>})=>void)=>void;removeAllListeners?:(name:string)=>void};
    plotNode.on?.("plotly_click",e=>{const p=points[e.points[0]?.pointIndex];if(p)onEvidence(p.evidence);});
    return()=>{plotNode.removeAllListeners?.("plotly_click");Plotly.purge(node);};
  },[data,onEvidence]);
  if(error)return <div className="state-card error-text">{error}</div>;
  if(!data)return <div className="state-card">Calculating the five-case, BOM-aware lead-time comparison…</div>;
  return <><div className="metric-grid story-metrics">{CASES.map(([key,label,color])=><button key={key} className="metric-card" onClick={()=>onEvidence(data.lead_time_series[key].at(-1)!.evidence)}><span><i className="case-dot" style={{background:color}}/>{label}</span><strong>{data.lead_time_series[key].at(-1)?.total_days.toFixed(1)}d</strong><small>Week 12 · click for evidence</small></button>)}</div>
    <section className="content-card"><div className="card-heading"><div><span className="eyebrow">FIVE PHYSICAL FUTURES</span><h2>Lead time changes with backlog</h2></div><span className="hint">Click a point for evidence</span></div><p className="chart-context">Each week's estimate uses the backlog already ahead when the order enters its finished-good and subassembly routes. Historical points remain fixed.</p><div className="story-plot" ref={lineRef}/><p className="chart-context">{data.horizon_rationale}</p></section>
    <div className="story-two"><section className="content-card"><span className="eyebrow">LEAD-TIME ANATOMY</span><h2>Queue time carries the shock</h2><p className="chart-context">Demand shock case: processing + queue + transfer = total lead time.</p><div className="story-plot" ref={stackRef}/></section><section className="content-card"><span className="eyebrow">CAPACITY TRANSLATION</span><h2>From multiplier to hours</h2>{data.capacity_intervention.map((c,i)=><div key={i} className="operation-card"><strong>{c.work_centre}</strong><div className="operation-grid"><span>Current calendar<b>{c.scheduled_hours_per_week.toFixed(1)}h/week</b></span><span>Current effective<b>{c.effective_hours_per_week_current?.toFixed(1)}h/week</b></span><span>Target effective<b>{c.effective_hours_per_week_target?.toFixed(1)}h/week</b></span><span>Added calendar<b>{c.additional_scheduled_hours_per_week.toFixed(1)}h/week</b></span></div><p>{`${c.calendar_arrangement}. Equivalent at unchanged availability for ${c.target_multiplier.toFixed(2)}×. Staffing and equipment feasibility require validation.`}</p></div>)}</section></div>
  </>;
}
