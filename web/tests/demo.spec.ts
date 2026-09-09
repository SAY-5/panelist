import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createDemo, DEFAULT_DEMO } from "../src/sim";

test("runs the real simulation, exports matching JSONL, and resets deterministically", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "The work behind a better answer." }),
  ).toBeVisible();
  await expect(page.getByTestId("run-status")).toHaveText("Ready");
  await expect(
    page.getByRole("button", { name: "Download JSONL" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Step once" }).click();
  await expect(page.getByTestId("run-status")).toHaveText("Paused");
  await page.getByLabel("Playback speed").selectOption("100");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(page.getByTestId("run-status")).toHaveText("Complete", {
    timeout: 15000,
  });
  const checksum = await page.getByTestId("checksum").textContent();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download JSONL" }).click();
  const download = await downloadPromise;
  const body = await readFile((await download.path())!);
  expect(createHash("sha256").update(body).digest("hex")).toBe(checksum);
  expect(
    body
      .toString()
      .trim()
      .split("\n")
      .every((line) => JSON.parse(line).task_id),
  ).toBe(true);
  const csvPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download payouts CSV" }).click();
  const csv = (await readFile((await (await csvPromise).path())!)).toString();
  expect(csv).toMatch(/^period,payout_id/);
  const paidCents = csv
    .trim()
    .split("\r\n")
    .slice(1)
    .reduce((total, row) => total + Number(row.split(",")[7]), 0);
  await expect(
    page
      .locator(".metrics > div")
      .filter({ has: page.getByText("Payouts approved", { exact: true }) })
      .locator("dd"),
  ).toHaveText(
    new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
    }).format(paidCents / 100),
  );
  await page.getByRole("button", { name: "Reset simulation" }).click();
  await expect(page.getByTestId("run-status")).toHaveText("Ready");
  await expect(
    page.getByRole("button", { name: "Download JSONL" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(page.getByTestId("run-status")).toHaveText("Complete", {
    timeout: 15000,
  });
  await expect(page.getByTestId("checksum")).toHaveText(checksum!);
});

test("the final event enables exports without requiring an extra step", async ({
  page,
}) => {
  const run = createDemo({ ...DEFAULT_DEMO, tasks: 100 });
  let eventCount = 0;
  while (!run.steps.next().done) eventCount += 1;
  await page.clock.install();
  await page.goto("/");
  await page.getByLabel("Task count").selectOption("100");
  await page.getByRole("button", { name: "Apply settings" }).click();
  await page.getByLabel("Playback speed").selectOption("1");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await page.clock.runFor((eventCount - 1) * 80);
  await page.getByRole("button", { name: "Pause simulation" }).click();
  await expect(page.getByTestId("run-status")).toHaveText("Paused");
  await page.getByRole("button", { name: "Step once" }).click();
  await expect(page.getByTestId("run-status")).toHaveText("Complete");
  await expect(
    page.getByRole("button", { name: "Download JSONL" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Download payouts CSV" }),
  ).toBeEnabled();
});

test("pauses without advancing and supports a configured run", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Seed").fill("19");
  await page.getByRole("button", { name: "Apply settings" }).click();
  await page.getByRole("button", { name: "Run simulation" }).click();
  await page.getByRole("button", { name: "Pause simulation" }).click();
  const count = await page.getByTestId("event-count").textContent();
  await page.waitForTimeout(250);
  await expect(page.getByTestId("event-count")).toHaveText(count!);
  await expect(page.getByTestId("active-seed")).toHaveText("19");
});

test("mobile controls and expert inspection remain usable without page overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Experts" }).click();
  await expect(
    page.getByRole("heading", { name: "Expert roster" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Events" }).click();
  await expect(
    page.getByText("Run or step through the simulation to inspect its events."),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
