import { getApps } from "@/lib/apps";
import { buildItmsPlist } from "@/lib/plist";

/**
 * Dynamic itms-services manifest: /apps/<slug>/itms.plist
 *
 * Rendered from the same apps.json the page uses — one data source, so the
 * manifest can never disagree with the download card. The manifest must always
 * be fresh (a stale one installs an old build), hence max-age=0; the apps.json
 * fetch behind it is itself cached + revalidated on demand via the 'apps' tag.
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params;
  const app = (await getApps()).find((a) => a.slug === slug);
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
