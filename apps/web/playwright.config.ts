import { defineConfig } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(__dirname, "../..");
for (const line of readFileSync(resolve(root, ".local/env"), "utf8").split(/\r?\n/)) {
  const separator = line.indexOf("=");
  if (separator > 0 && line.startsWith("KAJH_")) {
    const key = line.slice(0, separator), value = line.slice(separator + 1);
    if (["KAJH_OWNER_PASSWORD", "KAJH_REVIEWER_PASSWORD", "KAJH_AUDITOR_PASSWORD"].includes(key)) process.env[key] = value;
  }
}

export default defineConfig({
  testDir: resolve(root, "tests/e2e"),
  outputDir: resolve(root, ".local/evidence/playwright-artifacts"),
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: resolve(root, ".local/evidence/playwright-results.json") }]],
  use: {
    baseURL: "http://127.0.0.1:3210",
    viewport: { width: 1440, height: 1000 },
    browserName: "chromium",
    headless: true,
    trace: "off",
    screenshot: "only-on-failure"
  }
});
