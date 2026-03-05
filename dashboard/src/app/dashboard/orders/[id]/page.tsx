"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  getOrder,
  retryPlace,
  forceTracking,
  cancelRefund,
  createTicket,
} from "@/lib/api";

type OrderDetail = Awaited<ReturnType<typeof getOrder>>;

const STATUS_COLORS: Record<string, string> = {
  RECEIVED:  "bg-blue-100 text-blue-700",
  VALIDATED: "bg-indigo-100 text-indigo-700",
  PLACING:   "bg-yellow-100 text-yellow-700",
  PLACED:    "bg-cyan-100 text-cyan-700",
  SHIPPED:   "bg-green-100 text-green-700",
  FAILED:    "bg-red-100 text-red-700",
  CANCELED:  "bg-gray-100 text-gray-600",
};

function ActionButton({
  label,
  onClick,
  variant = "default",
  disabled = false,
}: {
  label: string;
  onClick: () => void;
  variant?: "default" | "danger" | "warning";
  disabled?: boolean;
}) {
  const base = "px-3 py-1.5 rounded-lg text-sm font-medium transition disabled:opacity-50";
  const colors = {
    default: "bg-blue-600 text-white hover:bg-blue-700",
    danger:  "bg-red-600 text-white hover:bg-red-700",
    warning: "bg-amber-500 text-white hover:bg-amber-600",
  };
  return (
    <button className={`${base} ${colors[variant]}`} onClick={onClick} disabled={disabled}>
      {label}
    </button>
  );
}

