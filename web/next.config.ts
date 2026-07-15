import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Ingest and core-API are separate services; the console talks to them over
  // their own origins (see NEXT_PUBLIC_* env). No rewrites needed in dev.
};

export default nextConfig;
