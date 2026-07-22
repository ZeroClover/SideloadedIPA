import { revalidateTag } from "next/cache";

/**
 * CI webhook: after the signing pipeline updates apps.json on R2, it calls
 * this endpoint with the shared secret to invalidate the 'apps' cache tag.
 * The next page / plist request then re-reads apps.json — no redeploy needed.
 */
export async function GET(request: Request) {
  const secret = request.headers.get("x-revalidate-secret");
  if (!secret || secret !== process.env.REVALIDATE_SECRET) {
    return Response.json({ message: "invalid secret" }, { status: 401 });
  }
  revalidateTag("apps", "max");
  return Response.json({ revalidated: true, now: Date.now() });
}
