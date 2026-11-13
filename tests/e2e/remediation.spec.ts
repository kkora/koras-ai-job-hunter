import { test, expect, type Page } from "../../apps/web/node_modules/@playwright/test";
import AxeBuilder from "../../apps/web/node_modules/@axe-core/playwright";
import { NextRequest } from "../../apps/web/node_modules/next/server";
import { GET as proxyGet } from "../../apps/web/app/api/[...path]/route";
import { request as httpRequest } from "node:http";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email address").fill("owner@kajh.invalid");
  const password = process.env.KAJH_OWNER_PASSWORD;
  if (!password) throw new Error("Missing generated synthetic owner credential");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Open workspace", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Opportunity intelligence", exact: true })).toBeVisible();
}

function navigation(page: Page, name: string) {
  return page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name, exact: true });
}

test.beforeEach(async ({ page }) => {
  await page.route("**/*", route => new URL(route.request().url()).origin === "http://127.0.0.1:3210" ? route.continue() : route.abort("blockedbyclient"));
});

test("editing a search profile preserves intentionally empty preference lists", async ({ page }) => {
  await signIn(page);
  await navigation(page, "Search profiles").click();
  await page.getByRole("button", { name: "New profile", exact: true }).click();
  const name = `Synthetic empty preferences ${Date.now()}`;
  let dialog = page.getByRole("dialog", { name: "Create search profile", exact: true });
  await dialog.getByLabel("Profile name", { exact: true }).fill(name);
  await dialog.getByLabel(/Technical skills/).fill("Dataverse");
  await dialog.getByLabel("Seniority", { exact: true }).fill("");
  await dialog.getByLabel("Excluded employment types", { exact: true }).fill("");
  await dialog.getByRole("button", { name: "Save profile", exact: true }).click();
  await expect(dialog).toBeHidden();
  const card = page.locator(".profile-card").filter({ has: page.getByRole("heading", { name, exact: true }) });
  await card.getByRole("button", { name: "Edit profile", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "Edit search profile", exact: true });
  await expect(dialog.getByLabel("Seniority", { exact: true })).toHaveValue("");
  await expect(dialog.getByLabel("Excluded employment types", { exact: true })).toHaveValue("");
  await dialog.getByRole("button", { name: "Save profile", exact: true }).click();
  await expect(dialog).toBeHidden();
  const response = await page.request.get("/api/v1/search-profiles");
  const profile = (await response.json()).items.find((item: { name: string }) => item.name === name);
  expect(profile.version).toBe(2);
  expect(profile.filters.seniority).toEqual([]);
  expect(profile.filters.excluded_employment_types).toEqual([]);
});

test("modal makes the workspace inert, traps keyboard focus, and restores its opener", async ({ page }) => {
  await signIn(page);
  await navigation(page, "Search profiles").click();
  const opener = page.getByRole("button", { name: "New profile", exact: true });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "Create search profile", exact: true });
  const close = dialog.getByRole("button", { name: "Close dialog", exact: true });
  await expect(close).toBeFocused();
  expect(await page.locator(".workspace-shell").evaluate(element => (element as HTMLElement).inert)).toBe(true);
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Save profile", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(close).toBeFocused();
  const accessibility = await new AxeBuilder({ page }).exclude("nextjs-portal").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(accessibility.violations.filter(item => ["serious", "critical"].includes(item.impact ?? "")).map(item => ({ id: item.id, targets: item.nodes.map(node => node.target) }))).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
  expect(await page.locator(".workspace-shell").evaluate(element => (element as HTMLElement).inert)).toBe(false);
});

test("a revoked session clears the private workspace on the next protected read", async ({ page }) => {
  await signIn(page);
  await navigation(page, "Workflow monitor").click();
  await expect(page.getByRole("heading", { name: "Workflow monitor", exact: true })).toBeVisible();
  const otherTab = await page.context().newPage();
  await otherTab.goto("/");
  await otherTab.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(otherTab.getByRole("heading", { name: "Welcome back.", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Refresh runs", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Welcome back.", exact: true })).toBeVisible();
  await expect(page.locator(".workspace-shell")).toHaveCount(0);
  await otherTab.close();
});

