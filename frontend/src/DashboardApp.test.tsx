import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Plotly from "plotly.js-dist-min";
import { describe, expect, it, vi } from "vitest";
import { DashboardApp } from "./DashboardApp";
import { plotCalls } from "./test/setup";
import { API, HttpError, calls, deferred, entry, evidence, leadStory, mockApi, page, storyPayload } from "./test/fixtures";

const NAV_PAGES = [
  ["plant-overview", "Plant Overview"], ["demand-forecast", "Demand & Forecast"], ["production-flow", "Production Flow"],
  ["bom-explorer", "BOM Explorer"], ["capacity", "Capacity"], ["wip-lead-time", "WIP & Lead Time"],
  ["scenario-lab", "Scenario Lab"], ["data-quality", "Data Quality"], ["recommendation", "Recommendation"],
  ["stage-performance", "Stage Performance"], ["decision-economics", "Decision Economics"], ["planning-policy", "Planning Policy"],
  ["shop-floor-flow", "Shop Floor Flow"], ["order-change-impact", "Order Change Impact"],
] as const;

function allPages() {
  return Object.fromEntries(NAV_PAGES.map(([slug, title]) => [`/api/pages/${slug}`, page({ title })]));
}

function setup(routes: Record<string, unknown> = {}) {
  const fetchMock = mockApi({ ...allPages(), ...routes });
  return { fetchMock, user: userEvent.setup() };
}

const navButton = (name: RegExp) => within(screen.getByRole("navigation")).getByRole("button", { name });
const heading = (name: string) => screen.findByRole("heading", { level: 1, name });
const lastPlot = () => plotCalls[plotCalls.length - 1];
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\&]/g, "\\$&");

describe("loading and errors", () => {
  it("shows a loading state, then the first page with its metrics", async () => {
    const pending = deferred<unknown>();
    setup({ "/api/pages/plant-overview": () => pending.promise });
    render(<DashboardApp />);
    expect(screen.getByText(/Computing the scenario comparison/)).toBeInTheDocument();
    await act(async () => { pending.resolve(page()); });
    expect(await heading("Plant Overview")).toBeInTheDocument();
    expect(screen.queryByText(/Computing the scenario comparison/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open orders.*42/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Late share.*0\.13/ })).toBeInTheDocument();
  });

  it("requests the first page from the configured API", async () => {
    const { fetchMock } = setup();
    render(<DashboardApp />);
    await heading("Plant Overview");
    expect(fetchMock.mock.calls[0][0]).toBe(`${API}/api/pages/plant-overview`);
  });

  it("shows the API's error detail instead of the page", async () => {
    setup({ "/api/pages/plant-overview": () => { throw new HttpError(503, { detail: "Schema is out of date" }); } });
    render(<DashboardApp />);
    expect(await screen.findByText(/Schema is out of date/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Loading analytics" })).toBeInTheDocument();
    expect(screen.queryByText("Open orders")).not.toBeInTheDocument();
  });

  it("falls back to a generic message when the error has no detail", async () => {
    setup({ "/api/pages/plant-overview": () => { throw new HttpError(500, {}); } });
    render(<DashboardApp />);
    expect(await screen.findByText(/API unavailable/)).toBeInTheDocument();
  });

  it("shows an error when the request itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<DashboardApp />);
    expect(await screen.findByText(/Failed to fetch/)).toBeInTheDocument();
  });
});

