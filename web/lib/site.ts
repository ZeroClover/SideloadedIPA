/**
 * Absolute base URL of this deployment, used to build itms.plist URLs that
 * iOS Safari / appstored can reach. SITE_PUBLIC_BASE_URL pins the public site
 * domain (set for production at DNS cutover). Fallback is
 * VERCEL_PROJECT_PRODUCTION_URL — the stable production alias.
 *
 * Never use VERCEL_URL here: it is the per-deploy ephemeral URL
 * (<project>-<hash>-<team>.vercel.app), which is behind Deployment Protection
 * (302 to a login page for anonymous fetchers like appstored) and changes on
 * every deploy.
 */
export function siteBaseUrl(): string {
  const explicit = process.env.SITE_PUBLIC_BASE_URL;
  if (explicit) return explicit.replace(/\/+$/, "");
  if (process.env.VERCEL_PROJECT_PRODUCTION_URL)
    return `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`;
  return "http://localhost:3000";
}
