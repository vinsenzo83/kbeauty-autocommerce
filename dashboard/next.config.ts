import type { NextConfig } from "next";

const API_URL = process.env.API_URL || "http://api:8000";

const nextConfig: NextConfig = {
  // Required for Docker multi-stage standalone build (Dockerfile.dashboard)
  output: "standalone",

  async rewrites() {
    return [
      // Browser-side requests: /admin/* → backend /admin/*
      {
        source: "/admin/:path*",
        destination: `${API_URL}/admin/:path*`,
      },
      // Legacy path kept for backwards-compat
      {
        source: "/api/admin/:path*",
        destination: `${API_URL}/admin/:path*`,
      },
    ];
  },
};

export default nextConfig;
