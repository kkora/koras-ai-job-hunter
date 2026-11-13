export type JsonObject = Record<string, unknown>;
export type Eligibility = "Eligible" | "Ineligible" | "Unknown" | "Needs Review";
export type Session = { user: { id: string; email: string; role: string }; workspace: { id: string; name: string }; csrf_token: string; synthetic: boolean };
export type Opportunity = {
  id: string; title: string; organization: string; category: string; description: string;
  country: string; workplace: string; eligibility: Eligibility; lifecycle: string;
  skills: string[]; facts: JsonObject; version: number; first_seen: string; last_seen: string; saved: boolean;
};
export type OpportunityPage = { items: Opportunity[]; total: number; next_cursor: string | null };
export type Source = { id: string; name: string; adapter_type: string; enabled: boolean; killed: boolean; permission: JsonObject; permission_version: number; cursor: string | null };
export type Dashboard = { counts: Record<string, number>; recent: Opportunity[]; source_health: Source[]; capabilities: Record<string, boolean> };
export type OpportunityDetail = { opportunity: Opportunity; sources: { id: string; name: string; original_url: string; external_id: string }[]; evidence: { rule: string; outcome: string; details: unknown }[]; history: JsonObject[] };
export type VendorProfile = { id: string; version: number; synthetic: boolean; profile: JsonObject };
export type SearchProfile = { id: string; version: number; name: string; filters: JsonObject };

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) { super(message); }
}

let csrf = "";
export function setCsrf(value: string) { csrf = value; }

function cookieCsrf(): string {
  if (typeof document === "undefined") return "";
  const value = document.cookie.split("; ").find(item => item.startsWith("kajh_csrf="));
  return value ? decodeURIComponent(value.slice("kajh_csrf=".length)) : "";
}

export async function api<T>(path: string, options: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  if (!path.startsWith("/")) throw new Error("Invalid API path");
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (!["GET", "HEAD"].includes(method)) {
    headers["x-csrf-token"] = cookieCsrf() || csrf;
    headers["idempotency-key"] = crypto.randomUUID();
  }
  const response = await fetch(`/api/v1${path}`, { method, headers, credentials: "same-origin", cache: "no-store", body: options.body === undefined ? undefined : JSON.stringify(options.body), signal: options.signal });
  const result = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const error = result?.error;
    throw new ApiError(response.status, error?.code ?? "REQUEST_FAILED", error?.message ?? "The request could not be completed.", error?.request_id);
  }
  return result as T;
}

export function str(value: unknown, fallback = "Not specified"): string {
  return typeof value === "string" && value ? value : typeof value === "number" ? String(value) : fallback;
}
export function array(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
export function object(value: unknown): JsonObject { return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {}; }
export function prettyLabel(value: string): string { return value.replaceAll("_", " ").replaceAll("-", " ").replace(/\b\w/g, letter => letter.toUpperCase()); }
export function date(value: unknown): string {
  if (typeof value !== "string") return "Not recorded";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "Not recorded" : new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(parsed);
}
export function shortId(value: unknown): string { return str(value, "—").slice(0, 8); }
export function countryLabel(value: string): string { return ({ US: "United States", CA: "Canada", GB: "United Kingdom", DE: "Germany", AU: "Australia", FR: "France", IN: "India", NL: "Netherlands" } as Record<string, string>)[value] ?? value ?? "Location unknown"; }
export function rateLabel(opportunity: Opportunity): string {
  const rate = object(opportunity.facts.rate);
  const currency = str(opportunity.facts.currency ?? rate.currency, "");
  const numeric = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : typeof value === "string" && /^\d+(\.\d+)?$/.test(value) && Number.isFinite(Number(value)) ? Number(value) : null;
  const minimum = numeric(opportunity.facts.rate_min ?? opportunity.facts.minimum_rate ?? rate.min);
  const maximum = numeric(opportunity.facts.rate_max ?? rate.max);
  const unit = str(opportunity.facts.rate_unit ?? rate.unit, "");
  if (minimum === null || !currency || !unit) return "Rate not disclosed";
  const formatter = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
  return `${currency} ${formatter.format(minimum)}${maximum !== null && maximum !== minimum ? `–${formatter.format(maximum)}` : ""} / ${unit}`;
}
