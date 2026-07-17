"use client";

import { useState } from "react";
import type { AppEntry } from "@/lib/apps";
import AppCard from "./AppCard";

export interface GridApp extends AppEntry {
  plistUrl: string;
}

/**
 * Header + live search + card grid (client component: filtering is interactive).
 * Cards match by display name or bundle id, as in the legacy page.
 */
export default function AppGrid({ apps }: { apps: GridApp[] }) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const visible = apps.filter(
    (app) =>
      !q || app.name.toLowerCase().includes(q) || app.bundleId.toLowerCase().includes(q),
  );

  return (
    <>
      <header className="header">
        <div className="header-top">
          <div className="title-block">
            <span className="eyebrow">Zero ITMS Service</span>
            <h1 className="title">Sideload IPAs</h1>
          </div>
          <span className="count" aria-live="polite">
            {apps.length} 个应用
          </span>
        </div>

        <div className="search">
          <svg
            className="search-icon"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden="true"
          >
            <circle cx="11" cy="11" r="7"></circle>
            <line x1="21" y1="21" x2="16.5" y2="16.5"></line>
          </svg>
          <input
            id="search"
            type="search"
            placeholder="搜索应用…"
            aria-label="搜索应用"
            autoComplete="off"
            spellCheck={false}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </header>

      <div className="grid">
        {visible.map((app) => (
          <AppCard
            key={app.slug}
            name={app.name}
            slug={app.bundleId}
            version={app.version}
            icon={app.iconUrl}
            manifest={app.plistUrl}
          />
        ))}
      </div>

      {visible.length === 0 && (
        <p className="empty">
          没有匹配「{query}」的应用
        </p>
      )}
    </>
  );
}
