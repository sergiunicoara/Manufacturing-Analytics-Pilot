import { vi } from "vitest";

export const API = "http://localhost:8000";

export function evidence(overrides: Record<string, unknown> = {}) {
  return {
    value: 12.5, provenance: "DERIVED", formula: "sum(load) / capacity",
    inputs: [{ name: "load_hours", value: 100, provenance: "MEASURED", source_record_id: "wc-7" }],
    calculation_trace: ["load = 100 h", "capacity = 8 h"], assumptions: ["Synthetic calendar"],
    ...overrides,
  };
}

export function entry(label: string, value: unknown, extra: Record<string, unknown> = {}) {
  return { label, value, evidence: evidence({ value }), ...extra };
}

export function page(overrides: Record<string, unknown> = {}) {
  return {
    title: "Plant Overview",
    metrics: [entry("Open orders", 42), entry("Late share", 0.125)],
    series: [entry("W1", 10), entry("W2", 14), entry("W3", 12)],
    series_label: "Weekly load", unit: "hours", rows: [], total_rows: 0,
    ...overrides,
  };
}

export type Route = unknown | ((url: URL, init?: RequestInit) => unknown | Promise<unknown>);
export type RouteTable = Record<string, Route>;
export class HttpError { constructor(public status: number, public body: unknown) {} }

/** Replaces fetch with a router keyed on the URL path. A route may return a body, a promise, or throw HttpError. */
export function mockApi(routes: RouteTable) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const route = routes[url.pathname];
    if (route === undefined) return response(404, { detail: `No mock for ${url.pathname}` });
    try {
      const body = typeof route === "function" ? await route(url, init) : route;
      return response(200, body);
    } catch (e) {
      if (e instanceof HttpError) return response(e.status, e.body);
      throw e;
    }
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function response(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

export function calls(fetchMock: ReturnType<typeof mockApi>, path: string) {
  return fetchMock.mock.calls.filter(([url]) => new URL(String(url)).pathname === path);
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(r => { resolve = r; });
  return { promise, resolve };
}

export function leadPoint(total: number, week: string) {
  return { week, processing_days: total * 0.3, queue_days: total * 0.6, transfer_days: total * 0.1,
           total_days: total, evidence: evidence({ value: total, formula: `lead time ${week}` }) };
}

export function leadStory() {
  const keys = ["BASELINE", "DEMAND_SHOCK_ONLY", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"];
  const series = Object.fromEntries(keys.map((key, k) => [key,
    Array.from({ length: 12 }, (_, i) => leadPoint(2 + k + i * 0.25, `2026-01-${String(i + 1).padStart(2, "0")}`))]));
  return {
    horizon_rationale: "Twelve weeks keep the shock and recovery visible.", lead_time_series: series,
    capacity_intervention: [{ work_centre: "Welding", scheduled_hours_per_week: 80, effective_hours_per_week_current: 60,
      effective_hours_per_week_target: 72, target_multiplier: 1.2, additional_scheduled_hours_per_week: 16,
      calendar_arrangement: "Add one Saturday shift" }],
  };
}

export function storyPayload() {
  return {
    horizon_rationale: "Twelve-week horizon rationale.",
    beats: [
      { title: "Demand rises", narration: "Demand steps up by forty percent.", evidence: evidence({ formula: "beat one method" }) },
      { title: "Welding binds", narration: "Welding becomes the constraint.", evidence: evidence({ formula: "beat two method" }) },
    ],
  };
}
