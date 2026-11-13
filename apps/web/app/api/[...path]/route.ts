import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED_ROOTS = new Set([
  "auth", "dashboard", "opportunities", "vendor-profile", "search-profiles",
  "sources", "source-runs", "jobs", "workflow-runs", "audit-events"
]);
const MAX_BODY_BYTES = 1_048_576;

function failure(code: string, message: string, status: number) {
  return Response.json({ error: { code, message } }, { status, headers: { "cache-control": "no-store, private" } });
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (path[0] !== "v1" || !ALLOWED_ROOTS.has(path[1]) || path.some(segment => !/^[A-Za-z0-9_-]+$/.test(segment))) {
    return Response.json({ error: { code: "ROUTE_NOT_ALLOWED", message: "This API route is unavailable." } }, { status: 404 });
  }

  let base: URL;
  try {
    base = new URL(process.env.API_BASE_URL ?? "http://127.0.0.1:8210");
    if (base.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(base.hostname) || base.port !== "8210" || base.username || base.password || base.pathname !== "/" || base.search || base.hash) throw new Error();
  } catch {
    return failure("CONFIGURATION_ERROR", "The local API endpoint is not configured correctly.", 503);
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
    if (Number(request.headers.get("content-length") ?? 0) > MAX_BODY_BYTES) return failure("BODY_TOO_LARGE", "Request is too large.", 413);
    const reader = request.body?.getReader();
    if (reader) {
      const chunks: Uint8Array[] = [];
      let bytes = 0;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          bytes += value.byteLength;
          if (bytes > MAX_BODY_BYTES) {
            void reader.cancel().catch(() => undefined);
            return failure("BODY_TOO_LARGE", "Request is too large.", 413);
          }
          chunks.push(value);
        }
      } catch {
        return failure("INVALID_REQUEST_BODY", "The request body could not be read.", 400);
      } finally { reader.releaseLock(); }
      const combined = new Uint8Array(bytes);
      let offset = 0;
      for (const chunk of chunks) { combined.set(chunk, offset); offset += chunk.byteLength; }
      body = combined.buffer;
    }
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
