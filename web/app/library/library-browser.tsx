"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import type { LibraryTrack } from "@/lib/db";
import { genreBadgeClass, familyColor, FAMILY } from "@/lib/analysis";

const PAGE_SIZE = 50;

export function LibraryBrowser({
  tracks,
  genres,
  subgenres,
}: {
  tracks: LibraryTrack[];
  genres: { genre: string; count: number }[];
  subgenres: { subgenre: string; count: number }[];
}) {
  const [search, setSearch] = useState("");
  const [genreFilter, setGenreFilter] = useState("");
  const [subgenreFilter, setSubgenreFilter] = useState("");
  const [yearMin, setYearMin] = useState("");
  const [yearMax, setYearMax] = useState("");
  const [page, setPage] = useState(0);

  const filteredSubgenres = useMemo(() => {
    if (!genreFilter) return subgenres;
    const familyForGenre = genreFilter;
    return subgenres.filter((sg) => {
      const fam = FAMILY[sg.subgenre];
      if (fam === familyForGenre) return true;
      // Also check if genre matches (for subgenres not in FAMILY map)
      return tracks.some(
        (t) => t.genre === genreFilter && t.subgenre === sg.subgenre
      );
    });
  }, [genreFilter, subgenres, tracks]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    const yMin = yearMin ? parseInt(yearMin) : 0;
    const yMax = yearMax ? parseInt(yearMax) : 9999;
    return tracks.filter((t) => {
      if (genreFilter && t.genre !== genreFilter) return false;
      if (subgenreFilter && t.subgenre !== subgenreFilter) return false;
      if (t.releaseYear && (t.releaseYear < yMin || t.releaseYear > yMax))
        return false;
      if (
        q &&
        !t.artist.toLowerCase().includes(q) &&
        !t.title.toLowerCase().includes(q)
      )
        return false;
      return true;
    });
  }, [tracks, search, genreFilter, subgenreFilter, yearMin, yearMax]);

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
  const pageItems = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function resetFilters() {
    setSearch("");
    setGenreFilter("");
    setSubgenreFilter("");
    setYearMin("");
    setYearMax("");
    setPage(0);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Link href="/" className="text-xs text-zinc-500 hover:text-zinc-300">
            ← Dashboard
          </Link>
          <h1 className="text-xl font-bold text-white mt-2">Library</h1>
        </div>
        <span className="text-sm text-zinc-400">
          {filtered.length.toLocaleString()} of{" "}
          {tracks.length.toLocaleString()} tracks
        </span>
      </div>

      {/* Filters */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
        <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-3">
          <div className="lg:col-span-2">
            <label className="text-xs text-zinc-500 block mb-1">Search</label>
            <input
              type="text"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
              placeholder="Artist or title..."
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
            />
          </div>
          <div>
            <label className="text-xs text-zinc-500 block mb-1">Genre</label>
            <select
              value={genreFilter}
              onChange={(e) => {
                setGenreFilter(e.target.value);
                setSubgenreFilter("");
                setPage(0);
              }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200"
            >
              <option value="">All genres</option>
              {genres.map((g) => (
                <option key={g.genre} value={g.genre}>
                  {g.genre} ({g.count.toLocaleString()})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-zinc-500 block mb-1">
              Subgenre
            </label>
            <select
              value={subgenreFilter}
              onChange={(e) => {
                setSubgenreFilter(e.target.value);
                setPage(0);
              }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200"
            >
              <option value="">All subgenres</option>
              {(genreFilter ? filteredSubgenres : subgenres).map((sg) => (
                <option key={sg.subgenre} value={sg.subgenre}>
                  {sg.subgenre} ({sg.count})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-zinc-500 block mb-1">Year</label>
            <div className="flex gap-1 items-center">
              <input
                type="number"
                value={yearMin}
                onChange={(e) => {
                  setYearMin(e.target.value);
                  setPage(0);
                }}
                placeholder="from"
                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-2 text-sm text-zinc-200 placeholder:text-zinc-600"
              />
              <span className="text-zinc-600 text-xs">–</span>
              <input
                type="number"
                value={yearMax}
                onChange={(e) => {
                  setYearMax(e.target.value);
                  setPage(0);
                }}
                placeholder="to"
                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-2 text-sm text-zinc-200 placeholder:text-zinc-600"
              />
            </div>
          </div>
        </div>
        {(search || genreFilter || subgenreFilter || yearMin || yearMax) && (
          <button
            onClick={resetFilters}
            className="mt-3 text-xs text-zinc-500 hover:text-zinc-300 transition"
          >
            Clear all filters
          </button>
        )}
      </div>

      {/* Results table */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-zinc-500 border-b border-zinc-800">
                <th className="px-4 py-2 text-left">Artist</th>
                <th className="px-4 py-2 text-left">Title</th>
                <th className="px-4 py-2 text-left">Genre</th>
                <th className="px-4 py-2 text-left">Subgenre</th>
                <th className="px-4 py-2 text-right">Year</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((t, i) => {
                const color = familyColor(t.subgenre, t.genre);
                return (
                  <tr
                    key={`${t.spotifyId}-${i}`}
                    className="border-b border-zinc-800/50 hover:bg-zinc-800/30"
                  >
                    <td className="px-4 py-2 text-zinc-200">{t.artist}</td>
                    <td className="px-4 py-2 text-zinc-300">{t.title}</td>
                    <td className="px-4 py-2">
                      {t.genre && (
                        <span
                          className={`text-[10px] px-1.5 py-0.5 rounded border ${genreBadgeClass(
                            t.genre
                          )}`}
                        >
                          {t.genre}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2">
                      {t.subgenre && (
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded"
                          style={{
                            backgroundColor: color.hex + "20",
                            color: color.hex,
                          }}
                        >
                          {t.subgenre}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right text-zinc-500 tabular-nums text-xs">
                      {t.releaseYear || ""}
                    </td>
                  </tr>
                );
              })}
              {pageItems.length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-8 text-center text-zinc-500"
                  >
                    No tracks match your filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <button
            onClick={() => setPage(Math.max(0, page - 1))}
            disabled={page === 0}
            className="text-sm text-zinc-400 hover:text-white disabled:text-zinc-700 transition px-3 py-1.5 rounded bg-zinc-900 border border-zinc-800 disabled:border-zinc-900"
          >
            ← Prev
          </button>
          <span className="text-xs text-zinc-500">
            Page {page + 1} of {totalPages}
          </span>
          <button
            onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
            disabled={page >= totalPages - 1}
            className="text-sm text-zinc-400 hover:text-white disabled:text-zinc-700 transition px-3 py-1.5 rounded bg-zinc-900 border border-zinc-800 disabled:border-zinc-900"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}
