import { handleItmsRequest } from "@/lib/itms-route";

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
  return handleItmsRequest(slug);
}
