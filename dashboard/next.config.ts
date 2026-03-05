import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/admin/:path*",
        destination: `${process.env.API_URL || "http://api:8000"}/admin/:path*`,
      },
    ];
  },
};

export default nextConfig;
