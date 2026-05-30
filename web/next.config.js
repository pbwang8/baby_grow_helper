/** @type {import('next').NextConfig} */
// Phase 1: API runs on :8000 (FastAPI). Frontend on :3000.
// Single-user local app — no env-driven base URL beyond NEXT_PUBLIC_API_BASE.
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
};

module.exports = nextConfig;
