/**
 * lib/api.ts — Typed API client for the KBeauty Admin backend.
 * All requests inject the stored JWT token automatically.
 */

const API_BASE =
  typeof window !== "undefined"
    ? ""  // browser: use rewrites → /api/...
    : (process.env.API_URL ?? "http://api:8000"); // SSR: direct to api service

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("admin_token");
}

async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const url = API_BASE ? `${API_BASE}${path}` : path;
  const res = await fetch(url, { ...options, headers });

  if (res.status === 401) {
    if (typeof window !== "undefined") {
      localStorage.removeItem("admin_token");
      window.location.href = "/login";
    }
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API error ${res.status}: ${body}`);
  }
  if (res.status === 204) return {} as T;
  return res.json();
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export interface LoginResponse {
  access_token: string;
  token_type: string;
  role: string;
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/admin/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function getMe(): Promise<{ email: string; role: string }> {
  return apiFetch("/admin/auth/me");
}

// ── Dashboard KPI ─────────────────────────────────────────────────────────────
export interface KPI {
  orders_today: number;
  revenue_today: number;
  avg_margin_pct: number;
  failed_today: number;
  tracking_stale_count: number;
  open_tickets_count: number;
}

export async function getKPI(): Promise<KPI> {
  return apiFetch("/admin/dashboard/kpi");
}

export interface Alerts {
  tracking_stale: Array<{ order_id: string; placed_at: string; supplier_order_id: string }>;
  margin_guard_violations: Array<{ order_id: string; margin_pct: number }>;
  bot_failures_last_hour: number;
  queue_backlog: number | null;
}

export async function getAlerts(): Promise<Alerts> {
  return apiFetch("/admin/dashboard/alerts");
}

export interface ChartRow {
  date: string;
  orders: number;
  revenue: number;
}

export async function getChart(days = 7): Promise<ChartRow[]> {
  return apiFetch(`/admin/dashboard/chart?days=${days}`);
}

// ── Orders ────────────────────────────────────────────────────────────────────
export interface OrderItem {
  id: string;
  shopify_order_id: string;
  email: string;
  total_price: string;
  currency: string;
  financial_status: string;
  status: string;
  supplier: string;
  supplier_order_id: string | null;
  placed_at: string | null;
  shipped_at: string | null;
  tracking_number: string | null;
  tracking_url: string | null;
  fail_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrdersResponse {
  total: number;
  page: number;
  page_size: number;
  items: OrderItem[];
}

export async function listOrders(params: {
  status?: string;
  supplier?: string;
  q?: string;
  page?: number;
  page_size?: number;
}): Promise<OrdersResponse> {
  const sp = new URLSearchParams();
  if (params.status)    sp.set("status",    params.status);
  if (params.supplier)  sp.set("supplier",  params.supplier);
  if (params.q)         sp.set("q",         params.q);
  if (params.page)      sp.set("page",      String(params.page));
  if (params.page_size) sp.set("page_size", String(params.page_size));
  const qs = sp.toString() ? `?${sp.toString()}` : "";
  return apiFetch(`/admin/orders${qs}`);
}

export async function getOrder(id: string): Promise<OrderItem & {
  shipping_address: Record<string, unknown>;
  line_items: unknown[];
  events: Array<{ id: string; source: string; event_type: string; note: string; created_at: string }>;
  artifacts: string[];
}> {
  return apiFetch(`/admin/orders/${id}`);
}

export async function retryPlace(orderId: string): Promise<void> {
  return apiFetch(`/admin/orders/${orderId}/retry-place`, { method: "POST" });
}

export async function forceTracking(orderId: string): Promise<void> {
  return apiFetch(`/admin/orders/${orderId}/force-tracking`, { method: "POST" });
}

export async function cancelRefund(orderId: string): Promise<void> {
  return apiFetch(`/admin/orders/${orderId}/cancel-refund`, { method: "POST" });
}

export async function createTicket(
  orderId: string,
  body: { type: string; subject?: string; note?: string }
): Promise<{ ticket_id: string }> {
  return apiFetch(`/admin/orders/${orderId}/create-ticket`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── Tickets ───────────────────────────────────────────────────────────────────
export interface TicketItem {
  id: string;
  order_id: string | null;
  type: string;
  status: string;
  subject: string | null;
  created_by: string | null;
  closed_at: string | null;
  created_at: string;
}

export interface TicketsResponse {
  total: number;
  page: number;
  page_size: number;
  items: TicketItem[];
}

export async function listTickets(params?: {
  status?: string;
  type?: string;
  q?: string;
  page?: number;
}): Promise<TicketsResponse> {
  const sp = new URLSearchParams();
  if (params?.status) sp.set("status", params.status);
  if (params?.type)   sp.set("type",   params.type);
  if (params?.q)      sp.set("q",      params.q);
  if (params?.page)   sp.set("page",   String(params.page));
  const qs = sp.toString() ? `?${sp.toString()}` : "";
  return apiFetch(`/admin/tickets${qs}`);
}

export async function closeTicket(id: string): Promise<void> {
  return apiFetch(`/admin/tickets/${id}/close`, { method: "POST" });
}

// ── Health ────────────────────────────────────────────────────────────────────
export interface HealthResponse {
  db_ok: boolean;
  redis_ok: boolean;
  queue_depth: number | null;
  recent_failures_24h: Array<{ event_type: string; count: number }>;
}

export async function getHealth(): Promise<HealthResponse> {
  return apiFetch("/admin/health");
}
