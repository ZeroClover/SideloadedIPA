import type { AppEntry } from "./apps";
import { getApps } from "./apps";
import { buildItmsPlist } from "./plist";

export type LoadApps = () => Promise<AppEntry[]>;

export async function handleItmsRequest(
  slug: string,
  loadApps: LoadApps = getApps,
): Promise<Response> {
  let apps: AppEntry[];
  try {
    apps = await loadApps();
  } catch {
    return new Response("application registry unavailable", {
      status: 503,
      headers: { "Cache-Control": "no-store", "Retry-After": "60" },
    });
  }
  const app = apps.find((entry) => entry.slug === slug);
  if (!app) {
    return new Response("not found", { status: 404 });
  }
  return new Response(buildItmsPlist(app.ipaUrl, app.bundleId, app.version, app.name), {
    headers: {
      "Content-Type": "text/xml; charset=utf-8",
      "Cache-Control": "public, max-age=0, must-revalidate",
    },
  });
}
