import Link from "next/link";
import { getStats, getGenreDistribution, getTopDjs, getRecentSets } from "@/lib/db";
import { genreBadgeClass } from "@/lib/analysis";

export const dynamic = "force-dynamic";

export default function Dashboard() {
  const stats = getStats();
  const genres = getGenreDistribution();
  const topDjs = getTopDjs();
  const recentSets = getRecentSets();
  const totalGenres = genres.reduce((s, g) => s + g.count, 0);

  return (
    <div className="space-y-10">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {[
          { label: "DJs", value: String(stats.djCount) },
          { label: "Sets", value: String(stats.setCount) },
          { label: "Tracks", value: stats.trackCount.toLocaleString() },
          { label: "Classified", value: stats.classCount.toLocaleString() },
        ].map((s) => (
          <div key={s.label} className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <div className="text-2xl font-bold text-white">{s.value}</div>
            <div className="text-xs text-zinc-500 uppercase tracking-wider mt-1">{s.label}</div>
          </div>
        ))}
      </div>

      <div className="grid lg:grid-cols-2 gap-8">
        {/* Genre Distribution */}
        <div>
          <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider mb-4">
            Genre Distribution
          </h2>
          <div className="space-y-2">
            {genres.slice(0, 12).map((g) => (
              <div key={g.genre} className="flex items-center gap-3">
                <span className={`text-xs px-2 py-0.5 rounded border ${genreBadgeClass(g.genre)} w-32 text-center truncate`}>
                  {g.genre}
                </span>
                <div className="flex-1 bg-zinc-800 rounded-full h-2 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-zinc-500"
                    style={{ width: `${(g.count / totalGenres) * 100}%` }}
                  />
                </div>
                <span className="text-xs text-zinc-500 w-14 text-right">
                  {g.count.toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Top DJs */}
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider">
              Top DJs by Sets
            </h2>
            <Link href="/djs" className="text-xs text-zinc-500 hover:text-zinc-300">
              View all →
            </Link>
          </div>
          <div className="space-y-1">
            {topDjs.map((dj) => (
              <Link
                key={dj.slug}
                href={`/dj/${dj.slug}`}
                className="flex items-center justify-between px-3 py-2 rounded hover:bg-zinc-800/50 transition group"
              >
                <span className="text-sm text-zinc-200 group-hover:text-white">
                  {dj.name}
                </span>
                <span className="text-xs text-zinc-500">
                  {dj.sets} sets · {dj.totalResolved} tracks
                </span>
              </Link>
            ))}
          </div>
        </div>
      </div>

      {/* Recent Sets */}
      <div>
        <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider mb-4">
          Recent Sets
        </h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {recentSets.map((s) => (
            <Link
              key={s.setId}
              href={`/set/${encodeURIComponent(s.setId)}`}
              className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 hover:border-zinc-600 transition group"
            >
              <div className="text-sm font-medium text-zinc-200 group-hover:text-white truncate">
                {s.title || s.setId}
              </div>
              <div className="text-xs text-zinc-500 mt-1">
                {s.djName} · {s.setDate || "unknown date"}
              </div>
              <div className="text-xs text-zinc-600 mt-1">
                {s.trackCount} tracks · {s.resolvedCount} resolved
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
