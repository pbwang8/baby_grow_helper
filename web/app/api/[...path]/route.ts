import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const API_INTERNAL_BASE =
  process.env.API_INTERNAL_BASE ?? "http://localhost:8000";

const FORWARDED_HEADERS = [
  "accept",
  "content-type",
  "x-family-code",
  "x-user-id",
] as const;

async function proxy(request: NextRequest, params: { path?: string[] }) {
  const target = new URL(
    `/${(params.path ?? []).join("/")}${request.nextUrl.search}`,
    API_INTERNAL_BASE
  );
  const headers = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const method = request.method.toUpperCase();
  const init: RequestInit = { method, headers, cache: "no-store" };
  if (method !== "GET" && method !== "HEAD") {
    const body = await request.arrayBuffer();
    if (body.byteLength > 0) init.body = body;
  }

  const upstream = await fetch(target, init);
  const responseHeaders = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) responseHeaders.set("content-type", contentType);

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export async function GET(
  request: NextRequest,
  { params }: { params: { path?: string[] } }
) {
  return proxy(request, params);
}

export async function POST(
  request: NextRequest,
  { params }: { params: { path?: string[] } }
) {
  return proxy(request, params);
}

export async function PUT(
  request: NextRequest,
  { params }: { params: { path?: string[] } }
) {
  return proxy(request, params);
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: { path?: string[] } }
) {
  return proxy(request, params);
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: { path?: string[] } }
) {
  return proxy(request, params);
}
