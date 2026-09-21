import Link from "next/link";
import { notFound } from "next/navigation";
import { getSetMeta, getSetTracks } from "@/lib/db";
import { analyzeSet, genreBadgeClass, familyColor } from "@/lib/analysis";
import { isAuthenticated } from "@/lib/spotify";
import { CreatePlaylistButton } from "./playlist-button";

export const dynamic = "force-dynamic";

export default function SetDetailPage({ params }: { params: { id: string } }) {
  const setId = decodeURIComponent(params.id);
  const meta = getSetMeta(setId);
  if (!meta) notFound();

  const tracks = getSetTracks(setId);
  const analysis = analyzeSet(tracks.map((t) => ({ subgenre: t.subgenre, genre: t.genre })));
  const authed = isAuthenticated();

  const validSpotifyIds = tracks
    .map((t) => t.spotifyId)
    .filter(
      (id): id is string =>
        !!id && !id.startsWith("lfm:") && !id.startsWith("fp:") && /^[a-zA-Z0-9]{22}$/.test(id)
    );

  return (
    <div className="space-y-6">
      <div>
        <Link href={`/dj/${meta.djSlug}`} className="text-xs text-zinc-500 hover:text-zinc-300">
          ← {meta.djName}
        </Link>
        <h1 className="text-xl font-bold text-white mt-2">{meta.title || meta.setId}</h1>
        <div className="text-sm text-zinc-400 mt-1 flex items-center gap-3 flex-wrap">
          <span>{meta.djName}</span>
          <span className="text-zinc-600">·</span>
          <span>{meta.setDate || "unknown date"}</span>
          <span className="text-zinc-600">·</span>
          <span>{meta.trackCount} tracks</span>
          {meta.youtubeUrl && (
            <>
              <span className="text-zinc-600">·</span>
              <a href={meta.youtubeUrl} target="_blank" rel="noopener" className="text-red-400 hover:text-red-300">
                YouTube
              </a>
            </>
          )}
        </div>
      </div>

      <div className="flex gap-3">
        {authed ? (
          <CreatePlaylistButton
            setId={setId}
            defaultName={meta.title || `${meta.djName} - ${meta.setDate}`}
            trackCount={validSpotifyIds.length}
          />
        ) : (
          <a href="/api/auth/login" className="text-sm bg-green-700 hover:bg-green-600 text-white rounded-lg px-4 py-2 transition">
            Connect Spotify to create playlist
          </a>
        )}
      </div>

      {analysis.n > 3 && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider">Set Shape</h2>
            <span className="text-xs font-mono text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded">{analysis.shape}</span>
          </div>
          <div className="flex gap-px h-6 rounded overflow-hidden mb-3">
            {analysis.trackColors.map((tc, i) => (
              <div key={i} className="flex-1 relative group cursor-pointer" style={{ backgroundColor: tc.hex }}>
                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 hidden group-hover:block z-10 bg-zinc-800 text-xs text-zinc-200 px-2 py-1 rounded whitespace-nowrap shadow-lg">
                  #{i + 1}: {tc.subgenre || tc.genre || "?"}
                </div>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
            <div>
              <span className="text-zinc-500">Transitions</span>
              <div className="text-zinc-200 font-medium">{analysis.transitions}/{analysis.n - 1} ({Math.round(analysis.transitionRate * 100)}%)</div>
            </div>
            <div>
              <span className="text-zinc-500">Family crossings</span>
              <div className="text-zinc-200 font-medium">{analysis.familyTransitions}</div>
            </div>
            <div>
              <span className="text-zinc-500">Longest plateau</span>
              <div className="text-zinc-200 font-medium">{analysis.maxRun} tracks</div>
            </div>
            <div>
              <span className="text-zinc-500">Distinct subgenres</span>
              <div className="text-zinc-200 font-medium">{analysis.distinct}</div>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-1 text-[10px]">
            <span className="text-zinc-500 w-10">OPEN</span>
            {analysis.quintileTops.map((qt, i) => (
              <span key={i} className="flex-1 text-center text-zinc-400 bg-zinc-800 py-0.5 rounded truncate">{qt}</span>
            ))}
            <span className="text-zinc-500 w-10 text-right">CLOSE</span>
          </div>
          <div className="mt-3 flex flex-wrap gap-1">
            {analysis.topSubgenres.map(([name, count]) => {
              const color = familyColor(name, null);
              return (
                <span key={name} className="text-[10px] px-1.5 py-0.5 rounded" style={{ backgroundColor: color.hex + "20", color: color.hex }}>
                  {name} ({count})
                </span>
              );
            })}
          </div>
        </div>
      )}

      <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
        <div className="px-5 py-3 border-b border-zinc-800">
          <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider">Tracklist ({tracks.length})</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-zinc-500 border-b border-zinc-800">
                <th className="px-4 py-2 text-left w-10">#</th>
                <th className="px-4 py-2 text-left">Artist</th>
                <th className="px-4 py-2 text-left">Title</th>
                <th className="px-4 py-2 text-left">Genre</th>
                <th className="px-4 py-2 text-left">Subgenre</th>
                <th className="px-4 py-2 text-right">Year</th>
              </tr>
            </thead>
            <tbody>
              {tracks.map((t, i) => {
                const color = familyColor(t.subgenre, t.genre);
                return (
                  <tr key={i} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                    <td className="px-4 py-2 text-zinc-600 tabular-nums">{t.position}</td>
                    <td className="px-4 py-2 text-zinc-200">{t.rawArtist}</td>
                    <td className="px-4 py-2 text-zinc-300">{t.rawTitle}</td>
                    <td className="px-4 py-2">
                      {t.genre && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded border ${genreBadgeClass(t.genre)}`}>{t.genre}</span>
                      )}
                    </td>
                    <td className="px-4 py-2">
                      {t.subgenre && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ backgroundColor: color.hex + "20", color: color.hex }}>
                          {t.subgenre}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right text-zinc-500 tabular-nums text-xs">{t.year || ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
