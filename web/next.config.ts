import type { NextConfig } from "next";

// Security headers. The console connects to several service origins (NEXT_PUBLIC_*)
// plus MediaMTX for WebRTC/HLS, so connect-src/media-src are permissive for http(s)
// and ws(s); everything else is locked down. Tighten connect-src to explicit origins
// in a fixed deployment.
const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      "img-src 'self' data: blob: https: http:",
      "media-src 'self' blob: https: http:",
      "connect-src 'self' https: http: ws: wss:",
      "script-src 'self' 'unsafe-inline'",
      "style-src 'self' 'unsafe-inline'",
      "frame-ancestors 'none'",
      "base-uri 'self'",
    ].join("; "),
  },
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Ingest and core-API are separate services; the console talks to them over
  // their own origins (see NEXT_PUBLIC_* env). No rewrites needed in dev.
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
