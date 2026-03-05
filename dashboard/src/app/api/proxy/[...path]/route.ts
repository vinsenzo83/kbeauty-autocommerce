import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.API_URL ?? "http://api:8000";

export async function GET(
  req: NextRequest,
  { params }: { params: { path: string[] } }
) {
  return proxyRequest(req, params.path, "GET");
}

export async function POST(
  req: NextRequest,
  { params }: { params: { path: string[] } }
) {
  return proxyRequest(req, params.path, "POST");
}

export async function PUT(
  req: NextRequest,
  { params }: { params: { path: string[] } }
) {
  return proxyRequest(req, params.path, "PUT");
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: { path: string[] } }
) {
  return proxyRequest(req, params.path, "DELETE");
}

async function proxyRequest(
  req: NextRequest,
  pathSegments: string[],
  method: string
): Promise<NextResponse> {
  try {
    const path = "/" + pathSegments.join("/");
    const search = req.nextUrl.search ?? "";
    const url = `${API_URL}${path}${search}`;

    // Forward Authorization header from the original request
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    const authHeader = req.headers.get("authorization");
    if (authHeader) {
      headers["Authorization"] = authHeader;
    }

    const init: RequestInit = { method, headers };
    if (method !== "GET" && method !== "DELETE") {
      const body = await req.text();
      if (body) init.body = body;
    }

    const upstream = await fetch(url, init);
    const contentType = upstream.headers.get("content-type") ?? "";
    const text = await upstream.text();

    return new NextResponse(text, {
      status: upstream.status,
      headers: {
        "Content-Type": contentType || "application/json",
      },
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : "Unknown proxy error";
    return NextResponse.json({ detail: msg }, { status: 502 });
  }
}
