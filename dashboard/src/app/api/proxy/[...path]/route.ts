import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.API_URL ?? "http://api:8000";

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: RouteContext) {
  return proxy(req, await ctx.params, "GET");
}
export async function POST(req: NextRequest, ctx: RouteContext) {
  return proxy(req, await ctx.params, "POST");
}
export async function PUT(req: NextRequest, ctx: RouteContext) {
  return proxy(req, await ctx.params, "PUT");
}
export async function DELETE(req: NextRequest, ctx: RouteContext) {
  return proxy(req, await ctx.params, "DELETE");
}

async function proxy(
  req: NextRequest,
  params: { path: string[] },
  method: string,
): Promise<NextResponse> {
  try {
    const path   = "/" + params.path.join("/");
    const search = req.nextUrl.search ?? "";
    const url    = `${API_URL}${path}${search}`;

    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const auth = req.headers.get("authorization");
    if (auth) headers["Authorization"] = auth;

    const init: RequestInit = { method, headers };
    if (method !== "GET" && method !== "DELETE") {
      const body = await req.text();
      if (body) init.body = body;
    }

    const upstream    = await fetch(url, init);
    const contentType = upstream.headers.get("content-type") ?? "application/json";
    const text        = await upstream.text();

    return new NextResponse(text, {
      status: upstream.status,
      headers: { "Content-Type": contentType },
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : "Proxy error";
    return NextResponse.json({ detail: msg }, { status: 502 });
  }
}
