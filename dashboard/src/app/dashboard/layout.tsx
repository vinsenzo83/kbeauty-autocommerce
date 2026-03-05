"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import clsx from "clsx";

const NAV = [
  { href: "/dashboard",           label: "Overview"  },
  { href: "/dashboard/orders",    label: "Orders"    },
  { href: "/dashboard/metrics",   label: "Metrics"   },
  { href: "/dashboard/tickets",   label: "Tickets"   },
  { href: "/dashboard/health",    label: "Health"    },
  { href: "/dashboard/publish",   label: "🚀 Publish" },  // Sprint 12
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { email, role, logout, loading } = useAuth();
  const pathname = usePathname();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-400">
        Loading…
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="w-56 bg-slate-800 text-slate-200 flex flex-col">
        <div className="px-5 py-5 border-b border-slate-700">
          <p className="text-sm font-bold text-white">KBeauty Admin</p>
          <p className="text-xs text-slate-400 mt-0.5 truncate">{email}</p>
          <span className="inline-block mt-1 text-xs px-2 py-0.5 rounded-full bg-blue-600 text-white">
            {role}
          </span>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "block rounded-lg px-3 py-2 text-sm font-medium transition",
                pathname === item.href
                  ? "bg-blue-600 text-white"
                  : "text-slate-300 hover:bg-slate-700"
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="px-5 py-4 border-t border-slate-700">
          <button
            onClick={logout}
            className="text-sm text-slate-400 hover:text-white transition"
          >
            Sign out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto bg-gray-50">
        {children}
      </main>
    </div>
  );
}
