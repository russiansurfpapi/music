import Link from "next/link";
import { notFound } from "next/navigation";
import { getDj, getDjSets, getDjGenreBreakdown, getDjSubgenreBreakdown, getDjArchetypeTracks, getSetTracksForShape } from "@/lib/db";
import { analyzeSet, computeArchetypeAxes, genreBadgeClass, FAMILY_COLORS, FAMILY } from "@/lib/analysis";

export const dynamic = "force-dynamic";

export default function DJDetailPage({ params }: { params: { slug: string } }) {
  const slug = decodeURIComponent(params.slug);
  const dj = getDj(slug);
  if (!dj) notFound();

  const sets = getDjSets(slug);
  const genres = getDjGenreBreakdown(slug);
  const subgenres = getDjSubgenreBreakdown(slug);
  const archetypeTracks = getDjArchetypeTracks(slug);
  const archetype = computeArchetypeAxes(archetypeTracks);

  const setShapes = sets.slice(0, 10).map((s) => {
    const tracks = getSetTracksForShape(s.setId);
    return { setId: s.setId, analysis: analyzeSet(tracks) };
  });
  const shapeMap: Record<string, ReturnType<typeof analyzeSet>> = {};
  for (const ss of setShapes) shapeMap[ss.setId] = ss.analysis;

  const totalGenreTracks = genres.reduce((s, g) => s + g.count, 0);
  const totalSubTracks = subgenres.reduce((s, g) => s + g.count, 0);

  return (
    <div className="space-y-8">
      <div>
        <Link href="/djs" className="text-xs text-zinc-500 hover:text-zinc-300">
          ← All DJs
        </Link>
        <h1 className="text-2xl font-bold text-white mt-2 flex items-center gap-2">
          {dj.name}
          {dj.isFavorite ? <span className="text-yellow-500">★</span> : null}
        </h1>
      </div>

      <div className="grid lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 space-y-6">
          {archetype && (
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
              <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider mb-4">Archetype</h2>
              <div className="grid grid-cols-2 gap-4">
                <AxisBar label="Breadth" value={archetype.breadth} max={5} detail={`${archetype.distinctSubgenres} subgenres`} />
                <AxisBar label="Flow" value={archetype.flow} max={8} detail={`median run ${archetype.flow}`} />
                <AxisBar label="Anchor Loyalty" value={archetype.anchorLoyalty} max={1} detail={archetype.anchorGenre} />
                <div className="flex flex-col">
                  <span className="text-xs text-zinc-500">Era Median</span>
                  <span className="text-lg font-bold text-white">{archetype.eraMedian || "—"}</span>
                </div>
              </div>
              <div className="text-xs text-zinc-600 mt-3">
                {archetype.totalClassified} classified tracks across {sets.length} sets
              </div>
            </div>
          )}

          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider mb-4">Genre Breakdown</h2>
            <div className="space-y-2">
              {genres.map((g) => (
                <div key={g.genre} className="flex items-center gap-3">
                  <span className={`text-xs px-2 py-0.5 rounded border ${genreBadgeClass(g.genre)} w-36 text-center truncate`}>
                    {g.genre}
                  </span>
                  <div className="flex-1 bg-zinc-800 rounded-full h-2 overflow-hidden">
                    <div className="h-full rounded-full bg-zinc-500" style={{ width: `${(g.count / totalGenreTracks) * 100}%` }} />
                  </div>
                  <span className="text-xs text-zinc-500 w-16 text-right">
                    {g.count} ({Math.round((g.count / totalGenreTracks) * 100)}%)
                  </span>
                </div>
              ))}
            </div>
          </div>

          {subgenres.length > 0 && (
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
              <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider mb-4">Top Subgenres</h2>
              <div className="flex flex-wrap gap-2">
                {subgenres.map((sg) => {
                  const family = FAMILY[sg.subgenre];
                  const color = family ? FAMILY_COLORS[family] : null;
                  return (
                    <span
                      key={sg.subgenre}
                      className="text-xs px-2 py-1 rounded-full border border-zinc-700"
                      style={color ? { borderColor: color.hex + "60", color: color.hex } : undefined}
                    >
                      {sg.subgenre}{" "}
                      <span className="text-zinc-500">{Math.round((sg.count / totalSubTracks) * 100)}%</span>
                    </span>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <div>
          <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider mb-4">Sets ({sets.length})</h2>
          <div className="space-y-2">
            {sets.map((s) => {
              const shape = shapeMap[s.setId];
              return (
                <Link
                  key={s.setId}
                  href={`/set/${encodeURIComponent(s.setId)}`}
                  className="block bg-zinc-900 border border-zinc-800 rounded-lg p-3 hover:border-zinc-600 transition group"
                >
                  <div className="text-sm text-zinc-200 group-hover:text-white truncate">
                    {s.title || s.setId}
                  </div>
                  <div className="text-xs text-zinc-500 mt-1">
                    {s.setDate || "?"} · {s.trackCount}t · {s.resolvedCount} resolved
                  </div>
                  {shape && shape.n > 3 && (
                    <div className="mt-2">
                      <div className="flex gap-px h-3 rounded overflow-hidden">
                        {shape.trackColors.map((tc, i) => (
                          <div key={i} className="flex-1" style={{ backgroundColor: tc.hex }} title={tc.subgenre || tc.genre || "?"} />
                        ))}
                      </div>
                      <div className="text-[10px] text-zinc-600 mt-1">{shape.shape} · {shape.distinct} subgenres</div>
                    </div>
                  )}
                </Link>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function AxisBar({ label, value, max, detail }: { label: string; value: number; max: number; detail: string }) {
  const pct = Math.min(100, (value / max) * 100);
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-zinc-500">{label}</span>
        <span className="text-xs text-zinc-400">{value}</span>
      </div>
      <div className="bg-zinc-800 rounded-full h-2 overflow-hidden">
        <div className="h-full rounded-full bg-blue-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-zinc-600">{detail}</span>
    </div>
  );
}
