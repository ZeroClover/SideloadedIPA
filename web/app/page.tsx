import AppGrid from "@/components/AppGrid";
import { getApps } from "@/lib/apps";
import { siteBaseUrl } from "@/lib/site";

export default async function Page() {
  const apps = await getApps();
  const base = siteBaseUrl();
  const cards = apps.map((app) => ({
    ...app,
    plistUrl: `${base}/apps/${encodeURIComponent(app.slug)}/itms.plist`,
  }));

  return (
    <main className="page">
      <div className="inner">
        <AppGrid apps={cards} />
      </div>
    </main>
  );
}
