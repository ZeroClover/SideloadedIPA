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
  if (!configuredSecret || !suppliedSecret || suppliedSecret !== configuredSecret) {
    return Response.json({ message: "invalid secret" }, { status: 401 });
  }
  revalidate("apps", { expire: 0 });
  return Response.json(
    { revalidated: true, now: Date.now() },
    { headers: { "Cache-Control": "no-store" } },
  );
}
