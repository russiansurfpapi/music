/**
 * Data layer — reads from exported JSON (data/library.json) instead of SQLite.
 * No database dependency at runtime. Re-export by running: node scripts/export-data.mjs
 */

import { readFileSync } from "fs";
import { join } from "path";

export interface LibraryTrack {
  spotifyId: string;
  artist: string;
  title: string;
  album: string | null;
  releaseYear: number | null;
  genre: string | null;
  subgenre: string | null;
  rhythm: string | null;
  texture: string | null;
  productionDna: string | null;
}

interface LibraryData {
  stats: { djCount: number; setCount: number; trackCount: number; classCount: number };
  genres: { genre: string; count: number }[];
  subgenreList: { subgenre: string; count: number }[];
  djs: { slug: string; name: string; isFavorite: boolean; sets: number; totalResolved: number }[];
  djGenres: Record<string, { genre: string; count: number }[]>;
  djSubgenres: Record<string, { subgenre: string; count: number }[]>;
  djArchetypeTracks: Record<string, { subgenre: string | null; genre: string | null; year: number | null }[]>;
  sets: {
    setId: string; djSlug: string; djName: string; title: string | null;
    setDate: string | null; trackCount: number; resolvedCount: number; youtubeUrl: string | null;
  }[];
  setTracks: Record<string, {
    position: number; rawArtist: string; rawTitle: string; spotifyId: string | null;
    genre: string | null; subgenre: string | null; rhythm: string | null;
    texture: string | null; productionDna: string | null; year: number | null;
  }[]>;
  tracks: LibraryTrack[];
}

let _data: LibraryData | null = null;

function getData(): LibraryData {
  if (!_data) {
    const filePath = join(process.cwd(), "data", "library.json");
    _data = JSON.parse(readFileSync(filePath, "utf-8"));
  }
  return _data!;
}

// ---- Dashboard ----

export function getStats() {
  return getData().stats;
}

export function getGenreDistribution() {
  return getData().genres;
}

export function getTopDjs(limit = 10) {
  return getData().djs.slice(0, limit);
}

export function getRecentSets(limit = 15) {
  return getData().sets.slice(0, limit);
}

// ---- DJs ----

export function getAllDjs() {
  return getData().djs;
}

export function getDjGenres() {
  return getData().djGenres;
}

// ---- DJ Detail ----

export function getDj(slug: string) {
  return getData().djs.find(d => d.slug === slug) || null;
}

export function getDjSets(slug: string) {
  return getData().sets.filter(s => s.djSlug === slug);
}

export function getDjGenreBreakdown(slug: string) {
  return getData().djGenres[slug] || [];
}

export function getDjSubgenreBreakdown(slug: string) {
  return (getData().djSubgenres[slug] || []).slice(0, 15);
}

export function getDjArchetypeTracks(slug: string) {
  return getData().djArchetypeTracks[slug] || [];
}

export function getSetTracksForShape(setId: string) {
  return (getData().setTracks[setId] || []).map(t => ({
    subgenre: t.subgenre,
    genre: t.genre,
  }));
}

// ---- Set Detail ----

export function getSetMeta(setId: string) {
  return getData().sets.find(s => s.setId === setId) || null;
}

export function getSetTracks(setId: string) {
  return getData().setTracks[setId] || [];
}

export function getSetSpotifyIds(setId: string): string[] {
  return (getData().setTracks[setId] || [])
    .map(t => t.spotifyId)
    .filter((id): id is string => !!id);
}

// ---- Library (all classified tracks) ----

export function getAllTracks(): LibraryTrack[] {
  return getData().tracks;
}

export function getSubgenreList() {
  return getData().subgenreList;
}
