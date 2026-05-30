import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "BabyGrowHelper · Phase 1",
  description: "Local-first parenting companion · signals layer",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen font-sans antialiased">
        <header className="border-b border-stone-200 bg-white">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
            <div className="flex items-baseline gap-3">
              <Link
                href="/log"
                className="text-lg font-semibold tracking-tight"
              >
                BabyGrowHelper
              </Link>
              <span className="text-xs text-stone-500">phase 1 · 信号</span>
            </div>
            <nav className="flex gap-5 text-sm">
              <Link href="/log" className="hover:text-stone-900 text-stone-600">
                记一笔
              </Link>
              <Link
                href="/timeline"
                className="hover:text-stone-900 text-stone-600"
              >
                时间轴
              </Link>
              <Link
                href="/heatmap"
                className="hover:text-stone-900 text-stone-600"
              >
                热度图
              </Link>
              <Link
                href="/weekly"
                className="hover:text-stone-900 text-stone-600"
              >
                周报
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
