/**
 * Download-page data source: site/apps.json on Cloudflare R2.
 *
 * apps.json is the SINGLE source of truth for both the download page and the
 * dynamic itms.plist route. It lives only on R2 (never in git) and is generated
 * by the signing pipeline (first full build seeds it). Reads are tagged
 * ('apps') so the /api/revalidate hook can refresh the cache on demand — no
 * redeploy needed.
 *
 * When R2_APPS_JSON_URL is not set (local dev / preview spin-up before the
 * first pipeline run), the bundled fixture (fixtures/apps.json) is used so the
 * page still renders meaningful cards.
 */
import fixture from "@/fixtures/apps.json";

export interface AppEntry {
  slug: string;
  name: string;
  bundleId: string;
  version: string;
  ipaUrl: string;
  /**
   * Content-addressed icon URL (apps/<slug>/icon-<sha12>.png), served immutable:
   * a changed icon arrives as a NEW URL rather than waiting out the zone's
   * browser-cache TTL. Never rebuild it from the slug — the hash is the only way
   * to know the current key. May be "" when an app has no icon yet (the pipeline
   * leaves it empty rather than pointing at a key that does not exist); AppCard
   * renders a lettered tile in that case.
   */
  iconUrl: string;
}

export async function getApps(): Promise<AppEntry[]> {
  const url = process.env.R2_APPS_JSON_URL;
  if (!url) {
    console.warn("[apps] R2_APPS_JSON_URL not set - using bundled fixture");
    return fixture.apps as AppEntry[];
  }
  try {
    const res = await fetch(url, { next: { tags: ["apps"] } });
    if (!res.ok) {
      console.warn(`[apps] apps.json fetch failed: HTTP ${res.status}`);
      return [];
    }
    const data: unknown = await res.json();
    const apps = (data as { apps?: unknown } | null)?.apps;
    return Array.isArray(apps) ? (apps as AppEntry[]) : [];
  } catch (error) {
    console.warn(`[apps] apps.json fetch error: ${error}`);
    return [];
  }
}
