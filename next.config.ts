import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root so Turbopack ignores stray lockfiles further up the tree.
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
