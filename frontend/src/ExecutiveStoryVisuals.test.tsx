import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Plotly from "plotly.js-dist-min";
import { describe, expect, it, vi } from "vitest";
import { ExecutiveStoryVisuals } from "./ExecutiveStoryVisuals";
import { plotCalls } from "./test/setup";
import { HttpError, deferred, leadStory, mockApi } from "./test/fixtures";

describe("ExecutiveStoryVisuals", () => {
  it("shows a calculating state until the lead-time data arrives", async () => {
    const pending = deferred<unknown>();
    mockApi({ "/api/executive-story": () => pending.promise });
    render(<ExecutiveStoryVisuals onEvidence={vi.fn()} />);
    expect(screen.getByText(/Calculating the five-case, BOM-aware lead-time comparison/)).toBeInTheDocument();
    await act(async () => { pending.resolve(leadStory()); });
    expect(await screen.findByText("Lead time changes with backlog")).toBeInTheDocument();
  });

  it("shows the API error when the data is unavailable", async () => {
    mockApi({ "/api/executive-story": () => { throw new HttpError(503, { detail: "Lead-time engine not ready" }); } });
    render(<ExecutiveStoryVisuals onEvidence={vi.fn()} />);
    expect(await screen.findByText(/Lead-time engine not ready/)).toBeInTheDocument();
    expect(plotCalls).toHaveLength(0);
  });

  it("has one card per scenario with its week-12 lead time", async () => {
    mockApi({ "/api/executive-story": leadStory() });
    render(<ExecutiveStoryVisuals onEvidence={vi.fn()} />);
    const cards = await screen.findAllByRole("button", { name: /Week 12/ });
    expect(cards).toHaveLength(5);
    // week 12 totals are 2 + k + 11 * 0.25 = 4.75, 5.75, ... 8.75
    expect(cards[0]).toHaveTextContent("Baseline");
    expect(cards[0]).toHaveTextContent("4.8d");
    expect(cards[4]).toHaveTextContent("Combined");
    expect(cards[4]).toHaveTextContent("8.8d");
  });

  it("passes a card's week-12 evidence to the caller", async () => {
    const onEvidence = vi.fn();
    mockApi({ "/api/executive-story": leadStory() });
    const user = userEvent.setup();
    render(<ExecutiveStoryVisuals onEvidence={onEvidence} />);
    await user.click((await screen.findAllByRole("button", { name: /Week 12/ }))[1]);
    expect(onEvidence).toHaveBeenCalledTimes(1);
    expect(onEvidence.mock.calls[0][0].formula).toBe("lead time 2026-01-12");
  });

  it("draws five scenario lines and a stacked processing/queue/transfer chart", async () => {
    mockApi({ "/api/executive-story": leadStory() });
    render(<ExecutiveStoryVisuals onEvidence={vi.fn()} />);
    await waitFor(() => expect(plotCalls).toHaveLength(2));
    const [line, stack] = plotCalls;
    expect(line.data.map(t => t.name)).toEqual(["Baseline", "Demand shock", "Buffer only", "Capacity only", "Combined"]);
    expect(line.data[0].x).toHaveLength(12);
    expect(line.data[0].x[0]).toBe("W1");
    expect(stack.layout.barmode).toBe("stack");
    expect(stack.data.map(t => t.name)).toEqual(["Processing", "Queue", "Transfer"]);
  });

  it("opens the evidence of a clicked point on either chart", async () => {
    const onEvidence = vi.fn();
    mockApi({ "/api/executive-story": leadStory() });
    render(<ExecutiveStoryVisuals onEvidence={onEvidence} />);
    await waitFor(() => expect(plotCalls).toHaveLength(2));
    const [line, stack] = plotCalls;
    act(() => line.handlers.plotly_click({ points: [{ curveNumber: 3, pointIndex: 2 }] }));
    expect(onEvidence.mock.calls[0][0].formula).toBe("lead time 2026-01-03");
    act(() => stack.handlers.plotly_click({ points: [{ pointIndex: 5 }] }));
    expect(onEvidence.mock.calls[1][0].formula).toBe("lead time 2026-01-06");
  });

  it("ignores a click that does not land on a data point", async () => {
    const onEvidence = vi.fn();
    mockApi({ "/api/executive-story": leadStory() });
    render(<ExecutiveStoryVisuals onEvidence={onEvidence} />);
    await waitFor(() => expect(plotCalls).toHaveLength(2));
    act(() => plotCalls[0].handlers.plotly_click({ points: [] }));
    act(() => plotCalls[1].handlers.plotly_click({ points: [] }));
    expect(onEvidence).not.toHaveBeenCalled();
  });

  it("translates the capacity multiplier into hours", async () => {
    mockApi({ "/api/executive-story": leadStory() });
    render(<ExecutiveStoryVisuals onEvidence={vi.fn()} />);
    const card = (await screen.findByText("Welding")).closest(".operation-card") as HTMLElement;
    expect(within(card).getByText("80.0h/week")).toBeInTheDocument();
    expect(within(card).getByText("60.0h/week")).toBeInTheDocument();
    expect(within(card).getByText("72.0h/week")).toBeInTheDocument();
    expect(within(card).getByText("16.0h/week")).toBeInTheDocument();
    expect(card).toHaveTextContent("Add one Saturday shift. Equivalent at unchanged availability for 1.20×");
    expect(card).toHaveTextContent("Staffing and equipment feasibility require validation");
  });

  it("ignores data that arrives after unmount, and purges both plots on unmount", async () => {
    const pending = deferred<unknown>();
    mockApi({ "/api/executive-story": () => pending.promise });
    const first = render(<ExecutiveStoryVisuals onEvidence={vi.fn()} />);
    first.unmount();
    await act(async () => { pending.resolve(leadStory()); });
    expect(plotCalls).toHaveLength(0);

    mockApi({ "/api/executive-story": leadStory() });
    const second = render(<ExecutiveStoryVisuals onEvidence={vi.fn()} />);
    await waitFor(() => expect(plotCalls).toHaveLength(2));
    second.unmount();
    expect(Plotly.purge).toHaveBeenCalledTimes(2);
  });

  it("shows n/a instead of crashing when a scenario's lead time is unavailable", async () => {
    const story = leadStory();
    story.lead_time_series.COMBINED[11] = { ...story.lead_time_series.COMBINED[11], total_days: null as any,
      processing_days: null as any, queue_days: null as any, transfer_days: null as any };
    const onEvidence = vi.fn();
    mockApi({ "/api/executive-story": story });
    const user = userEvent.setup();
    render(<ExecutiveStoryVisuals onEvidence={onEvidence} />);
    const cards = await screen.findAllByRole("button", { name: /Week 12/ });
    expect(cards[4]).toHaveTextContent("Combined");
    expect(cards[4]).toHaveTextContent("n/a");
    expect(cards[4]).not.toHaveTextContent("NaN");
    await user.click(cards[4]);
    expect(onEvidence).toHaveBeenCalledTimes(1);
  });

  it("shows n/a in the capacity card when an hours value is missing", async () => {
    const story = leadStory();
    story.capacity_intervention[0] = { ...story.capacity_intervention[0], effective_hours_per_week_current: null as any,
      effective_hours_per_week_target: null as any, target_multiplier: null as any };
    mockApi({ "/api/executive-story": story });
    render(<ExecutiveStoryVisuals onEvidence={vi.fn()} />);
    const card = (await screen.findByText("Welding")).closest(".operation-card") as HTMLElement;
    expect(within(card).getAllByText("n/a")).toHaveLength(2);
    expect(within(card).getByText("80.0h/week")).toBeInTheDocument();
    expect(card).toHaveTextContent("availability for n/a.");
    expect(card).not.toHaveTextContent(/(^|[^0-9.])h\/week/);
  });
});
