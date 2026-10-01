import { defineConfig } from "@playwright/test";

// Smoke test against the running demo stack (docker compose up). It is read-only: it never runs a scenario,
// and the copilot step (which may reach an external LLM) runs only with PILOT_E2E_COPILOT=1.
// Uses the system Chrome, so no browser download is needed; set PILOT_BROWSER_CHANNEL=msedge (or "") to change.
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.PILOT_UI_URL ?? "http://localhost:5173",
    channel: process.env.PILOT_BROWSER_CHANNEL ?? "chrome",
    trace: "retain-on-failure",
  },
});
