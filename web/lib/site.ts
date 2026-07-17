/**
 * Absolute base URL of this deployment, used to build itms.plist URLs that
 * iOS Safari can reach. SITE_PUBLIC_BASE_URL pins production; VERCEL_URL
 * covers preview deployments automatically; localhost for `next dev`.
 */
export function siteBaseUrl(): string {
  const explicit = process.env.SITE_PUBLIC_BASE_URL;
  if (explicit) return explicit.replace(/\/+$/, "");
  if (process.env.VERCEL_URL) return `https://${process.env.VERCEL_URL}`;
  return "http://localhost:3000";
}
