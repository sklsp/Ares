import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root so Turbopack ignores stray lockfiles further up the tree.
  turbopack: {
    root: __dirname,
  },
  // Emit a self-contained server bundle so the Docker runtime image does not
  // need node_modules. No effect on `next dev`.
  output: "standalone",
};

export default nextConfig;
