"use client";

import { useState } from "react";

// Placeholder background colours from the original design (icon fallback).
const PALETTE = ["#1a1a18", "#3a3a36", "#56564f"];

/** Stable palette index derived from the app name (same app = same colour). */
function paletteFor(key: string): string {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

export interface AppCardProps {
  name: string;
  /** Shown under the name — the bundle id, as in the legacy UI. */
  slug: string;
  version?: string;
  icon?: string;
  /** Absolute https URL of the itms.plist manifest. */
  manifest: string;
}

export default function AppCard({ name, slug, version, icon, manifest }: AppCardProps) {
  const [iconFailed, setIconFailed] = useState(false);
  const background = paletteFor(name || slug || "?");

  function download() {
    if (!manifest) return;
    window.open(
      `itms-services://?action=download-manifest&url=${encodeURIComponent(manifest)}`,
      "_blank",
    );
  }

  return (
    <div className="card">
      {/* Neutral tile + hairline keeps solid-white/black icons visible on the glass card. */}
      <div className="icon-tile">
        {icon && !iconFailed ? (
          // Plain <img> on purpose: icons are tiny files served straight from R2.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            className="icon"
            src={icon}
            alt={`${name} 图标`}
            width={84}
            height={84}
            loading="lazy"
            decoding="async"
            style={{ background }}
            onError={() => setIconFailed(true)}
          />
        ) : (
          <div className="icon-fallback" style={{ background }}>
            {(name || "?").charAt(0).toUpperCase()}
          </div>
        )}
      </div>
      <div className="meta">
        <span className="name">{name}</span>
        <span className="slug">{slug}</span>
        {version ? <span className="version">v{version}</span> : null}
      </div>
      <button className="download" type="button" onClick={download} aria-label={`下载 ${name}`}>
        下载
      </button>
    </div>
  );
}
