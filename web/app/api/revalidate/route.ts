import { revalidateTag } from "next/cache";
import { handleRevalidation } from "@/lib/revalidation";

/**
 * CI webhook: after the signing pipeline updates apps.json on R2, it calls
 * this endpoint with the shared secret to invalidate the 'apps' cache tag.
 * The next page / plist request then re-reads apps.json — no redeploy needed.
 */
export async function GET(request: Request) {
  return handleRevalidation(request, process.env.REVALIDATE_SECRET, revalidateTag);
}
