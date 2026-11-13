import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED_ROOTS = new Set([
  "auth", "dashboard", "opportunities", "vendor-profile", "search-profiles",
  "sources", "source-runs", "jobs", "workflow-runs", "audit-events"
]);

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (path[0] !== "v1" || !ALLOWED_ROOTS.has(path[1]) || path.some(segment => !/^[A-Za-z0-9_-]+$/.test(segment))) {
    return Response.json({ error: { code: "ROUTE_NOT_ALLOWED", message: "This API route is unavailable." } }, { status: 404 });
  }

  const base = new URL(process.env.API_BASE_URL ?? "http://127.0.0.1:8210");
  if (base.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(base.hostname) || base.port !== "8210" || base.username || base.password || base.pathname !== "/") {
    return Response.json({ error: { code: "CONFIGURATION_ERROR", message: "The local API endpoint is not configured correctly." } }, { status: 503 });
  }

  const url = new URL(`/api/${path.join("/")}`, base);
  url.search = request.nextUrl.search;
  const headers = new Headers({ accept: "application/json" });
  for (const name of ["content-type", "cookie", "origin", "x-csrf-token", "idempotency-key", "x-request-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  let body: ArrayBuffer | undefined;
  if (!["GET", "HEAD"].includes(request.method)) {
    if (Number(request.headers.get("content-length") ?? 0) > 1_048_576) return Response.json({ error: { code: "BODY_TOO_LARGE", message: "Request is too large." } }, { status: 413 });
    body = await request.arrayBuffer();
    if (body.byteLength > 1_048_576) return Response.json({ error: { code: "BODY_TOO_LARGE", message: "Request is too large." } }, { status: 413 });
  }

  try {
    const upstream = await fetch(url, { method: request.method, headers, body, cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(15000) });
    if (upstream.status >= 300 && upstream.status < 400) return Response.json({ error: { code: "UPSTREAM_REDIRECT", message: "Unexpected API redirect." } }, { status: 502 });
    const output = new Headers({ "content-type": upstream.headers.get("content-type") ?? "application/json", "cache-control": "no-store, private" });
    for (const cookie of upstream.headers.getSetCookie()) output.append("set-cookie", cookie);
    const requestId = upstream.headers.get("x-request-id");
    if (requestId) output.set("x-request-id", requestId);
    return new Response(upstream.body, { status: upstream.status, headers: output });
  } catch {
    return Response.json({ error: { code: "API_UNAVAILABLE", message: "The local API is unavailable. Check the local service status and retry." } }, { status: 503, headers: { "cache-control": "no-store" } });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
