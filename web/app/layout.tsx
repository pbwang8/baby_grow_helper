import "./globals.css";
import type { Metadata, Viewport } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "BabyGrowHelper",
  description: "Local-first parenting companion for invited family trials",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    title: "BabyGrow",
    statusBarStyle: "default",
  },
  icons: {
    icon: "/icons/babygrow-icon.svg",
    apple: "/icons/babygrow-icon.svg",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#fafaf7",
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
          <div className="mx-auto flex max-w-5xl flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <div className="flex items-baseline gap-3">
              <Link
                href="/log"
                className="text-lg font-semibold tracking-tight"
              >
                BabyGrowHelper
              </Link>
              <span className="text-xs text-stone-500">family trial</span>
            </div>
            <nav className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
              <Link
                href="/login"
                className="hover:text-stone-900 text-stone-600"
              >
                家庭
              </Link>
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
        <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
          {children}
        </main>
      </body>
    </html>
  );
}
