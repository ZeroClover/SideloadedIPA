import { createHash, timingSafeEqual } from "node:crypto";

export type RevalidateApps = (tag: "apps", profile: { expire: 0 }) => void;

export async function handleRevalidation(
  request: Request,
  configuredSecret: string | undefined,
  revalidate: RevalidateApps,
): Promise<Response> {
  if (request.method !== "POST") {
    return new Response(null, {
      status: 405,
      headers: { Allow: "POST", "Cache-Control": "no-store" },
    });
  }
  const suppliedSecret = request.headers.get("x-revalidate-secret");
  // Compare fixed-length digests so unequal UTF-8 lengths cannot throw or leak
  // matching prefixes through a short-circuit string comparison.
  if (
    !configuredSecret ||
    !suppliedSecret ||
    !timingSafeEqual(
      createHash("sha256").update(suppliedSecret).digest(),
      createHash("sha256").update(configuredSecret).digest(),
    )
  ) {
    return Response.json(
      { message: "invalid secret" },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }
  revalidate("apps", { expire: 0 });
  return Response.json(
    { revalidated: true, now: Date.now() },
    { headers: { "Cache-Control": "no-store" } },
  );
}