describe("navigation", () => {
  it("offers all fourteen pages plus the executive story", async () => {
    setup();
    render(<DashboardApp />);
    await heading("Plant Overview");
    expect(within(screen.getByRole("navigation")).getAllByRole("button")).toHaveLength(14);
    expect(screen.getByRole("button", { name: /Executive Story/ })).toBeInTheDocument();
  });

  it.each(NAV_PAGES)("opens %s and fetches its own endpoint", async (slug, title) => {
    const { fetchMock, user } = setup();
    render(<DashboardApp />);
    await heading("Plant Overview");
    await user.click(navButton(new RegExp(escape(title))));
    expect(await heading(title)).toBeInTheDocument();
    expect(calls(fetchMock, `/api/pages/${slug}`).length).toBeGreaterThan(0);
  });

  it("marks the current page as selected", async () => {
    const { user } = setup();
    render(<DashboardApp />);
    await heading("Plant Overview");
    expect(navButton(/Plant Overview/)).toHaveClass("selected");
    await user.click(navButton(/Capacity/));
    await heading("Capacity");
    expect(navButton(/Capacity/)).toHaveClass("selected");
    expect(navButton(/Plant Overview/)).not.toHaveClass("selected");
  });

  it("ignores a slow response for a page the user already left", async () => {
    const slow = deferred<unknown>();
    const { user } = setup({ "/api/pages/plant-overview": () => slow.promise });
    render(<DashboardApp />);
    await user.click(navButton(/Capacity/));
    await heading("Capacity");
    await act(async () => { slow.resolve(page({ title: "Plant Overview" })); });
    expect(screen.getByRole("heading", { level: 1, name: "Capacity" })).toBeInTheDocument();
  });

  it("closes an open evidence drawer when the page changes", async () => {
    const { user } = setup();
    render(<DashboardApp />);
    await user.click(await screen.findByRole("button", { name: /Open orders/ }));
    expect(screen.getByRole("dialog", { name: "Evidence details" })).toBeInTheDocument();
    await user.click(navButton(/Capacity/));
    await heading("Capacity");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("evidence drawer", () => {
  async function openDrawer(ev: ReturnType<typeof evidence>) {
    const { user } = setup({ "/api/pages/plant-overview": page({ metrics: [{ label: "Open orders", value: 42, evidence: ev }] }) });
    render(<DashboardApp />);
    await user.click(await screen.findByRole("button", { name: /Open orders/ }));
    return { user, drawer: within(screen.getByRole("dialog", { name: "Evidence details" })) };
  }

  it("shows value, provenance, method, inputs, trace and assumptions", async () => {
    const { drawer } = await openDrawer(evidence({ value: 1234.567 }));
    expect(drawer.getByText("1,234.57")).toBeInTheDocument();
    expect(drawer.getByText("DERIVED")).toBeInTheDocument();
    expect(drawer.getByText("sum(load) / capacity")).toBeInTheDocument();
    expect(drawer.getByText("load_hours")).toBeInTheDocument();
    expect(drawer.getByText(/MEASURED · wc-7/)).toBeInTheDocument();
    expect(drawer.getByText("load = 100 h")).toBeInTheDocument();
    expect(drawer.getByText("Synthetic calendar")).toBeInTheDocument();
  });

  it("labels synthetic data and shows units, time scope, coverage and exclusions", async () => {
    const { drawer } = await openDrawer(evidence({
      data_origin: "SYNTHETIC", units: "hours", time_scope: "12 weeks",
      coverage: { valid_observations: 35, share_pct: 61.5 }, exclusions: ["Open operations"] }));
    expect(drawer.getByText("SYNTHETIC DATA")).toBeInTheDocument();
    expect(drawer.getByText("hours · 12 weeks")).toBeInTheDocument();
    expect(drawer.getByText("valid observations")).toBeInTheDocument();
    expect(drawer.getByText("61.5")).toBeInTheDocument();
    expect(drawer.getByText("Open operations")).toBeInTheDocument();
  });

  it("states plainly when there are no inputs, trace or assumptions", async () => {
    const { drawer } = await openDrawer(evidence({ inputs: [], calculation_trace: [], assumptions: [] }));
    expect(drawer.getByText("No inputs recorded.")).toBeInTheDocument();
    expect(drawer.getByText(/See the method reference/)).toBeInTheDocument();
    expect(drawer.getByText("No additional assumptions recorded.")).toBeInTheDocument();
    expect(drawer.queryByText("Coverage")).not.toBeInTheDocument();
    expect(drawer.queryByText("Exclusions")).not.toBeInTheDocument();
  });

  it("renders missing and object values without crashing", async () => {
    const { drawer } = await openDrawer(evidence({ value: null, inputs: [{ name: "obj", value: { a: 1 }, provenance: "ASSUMED", source_record_id: null }] }));
    expect(drawer.getByText("—")).toBeInTheDocument();
    expect(drawer.getByText('{"a":1}')).toBeInTheDocument();
  });

  it("closes with the × button and with a click on the backdrop", async () => {
    const { user } = await openDrawer(evidence());
    await user.click(screen.getByRole("button", { name: "Close evidence" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Open orders/ }));
    await user.click(document.querySelector(".drawer-shade") as HTMLElement);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("charts", () => {
  it("draws the series as a line chart with the last point highlighted", async () => {
    setup();
    render(<DashboardApp />);
    await waitFor(() => expect(plotCalls.length).toBeGreaterThan(0));
    const { data, layout } = lastPlot();
    expect(data[0]).toMatchObject({ type: "scatter", mode: "lines+markers", x: ["W1", "W2", "W3"], y: [10, 14, 12] });
    expect(data[0].marker.color).toEqual(["#315cc7", "#315cc7", "#e17148"]);
    expect(layout.title.text).toBe("Weekly load");
    expect(layout.yaxis.title.text).toBe("hours");
  });

  it("uses bars on comparison pages", async () => {
    const { user } = setup();
    render(<DashboardApp />);
    await heading("Plant Overview");
    await user.click(navButton(/Capacity/));
    await heading("Capacity");
    await waitFor(() => expect(lastPlot().data[0].type).toBe("bar"));
  });

  it("does not plot a series with non-numeric values", async () => {
    setup({ "/api/pages/plant-overview": page({ series: [entry("W1", 10), entry("W2", null)] }) });
    render(<DashboardApp />);
    await heading("Plant Overview");
    expect(plotCalls).toHaveLength(0);
  });

  it("omits the chart card when the page has no series", async () => {
    setup({ "/api/pages/plant-overview": page({ series: [] }) });
    render(<DashboardApp />);
    await heading("Plant Overview");
    expect(screen.queryByText("Click a point for evidence")).not.toBeInTheDocument();
  });

  it("opens the evidence of the clicked point", async () => {
    setup({ "/api/pages/plant-overview": page({ series: [
      { label: "W1", value: 10, evidence: evidence({ formula: "point one" }) },
      { label: "W2", value: 14, evidence: evidence({ formula: "point two" }) }] }) });
    render(<DashboardApp />);
    await waitFor(() => expect(plotCalls.length).toBeGreaterThan(0));
    act(() => lastPlot().handlers.plotly_click({ points: [{ pointIndex: 1 }] }));
    expect(within(screen.getByRole("dialog")).getByText("point two")).toBeInTheDocument();
  });

  it("does not redraw the chart when the evidence drawer opens or closes", async () => {
    const { user } = setup();
    render(<DashboardApp />);
    await waitFor(() => expect(plotCalls.length).toBeGreaterThan(0));
    const drawn = plotCalls.length;
    await user.click(screen.getByRole("button", { name: /Open orders/ }));
    await user.click(screen.getByRole("button", { name: "Close evidence" }));
    expect(plotCalls).toHaveLength(drawn);
  });

  it("purges the plot when the page unmounts", async () => {
    setup();
    const { unmount } = render(<DashboardApp />);
    await waitFor(() => expect(plotCalls.length).toBeGreaterThan(0));
    unmount();
    expect(Plotly.purge).toHaveBeenCalled();
  });
});

describe("detail rows", () => {
  it("lists rows with their counts and opens a row's evidence", async () => {
    const { user } = setup({ "/api/pages/recommendation": page({ title: "Recommendation", total_rows: 40, rows: [
      { label: "CAB-100", item_code: "CAB-100", value: 5, reason: "Upstream of the constraint", recommended_min: 10, recommended_max: 25,
        evidence: evidence({ formula: "buffer range method" }) }] }) });
    render(<DashboardApp />);
    await user.click(navButton(/Recommendation/));
    expect(await screen.findByText("40 rows · click to inspect")).toBeInTheDocument();
    expect(screen.getByText("Analytical buffer recommendations")).toBeInTheDocument();
    const row = screen.getByRole("button", { name: /CAB-100/ });
    expect(row).toHaveTextContent("10–25");
    expect(row).toHaveTextContent("Upstream of the constraint");
    await user.click(row);
    expect(within(screen.getByRole("dialog")).getByText("buffer range method")).toBeInTheDocument();
  });

  it("describes data-quality groups by unique records and offers the CSV export", async () => {
    const { user } = setup({ "/api/pages/data-quality": page({ title: "Data Quality", rows: [
      entry("negative_inventory", 12, { classification: "BLOCKING", unique_records: 12, records_affected_pct: 3.4, origin: "SYNTHETIC", impact_scope: "item" })] }) });
    render(<DashboardApp />);
    await user.click(navButton(/Data Quality/));
    const row = await screen.findByRole("button", { name: /negative_inventory/ });
    expect(row).toHaveTextContent("12 unique records · 3.4% of the table · SYNTHETIC · scope item");
    expect(screen.getByRole("link", { name: /Export all findings/ })).toHaveAttribute("href", `${API}/api/dq/findings.csv`);
  });

  it("shows the cost caveats and the review-package links on the decision pages", async () => {
    const { user } = setup();
    render(<DashboardApp />);
    await heading("Plant Overview");
    await user.click(navButton(/Decision Economics/));
    expect(await screen.findByText(/No ROI or profit figure is computed/)).toBeInTheDocument();
    await user.click(navButton(/Planning Policy/));
    expect(await screen.findByRole("link", { name: "JSON" })).toHaveAttribute("href", `${API}/api/parameters/package.json`);
    expect(screen.getByRole("link", { name: "CSV" })).toHaveAttribute("href", `${API}/api/parameters/package.csv`);
  });
});

describe("scenario lab", () => {
  async function openLab(routes: Record<string, unknown> = {}) {
    const ctx = setup(routes);
    render(<DashboardApp />);
    await ctx.user.click(navButton(/Scenario Lab/));
    await heading("Scenario Lab");
    return ctx;
  }

  it("starts from the +40% demand shock defaults", async () => {
    await openLab();
    expect(screen.getByLabelText("Demand multiplier")).toHaveValue(1.4);
    expect(screen.getByLabelText("Buffer units per item")).toHaveValue(0);
    expect(screen.getByLabelText("Capacity multiplier")).toHaveValue(1);
  });

  it("posts the chosen inputs and shows the saved run", async () => {
    const run = vi.fn(() => ({ run_id: 7, metrics: [entry("Ending backlog", 1234.5)] }));
    const { fetchMock, user } = await openLab({ "/api/scenarios/run": run });
    const demand = screen.getByLabelText("Demand multiplier");
    await user.clear(demand);
    await user.type(demand, "2");
    await user.click(screen.getByRole("button", { name: "Run & save" }));
    const saved = await screen.findByRole("button", { name: /Saved run #7/ });
    expect(saved).toHaveTextContent("1,234.5 backlog hours");
    const [, init] = calls(fetchMock, "/api/scenarios/run")[0];
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toMatchObject({ demand_multiplier: 2, buffer_boost_per_item: 0, capacity_multiplier: 1, intervention_start_week: 4 });
    await user.click(saved);
    expect(screen.getByRole("dialog", { name: "Evidence details" })).toBeInTheDocument();
  });

  it("disables the button while the run is in flight", async () => {
    const pending = deferred<unknown>();
    const { user } = await openLab({ "/api/scenarios/run": () => pending.promise });
    await user.click(screen.getByRole("button", { name: "Run & save" }));
    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
    await act(async () => { pending.resolve({ run_id: 1, metrics: [entry("Ending backlog", 1)] }); });
    expect(screen.getByRole("button", { name: "Run & save" })).toBeEnabled();
  });

  it("shows the server's reason when the run is rejected", async () => {
    const { user } = await openLab({ "/api/scenarios/run": () => { throw new HttpError(401, { detail: "A valid X-API-Key header is required" }); } });
    await user.click(screen.getByRole("button", { name: "Run & save" }));
    expect(await screen.findByText(/A valid X-API-Key header is required/)).toBeInTheDocument();
    expect(screen.queryByText(/Saved run/)).not.toBeInTheDocument();
  });
});

describe("order change impact", () => {
  async function openImpact(routes: Record<string, unknown> = {}) {
    const ctx = setup(routes);
    render(<DashboardApp />);
    await ctx.user.click(navButton(/Order Change Impact/));
    await heading("Order Change Impact");
    return ctx;
  }

  it("starts from cancelling CAB-100 from week 2 on day 17", async () => {
    await openImpact();
    expect(screen.getByLabelText("Change on day")).toHaveValue(17);
    expect(screen.getByLabelText("From demand week")).toHaveValue(2);
    expect(screen.getByLabelText("Forecast factor (0 cancels)")).toHaveValue(0);
    expect(screen.getByLabelText("Product family")).toHaveValue("CAB-100");
  });

  it("posts the chosen change and replaces the page with the returned impact", async () => {
    const result = page({ title: "Order Change Impact", metrics: [entry("Stranded, no other use (EUR)", 4321)] });
    const { fetchMock, user } = await openImpact({ "/api/flow/change-impact": () => result });
    const factor = screen.getByLabelText("Forecast factor (0 cancels)");
    await user.clear(factor);
    await user.type(factor, "0.5");
    await user.selectOptions(screen.getByLabelText("Product family"), "");
    await user.click(screen.getByRole("button", { name: "Show impact" }));
    expect(await screen.findByRole("button", { name: /Stranded, no other use/ })).toHaveTextContent("4,321");
    const [, init] = calls(fetchMock, "/api/flow/change-impact")[0];
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ day: 17, from_week: 2, factor: 0.5, families: [] });
  });

  it("shows the server's reason when the change is rejected", async () => {
    const { user } = await openImpact({ "/api/flow/change-impact": () => { throw new HttpError(422, { detail: "to_week must not be before from_week" }); } });
    await user.click(screen.getByRole("button", { name: "Show impact" }));
    expect(await screen.findByText(/to_week must not be before from_week/)).toBeInTheDocument();
  });

  it("disables the button while the impact is calculated", async () => {
    const pending = deferred<unknown>();
    const { user } = await openImpact({ "/api/flow/change-impact": () => pending.promise });
    await user.click(screen.getByRole("button", { name: "Show impact" }));
    expect(screen.getByRole("button", { name: "Calculating…" })).toBeDisabled();
    await act(async () => { pending.resolve(page({ title: "Order Change Impact" })); });
    expect(screen.getByRole("button", { name: "Show impact" })).toBeEnabled();
  });
});

describe("stage performance records", () => {
  const record = (id: number, cls: string) => ({ po_operation_id: id, production_order_id: 900 + id, seq_no: 10, work_centre_id: 3, item_id: 55,
    status: "IN_PROGRESS", actual_start: "2026-01-05T08:00:00", actual_finish: null, record_class: cls, elapsed_hours: null, standard_processing_hours: 1.5 });
  const lastQuery = (records: ReturnType<typeof vi.fn>) => (records.mock.calls.at(-1)![0] as URL).searchParams;

  async function openStage(total = 60) {
    const records = vi.fn((url: URL) => ({ total, records: [record(Number(url.searchParams.get("offset")) + 1, url.searchParams.get("record_class") || "ALL")] }));
    const ctx = setup({ "/api/stage-performance/records": records });
    render(<DashboardApp />);
    await ctx.user.click(navButton(/Stage Performance/));
    await screen.findByText(`${total} matching operations`);
    return { ...ctx, records };
  }

  it("starts with open started operations, 25 per page", async () => {
    const { records } = await openStage();
    const q = lastQuery(records);
    expect(q.get("record_class")).toBe("OPEN_STARTED");
    expect(q.get("limit")).toBe("25");
    expect(q.get("offset")).toBe("0");
    expect(q.has("work_centre_id")).toBe(false);
    expect(screen.getByText(/elapsed calendar time, not productive processing time/)).toBeInTheDocument();
  });

  it("offers every record class plus All", async () => {
    await openStage();
    const options = within(screen.getByLabelText("Record class")).getAllByRole("option").map(o => o.textContent);
    expect(options).toHaveLength(11);
    expect(options).toEqual(expect.arrayContaining(["All", "COMPLETED_VALID", "DQ_EXCLUDED", "OUTSIDE_WINDOW"]));
  });

  it("refetches from the first page when the class changes", async () => {
    const { user, records } = await openStage();
    await user.click(screen.getByRole("button", { name: "Next →" }));
    await waitFor(() => expect(lastQuery(records).get("offset")).toBe("25"));
    await user.selectOptions(screen.getByLabelText("Record class"), "DUPLICATE");
    await waitFor(() => expect(lastQuery(records).get("record_class")).toBe("DUPLICATE"));
    expect(lastQuery(records).get("offset")).toBe("0");
  });

  it("drops the class filter for All and adds the work-centre filter", async () => {
    const { user, records } = await openStage();
    await user.selectOptions(screen.getByLabelText("Record class"), "All");
    await waitFor(() => expect(lastQuery(records).has("record_class")).toBe(false));
    await user.type(screen.getByLabelText("Work centre id"), "3");
    await waitFor(() => expect(lastQuery(records).get("work_centre_id")).toBe("3"));
  });

  it("pages forward and back, disabling the ends", async () => {
    const { user, records } = await openStage(60);
    expect(screen.getByRole("button", { name: "← Previous" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next →" }));
    await waitFor(() => expect(lastQuery(records).get("offset")).toBe("25"));
    await user.click(await screen.findByRole("button", { name: "Next →" }));
    await waitFor(() => expect(lastQuery(records).get("offset")).toBe("50"));
    expect(await screen.findByRole("button", { name: "Next →" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "← Previous" }));
    await waitFor(() => expect(lastQuery(records).get("offset")).toBe("25"));
  });

  it("says so when a class has no operations", async () => {
    const { user } = setup({ "/api/stage-performance/records": () => ({ total: 0, records: [] }) });
    render(<DashboardApp />);
    await user.click(navButton(/Stage Performance/));
    expect(await screen.findByText("No operations in this class.")).toBeInTheDocument();
  });

  it("drops the old table when a filter change fails, so filters and rows never disagree", async () => {
    let failNext = false;
    const records = vi.fn((url: URL) => {
      if (failNext) throw new HttpError(500, { detail: "Records unavailable today" });
      return { total: 1, records: [record(1, url.searchParams.get("record_class") || "ALL")] };
    });
    const { user } = setup({ "/api/stage-performance/records": records });
    render(<DashboardApp />);
    await user.click(navButton(/Stage Performance/));
    await screen.findByText("1 matching operations");
    expect(screen.getByText("OPEN_STARTED", { selector: "td" })).toBeInTheDocument();
    failNext = true;
    await user.selectOptions(screen.getByLabelText("Record class"), "DUPLICATE");
    expect(await screen.findByText(/Records unavailable today/)).toBeInTheDocument();
    expect(screen.queryByText("OPEN_STARTED", { selector: "td" })).not.toBeInTheDocument();
    expect(screen.queryByText("1 matching operations")).not.toBeInTheDocument();
  });

  it("shows the records error", async () => {
    const { user } = setup({ "/api/stage-performance/records": () => { throw new HttpError(500, { detail: "Records unavailable today" }); } });
    render(<DashboardApp />);
    await user.click(navButton(/Stage Performance/));
    expect(await screen.findByText(/Records unavailable today/)).toBeInTheDocument();
  });
});

describe("copilot", () => {
  async function openCopilot(route: unknown) {
    const ctx = setup({ "/copilot/ask": route });
    render(<DashboardApp />);
    await screen.findByRole("heading", { name: "Ask about the model" });
    return ctx;
  }

  it("sends the question and shows the answer with its raw evidence", async () => {
    const ask = vi.fn(() => ({ answer: "Welding is the constraint.", evidence: [{ tool: "get_constraint" }] }));
    const { fetchMock, user } = await openCopilot(ask);
    const box = screen.getByLabelText("Question");
    expect(box).toHaveValue("Why does capacity matter after the demand shock?");
    await user.clear(box);
    await user.type(box, "What binds?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText("Welding is the constraint.")).toBeInTheDocument();
    expect(JSON.parse(String(calls(fetchMock, "/copilot/ask")[0][1]?.body))).toEqual({ question: "What binds?" });
    expect(screen.getByText(/"tool": "get_constraint"/)).toBeInTheDocument();
  });

  it("asks when Enter is pressed", async () => {
    const ask = vi.fn(() => ({ answer: "Answer by enter.", evidence: [] }));
    const { user } = await openCopilot(ask);
    await user.type(screen.getByLabelText("Question"), "{Enter}");
    expect(await screen.findByText("Answer by enter.")).toBeInTheDocument();
    expect(ask).toHaveBeenCalledTimes(1);
  });

  it("shows the failure instead of an answer and re-enables the button", async () => {
    const { user } = await openCopilot(() => { throw new HttpError(403, { detail: "This action needs an operator key" }); });
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/This action needs an operator key/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
  });

  it("is disabled while the model is being checked", async () => {
    const pending = deferred<unknown>();
    const { user } = await openCopilot(() => pending.promise);
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
    await act(async () => { pending.resolve({ answer: "Done.", evidence: [] }); });
    expect(await screen.findByText("Done.")).toBeInTheDocument();
  });
});

describe("executive story", () => {
  async function openStory(extra: Record<string, unknown> = {}) {
    const ctx = setup({ "/story": storyPayload(), "/api/executive-story": leadStory(), ...extra });
    render(<DashboardApp />);
    await heading("Plant Overview");
    await ctx.user.click(screen.getByRole("button", { name: /Executive Story/ }));
    await heading("Executive Story");
    return ctx;
  }

  it("shows the narrative beats and the horizon rationale", async () => {
    const { fetchMock } = await openStory();
    expect(await screen.findByText("Twelve-week horizon rationale.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Demand rises" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Welding binds" })).toBeInTheDocument();
    expect(calls(fetchMock, "/story")).toHaveLength(1);
    expect(calls(fetchMock, "/api/executive-story").length).toBeGreaterThan(0);
  });

  it("opens the evidence behind a beat", async () => {
    const { user } = await openStory();
    await user.click(await screen.findByRole("button", { name: /Welding binds/ }));
    expect(within(screen.getByRole("dialog")).getByText("beat two method")).toBeInTheDocument();
  });

  it("links on to the scenario lab", async () => {
    const { user } = await openStory();
    await user.click(await screen.findByRole("button", { name: /Explore the scenarios/ }));
    expect(await heading("Scenario Lab")).toBeInTheDocument();
  });

  it("reports a story that cannot be computed", async () => {
    setup({ "/story": () => { throw new HttpError(503, { detail: "Story data unavailable" }); } });
    const user = userEvent.setup();
    render(<DashboardApp />);
    await user.click(await screen.findByRole("button", { name: /Executive Story/ }));
    expect(await screen.findByText(/Story data unavailable/)).toBeInTheDocument();
  });
});
