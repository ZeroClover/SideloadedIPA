import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // cacheComponents stays disabled; on-demand ISR via revalidateTag('apps','max')
  // is the only cache-invalidation channel for the explicitly cached registry.
};

export default nextConfig;
