import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Plotly needs a real canvas/SVG layout engine; the tests only care what the UI asks it to draw.
// Like the real library, newPlot attaches `on` / `removeAllListeners` to the node so click handlers can be fired.
export type PlotCall = { node: HTMLDivElement; data: any[]; layout: any; handlers: Record<string, (event: any) => void> };
export const plotCalls: PlotCall[] = [];

vi.mock("plotly.js-dist-min", () => {
  const newPlot = vi.fn((node: any, data: any[], layout: any) => {
    const call: PlotCall = { node, data, layout, handlers: {} };
    node.on = (name: string, handler: (event: any) => void) => { call.handlers[name] = handler; };
    node.removeAllListeners = (name: string) => { delete call.handlers[name]; };
    plotCalls.push(call);
    return Promise.resolve(node);
  });
  return { default: { newPlot, purge: vi.fn() } };
});

afterEach(() => {
  cleanup();
  plotCalls.length = 0;
});
