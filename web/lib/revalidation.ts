export type RevalidateApps = (tag: "apps", profile: "max") => void;

export async function handleRevalidation(
  request: Request,
  configuredSecret: string | undefined,
  revalidate: RevalidateApps,
): Promise<Response> {
  const suppliedSecret = request.headers.get("x-revalidate-secret");
  if (!configuredSecret || !suppliedSecret || suppliedSecret !== configuredSecret) {
    return Response.json({ message: "invalid secret" }, { status: 401 });
  }
  revalidate("apps", "max");
  return Response.json({ revalidated: true, now: Date.now() });
}