export default function OrderDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params?.id as string;

  const [order, setOrder]   = useState<OrderDetail | null>(null);
  const [error, setError]   = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    getOrder(id)
      .then(setOrder)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  const act = async (fn: () => Promise<unknown>, msg: string) => {
    setActing(true);
    setActionMsg(null);
    try {
      await fn();
      setActionMsg(`✓ ${msg} succeeded`);
      // Refresh order details
      const updated = await getOrder(id);
      setOrder(updated);
    } catch (e: unknown) {
      const err = e instanceof Error ? e.message : String(e);
      setActionMsg(`✗ ${msg} failed: ${err}`);
    } finally {
      setActing(false);
    }
  };

  if (loading) {
    return (
      <div className="p-6 text-slate-400">Loading order…</div>
    );
  }

  if (error || !order) {
    return (
      <div className="p-6 text-red-600">
        {error ?? "Order not found."}
        <button className="ml-4 underline text-blue-600" onClick={() => router.back()}>
          ← Back
        </button>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button onClick={() => router.back()} className="text-slate-500 hover:text-slate-800 text-sm">
          ← Back
        </button>
        <h1 className="text-2xl font-bold text-slate-800">
          Order {order.shopify_order_id}
        </h1>
        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[order.status] ?? "bg-gray-100 text-gray-600"}`}>
          {order.status}
        </span>
      </div>

      {/* Action Message */}
      {actionMsg && (
        <div className={`rounded-lg px-4 py-2 text-sm font-medium ${actionMsg.startsWith("✓") ? "bg-green-50 text-green-700 border border-green-200" : "bg-red-50 text-red-700 border border-red-200"}`}>
          {actionMsg}
        </div>
      )}

      {/* Core Details */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-100 p-5">
        <h2 className="text-sm font-semibold text-slate-500 uppercase mb-3">Order Details</h2>
        <dl className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-sm">
          {[
            ["Email",           order.email],
            ["Total",           `${order.total_price} ${order.currency}`],
            ["Financial",       order.financial_status],
            ["Supplier",        order.supplier ?? "—"],
            ["Supplier Order",  order.supplier_order_id ?? "—"],
            ["Placed At",       order.placed_at?.slice(0,19).replace("T"," ") ?? "—"],
            ["Shipped At",      order.shipped_at?.slice(0,19).replace("T"," ") ?? "—"],
            ["Tracking #",      order.tracking_number ?? "—"],
            ["Fail Reason",     order.fail_reason ?? "—"],
            ["Created",         order.created_at.slice(0,19).replace("T"," ")],
          ].map(([label, val]) => (
            <div key={label as string}>
              <dt className="text-xs font-medium text-slate-500">{label}</dt>
              <dd className="mt-0.5 text-slate-800 font-medium">{val as string}</dd>
            </div>
          ))}
        </dl>
        {order.tracking_url && (
          <p className="mt-3 text-sm">
            <a href={order.tracking_url} target="_blank" rel="noreferrer" className="text-blue-600 underline">
              Track shipment →
            </a>
          </p>
        )}
      </div>

      {/* Actions */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-100 p-5">
        <h2 className="text-sm font-semibold text-slate-500 uppercase mb-3">Actions</h2>
        <div className="flex flex-wrap gap-2">
          <ActionButton
            label="Retry Place"
            onClick={() => act(() => retryPlace(id), "Retry Place")}
            disabled={acting}
          />
          <ActionButton
            label="Force Tracking"
            onClick={() => act(() => forceTracking(id), "Force Tracking")}
            variant="warning"
            disabled={acting}
          />
          <ActionButton
            label="Cancel & Refund"
            onClick={() => act(() => cancelRefund(id), "Cancel & Refund")}
            variant="danger"
            disabled={acting}
          />
          <ActionButton
            label="Create Ticket"
            onClick={() =>
              act(
                () => createTicket(id, { type: "manual", subject: "Admin raised", note: "Created from dashboard" }),
                "Create Ticket"
              )
            }
            disabled={acting}
          />
        </div>
      </div>

      {/* Shipping Address */}
      {order.shipping_address && Object.keys(order.shipping_address).length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-100 p-5">
          <h2 className="text-sm font-semibold text-slate-500 uppercase mb-3">Shipping Address</h2>
          <pre className="text-xs text-slate-600 whitespace-pre-wrap">
            {JSON.stringify(order.shipping_address, null, 2)}
          </pre>
        </div>
      )}

      {/* Line Items */}
      {Array.isArray(order.line_items) && order.line_items.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden">
          <h2 className="text-sm font-semibold text-slate-500 uppercase p-5 pb-3">Line Items</h2>
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                {["SKU","Title","Qty","Price"].map((h) => (
                  <th key={h} className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {(order.line_items as Array<Record<string, unknown>>).map((item, i) => (
                <tr key={i} className="hover:bg-slate-50">
                  <td className="px-4 py-2 font-mono text-xs">{String(item.sku ?? "—")}</td>
                  <td className="px-4 py-2">{String(item.title ?? "—")}</td>
                  <td className="px-4 py-2">{String(item.quantity ?? "—")}</td>
                  <td className="px-4 py-2">{String(item.price ?? "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Event Log */}
      {Array.isArray(order.events) && order.events.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden">
          <h2 className="text-sm font-semibold text-slate-500 uppercase p-5 pb-3">Event Log</h2>
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                {["Time","Source","Type","Note"].map((h) => (
                  <th key={h} className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {order.events.map((ev) => (
                <tr key={ev.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2 text-xs text-slate-400 whitespace-nowrap">
                    {ev.created_at.slice(0,19).replace("T"," ")}
                  </td>
                  <td className="px-4 py-2 text-slate-500">{ev.source}</td>
                  <td className="px-4 py-2 font-medium text-slate-700">{ev.event_type}</td>
                  <td className="px-4 py-2 text-slate-600">{ev.note ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Artifacts */}
      {Array.isArray(order.artifacts) && order.artifacts.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-100 p-5">
          <h2 className="text-sm font-semibold text-slate-500 uppercase mb-3">Artifacts</h2>
          <ul className="text-sm space-y-1">
            {order.artifacts.map((a, i) => (
              <li key={i} className="font-mono text-xs text-blue-600 hover:underline">
                <a href={`/api/admin/artifacts?path=${encodeURIComponent(a)}`} target="_blank" rel="noreferrer">
                  {a}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