test("changing a filter hides stale result counts while the next response is pending", async ({ page }) => {
  await signIn(page);
  await navigation(page, "Opportunities").click();
  await expect(page.locator(".opportunity-row").first()).toBeVisible();
  let release = () => {};
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/v1/opportunities?**", async route => {
    if (new URL(route.request().url()).searchParams.get("country") === "AU") await pending;
    await route.continue();
  });
  try {
    const requested = page.waitForRequest(request => new URL(request.url()).pathname === "/api/v1/opportunities" && new URL(request.url()).searchParams.get("country") === "AU");
    await page.getByRole("combobox", { name: "Country", exact: true }).selectOption("AU");
    await requested;
    await expect(page.getByRole("status").filter({ hasText: "Loading opportunities" })).toBeVisible();
    await expect(page.locator(".results-toolbar strong")).toHaveText("—");
    await expect(page.locator(".opportunity-row")).toHaveCount(0);
  } finally { release(); }
  await expect(page.locator(".opportunity-row").first()).toContainText("Australia");
});

test("the same-origin proxy preserves origin and CSRF enforcement", async ({ page }) => {
  await signIn(page);
  const session = await (await page.request.get("/api/v1/auth/me")).json();
  const item = (await (await page.request.get("/api/v1/opportunities?limit=1")).json()).items[0];
  const path = `/api/v1/opportunities/${item.id}/saved`;
  const body = { saved: item.saved };
  const missingToken = await page.request.put(path, { data: body, headers: { Origin: "http://127.0.0.1:3210" } });
  expect(missingToken.status()).toBe(403);
  const foreignOrigin = await page.request.put(path, { data: body, headers: { Origin: "http://untrusted.invalid", "x-csrf-token": session.csrf_token } });
  expect(foreignOrigin.status()).toBe(403);
  const valid = await page.request.put(path, { data: body, headers: { Origin: "http://127.0.0.1:3210", "x-csrf-token": session.csrf_token, "idempotency-key": crypto.randomUUID() } });
  expect(valid.ok()).toBe(true);
});

test("proxy rejects oversized chunked uploads before the client finishes uploading", async () => {
  const response = await new Promise<{ status: number; body: string }>((resolve, reject) => {
    let settled = false;
    const request = httpRequest("http://127.0.0.1:3210/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json", "Transfer-Encoding": "chunked", Origin: "http://127.0.0.1:3210" } });
    const timer = setTimeout(() => { settled = true; request.destroy(); reject(new Error("Proxy waited for the oversized upload to finish")); }, 5000);
    request.once("error", error => { if (!settled) { settled = true; clearTimeout(timer); reject(error); } });
    request.once("response", incoming => {
      let body = "";
      incoming.on("data", chunk => { body += String(chunk); });
      incoming.once("end", () => { settled = true; clearTimeout(timer); request.destroy(); resolve({ status: incoming.statusCode ?? 0, body }); });
    });
    request.write(Buffer.alloc(600_000, "0"));
    request.write(Buffer.alloc(600_000, "0"));
    // Leave the upload open: the proxy must enforce its cap while reading.
  });
  expect(response.status).toBe(413);
  expect(JSON.parse(response.body).error.code).toBe("BODY_TOO_LARGE");
});

test("malformed proxy configuration returns a safe structured response", async () => {
  const previous = process.env.API_BASE_URL;
  try {
    for (const invalid of ["not a URL", "http://127.0.0.1:8210/?target=forbidden", "http://127.0.0.1:8210/#fragment"]) {
      process.env.API_BASE_URL = invalid;
      const response = await proxyGet(new NextRequest("http://127.0.0.1:3210/api/v1/auth/me"), { params: Promise.resolve({ path: ["v1", "auth", "me"] }) });
      expect(response.status).toBe(503);
      expect(response.headers.get("cache-control")).toContain("no-store");
      expect((await response.json()).error.code).toBe("CONFIGURATION_ERROR");
    }
  } finally {
    if (previous === undefined) delete process.env.API_BASE_URL;
    else process.env.API_BASE_URL = previous;
  }
});
