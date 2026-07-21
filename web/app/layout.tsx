import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sideload IPAs · Zero ITMS Service",
  description: "Zero ITMS App 分发 · 通过 itms-services 安装重签名 IPA。",
};

// Browser chrome follows the page background in both appearances.
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#eef0f5" },
    { media: "(prefers-color-scheme: dark)", color: "#0b0d12" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // System font stack (SF Pro / PingFang on Apple devices) is set in globals.css —
  // closer to the Liquid Glass aesthetic than bundled webfonts, with zero download cost.
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
