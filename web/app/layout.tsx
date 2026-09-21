import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { isAuthenticated } from "@/lib/spotify";

export const metadata: Metadata = {
  title: "DJ Library",
  description: "Browse DJs, sets, and create Spotify playlists",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const authed = isAuthenticated();

  return (
    <html lang="en">
      <body className="min-h-screen">
        <nav className="border-b border-zinc-800 bg-zinc-950/80 backdrop-blur sticky top-0 z-50">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between h-14">
            <div className="flex items-center gap-6">
              <Link href="/" className="text-lg font-bold tracking-tight text-white">
                DJ Library
              </Link>
              <div className="flex gap-4 text-sm">
                <Link href="/djs" className="text-zinc-400 hover:text-white transition">
                  DJs
                </Link>
                <Link href="/library" className="text-zinc-400 hover:text-white transition">
                  Library
                </Link>
                <Link href="/create" className="text-zinc-400 hover:text-white transition">
                  Create
                </Link>
                <Link href="/ingest" className="text-zinc-400 hover:text-white transition">
                  Ingest
                </Link>
              </div>
            </div>
            <div>
              {authed ? (
                <span className="text-xs text-green-400 bg-green-950/50 border border-green-800 rounded px-2 py-1">
                  Spotify connected
                </span>
              ) : (
                <a
                  href="/api/auth/login"
                  className="text-xs text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded px-3 py-1.5 transition"
                >
                  Connect Spotify
                </a>
              )}
            </div>
          </div>
        </nav>
        <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
