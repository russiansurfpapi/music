import Link from "next/link";
import { getAllDjs, getDjGenres } from "@/lib/db";
import { genreBadgeClass } from "@/lib/analysis";

export const dynamic = "force-dynamic";

export default function DJsPage({
  searchParams,
}: {
  searchParams: { q?: string };
}) {
  const search = searchParams.q || "";
  const djs = getAllDjs();
  const djGenres = getDjGenres();

  const filtered = search
    ? djs.filter(
        (d) =>
          d.name.toLowerCase().includes(search.toLowerCase()) ||
          d.slug.toLowerCase().includes(search.toLowerCase())
      )
    : djs;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-white">DJs</h1>
        <span className="text-xs text-zinc-500">{filtered.length} total</span>
      </div>

      <form className="mb-6">
        <input
          type="text"
          name="q"
          defaultValue={search}
          placeholder="Search DJs..."
          className="w-full sm:w-80 bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
        />
      </form>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {filtered.map((dj) => {
          const genres = (djGenres[dj.slug] || []).slice(0, 3);
          return (
            <Link
              key={dj.slug}
              href={`/dj/${dj.slug}`}
              className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 hover:border-zinc-600 transition group"
            >
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-zinc-200 group-hover:text-white">
                  {dj.name}
                </span>
                {dj.isFavorite ? (
                  <span className="text-yellow-500 text-xs">★</span>
                ) : null}
              </div>
              <div className="text-xs text-zinc-500 mt-1">
                {dj.sets} sets · {dj.totalResolved} resolved tracks
              </div>
              {genres.length > 0 && (
                <div className="flex gap-1 mt-2 flex-wrap">
                  {genres.map((g) => (
                    <span
                      key={g.genre}
                      className={`text-[10px] px-1.5 py-0.5 rounded border ${genreBadgeClass(g.genre)}`}
                    >
                      {g.genre}
                    </span>
                  ))}
                </div>
              )}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
