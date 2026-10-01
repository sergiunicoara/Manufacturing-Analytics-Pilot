import { expect, test } from "@playwright/test";

const PAGES = ["Plant Overview", "Demand & Forecast", "Production Flow", "BOM Explorer", "Capacity", "WIP & Lead Time",
  "Scenario Lab", "Data Quality", "Recommendation", "Stage Performance", "Decision Economics", "Planning Policy",
  "Shop Floor Flow", "Order Change Impact"];

test.beforeEach(async ({ page }) => {
  const problems: string[] = [];
  page.on("pageerror", e => problems.push(`page error: ${e.message}`));
  page.on("console", m => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  (page as any).problems = problems;
});
test.afterEach(async ({ page }) => { expect((page as any).problems, "browser errors").toEqual([]); });

const h1 = (page: any, name: string) => page.getByRole("heading", { level: 1, name });

test("every page loads with metrics and no browser errors", async ({ page }) => {
  await page.goto("/");
  for (const name of PAGES) {
    await page.getByRole("navigation").getByRole("button", { name: new RegExp(name.replace(/[&]/g, "\&")) }).click();
    await expect(h1(page, name)).toBeVisible();
    await expect(page.locator(".state-card.error-text")).toHaveCount(0);
    await expect(page.locator(".metric-card").first()).toBeVisible();
  }
});

test("a chart renders and a metric opens its evidence", async ({ page }) => {
  await page.goto("/");
  await expect(h1(page, "Plant Overview")).toBeVisible();
  await expect(page.locator(".plot svg.main-svg").first()).toBeVisible();
  await page.locator(".metric-card").first().click();
  const drawer = page.getByRole("dialog", { name: "Evidence details" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "Method" })).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "Assumptions" })).toBeVisible();
  await page.getByRole("button", { name: "Close evidence" }).click();
  await expect(drawer).toBeHidden();
});

test("the executive story shows five scenarios and two charts", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Executive Story/ }).click();
  await expect(h1(page, "Executive Story")).toBeVisible();
  await expect(page.getByRole("button", { name: /Week 12/ })).toHaveCount(5);
  await expect(page.locator(".story-plot svg.main-svg, .plot svg.main-svg").first()).toBeVisible();
  await page.getByRole("button", { name: /Week 12/ }).first().click();
  await expect(page.getByRole("dialog", { name: "Evidence details" })).toBeVisible();
});

test("stage records can be filtered and paged", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation").getByRole("button", { name: /Stage Performance/ }).click();
  await expect(page.getByText(/matching operations/)).toBeVisible();
  await page.getByLabel("Record class").selectOption("COMPLETED_VALID");
  await expect(page.locator(".record-table tbody tr").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "← Previous" })).toBeDisabled();
});

// Opt-in: if the API has an Anthropic key (demo profile), asking the copilot sends the question and evidence
// to the external model. Set PILOT_E2E_COPILOT=1 only when that is intended.
test("the copilot answers from deterministic evidence", async ({ page }) => {
  test.skip(process.env.PILOT_E2E_COPILOT !== "1", "set PILOT_E2E_COPILOT=1 to include the copilot (may call the external LLM)");
  await page.goto("/");
  await page.getByLabel("Question").press("Enter");
  await expect(page.getByText("Raw deterministic evidence")).toBeVisible();
});
