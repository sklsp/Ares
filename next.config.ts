import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root so Turbopack ignores stray lockfiles further up the tree.
  turbopack: {
    root: __dirname,
  },
  // Emit a self-contained server bundle so the Docker runtime image does not
  // need node_modules. No effect on `next dev`.
  output: "standalone",
  // Security headers on every page. The CSP holds only directives that cannot break the app
  // (Next hydrates with inline scripts, so a script policy would need nonces).
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'; base-uri 'self'; object-src 'none'" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
