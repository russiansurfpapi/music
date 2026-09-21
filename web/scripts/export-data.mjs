/**
 * Export library.db data to JSON for Vercel deployment.
 * Run: node scripts/export-data.mjs
 *
 * Reads ../library.db and writes data/library.json with all the data
 * the web app needs. Pages read from this file instead of querying
 * SQLite at runtime — no database needed in production.
 */

import { createClient } from "@libsql/client";
import { writeFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const webRoot = join(__dirname, "..");

const db = createClient({ url: `file:${join(webRoot, "..", "library.db")}` });

async function q(sql) {
  const result = await db.execute(sql);
  return result.rows;
}

async function qp(sql, args) {
  const result = await db.execute({ sql, args });
  return result.rows;
}

async function main() {
  console.log("Exporting library.db → data/library.json ...");

  // Stats
  const [[djCount], [setCount], [trackCount], [classCount]] = await Promise.all([
    q("SELECT COUNT(*) as n FROM djs"),
    q("SELECT COUNT(*) as n FROM dj_sets"),
    q("SELECT COUNT(*) as n FROM tracks"),
    q("SELECT COUNT(*) as n FROM classifications"),
  ]);

  const stats = {
    djCount: Number(djCount.n),
    setCount: Number(setCount.n),
    trackCount: Number(trackCount.n),
    classCount: Number(classCount.n),
  };

  // Genre distribution
  const genres = (await q(
    `SELECT genre, COUNT(*) as n FROM classifications WHERE genre IS NOT NULL GROUP BY genre ORDER BY n DESC`
  )).map(r => ({ genre: String(r.genre), count: Number(r.n) }));

  // All DJs with set counts
  const djs = (await q(
    `SELECT d.slug, d.name, d.is_favorite,
            COUNT(s.set_id) as sets,
            COALESCE(SUM(s.resolved_count), 0) as total_resolved
     FROM djs d LEFT JOIN dj_sets s ON s.dj_slug = d.slug
     GROUP BY d.slug ORDER BY sets DESC, d.name`
  )).map(r => ({
    slug: String(r.slug),
    name: String(r.name),
    isFavorite: Boolean(r.is_favorite),
    sets: Number(r.sets),
    totalResolved: Number(r.total_resolved),
  }));

  // Top genres per DJ
  const djGenreRows = await q(
    `SELECT s.dj_slug, c.genre, COUNT(*) as n
     FROM dj_set_tracks t
     JOIN dj_sets s ON t.set_id = s.set_id
     JOIN classifications c ON c.spotify_id = t.spotify_id
     WHERE c.genre IS NOT NULL
     GROUP BY s.dj_slug, c.genre ORDER BY s.dj_slug, n DESC`
  );
  const djGenres = {};
  for (const r of djGenreRows) {
    const slug = String(r.dj_slug);
    if (!djGenres[slug]) djGenres[slug] = [];
    djGenres[slug].push({ genre: String(r.genre), count: Number(r.n) });
  }

  // All sets
  const sets = (await q(
    `SELECT s.set_id, s.dj_slug, s.title, s.set_date, s.track_count,
            s.resolved_count, s.youtube_url, d.name as dj_name
     FROM dj_sets s JOIN djs d ON d.slug = s.dj_slug
     ORDER BY s.set_date DESC NULLS LAST`
  )).map(r => ({
    setId: String(r.set_id),
    djSlug: String(r.dj_slug),
    djName: String(r.dj_name),
    title: r.title ? String(r.title) : null,
    setDate: r.set_date ? String(r.set_date) : null,
    trackCount: Number(r.track_count),
    resolvedCount: Number(r.resolved_count),
    youtubeUrl: r.youtube_url ? String(r.youtube_url) : null,
  }));

  // All set tracks with classifications (the big table)
  const setTrackRows = await q(
    `SELECT t.set_id, t.position, t.raw_artist, t.raw_title, t.spotify_id,
            t.timestamp_sec, c.genre, c.subgenre, c.rhythm, c.texture,
            c.production_dna, tr.release_year
     FROM dj_set_tracks t
     LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
     LEFT JOIN tracks tr ON tr.spotify_id = t.spotify_id
     ORDER BY t.set_id, t.position`
  );

  // Group tracks by set_id
  const setTracks = {};
  for (const r of setTrackRows) {
    const sid = String(r.set_id);
    if (!setTracks[sid]) setTracks[sid] = [];
    setTracks[sid].push({
      position: Number(r.position),
      rawArtist: String(r.raw_artist),
      rawTitle: String(r.raw_title),
      spotifyId: r.spotify_id ? String(r.spotify_id) : null,
      genre: r.genre ? String(r.genre) : null,
      subgenre: r.subgenre ? String(r.subgenre) : null,
      rhythm: r.rhythm ? String(r.rhythm) : null,
      texture: r.texture ? String(r.texture) : null,
      productionDna: r.production_dna ? String(r.production_dna) : null,
      year: r.release_year ? Number(r.release_year) : null,
    });
  }

  // DJ subgenre breakdowns
  const djSubgenreRows = await q(
    `SELECT s.dj_slug, c.subgenre, COUNT(*) as n
     FROM dj_set_tracks t
     JOIN dj_sets s ON t.set_id = s.set_id
     JOIN classifications c ON c.spotify_id = t.spotify_id
     WHERE s.dj_slug IS NOT NULL AND c.subgenre IS NOT NULL
     GROUP BY s.dj_slug, c.subgenre ORDER BY s.dj_slug, n DESC`
  );
  const djSubgenres = {};
  for (const r of djSubgenreRows) {
    const slug = String(r.dj_slug);
    if (!djSubgenres[slug]) djSubgenres[slug] = [];
    djSubgenres[slug].push({ subgenre: String(r.subgenre), count: Number(r.n) });
  }

  // DJ-level classified tracks (for archetype computation)
  const djArchetypeRows = await q(
    `SELECT s.dj_slug, c.subgenre, c.genre, tr.release_year as year
     FROM dj_set_tracks t
     JOIN dj_sets s ON t.set_id = s.set_id
     JOIN classifications c ON c.spotify_id = t.spotify_id
     LEFT JOIN tracks tr ON tr.spotify_id = t.spotify_id
     WHERE c.genre IS NOT NULL`
  );
  const djArchetypeTracks = {};
  for (const r of djArchetypeRows) {
    const slug = String(r.dj_slug);
    if (!djArchetypeTracks[slug]) djArchetypeTracks[slug] = [];
    djArchetypeTracks[slug].push({
      subgenre: r.subgenre ? String(r.subgenre) : null,
      genre: r.genre ? String(r.genre) : null,
      year: r.year ? Number(r.year) : null,
    });
  }

  // All classified tracks (full library browser)
  const classifiedTrackRows = await q(
    `SELECT t.spotify_id, t.artist, t.title, t.album, t.release_year,
            c.genre, c.subgenre, c.rhythm, c.texture, c.production_dna
     FROM tracks t
     JOIN classifications c ON c.spotify_id = t.spotify_id
     WHERE c.genre IS NOT NULL OR c.subgenre IS NOT NULL
     ORDER BY t.artist, t.title`
  );
  const tracks = classifiedTrackRows.map(r => ({
    spotifyId: String(r.spotify_id),
    artist: String(r.artist),
    title: String(r.title),
    album: r.album ? String(r.album) : null,
    releaseYear: r.release_year ? Number(r.release_year) : null,
    genre: r.genre ? String(r.genre) : null,
    subgenre: r.subgenre ? String(r.subgenre) : null,
    rhythm: r.rhythm ? String(r.rhythm) : null,
    texture: r.texture ? String(r.texture) : null,
    productionDna: r.production_dna ? String(r.production_dna) : null,
  }));

  // Subgenre list with counts (for library filter dropdowns)
  const subgenreList = (await q(
    `SELECT subgenre, COUNT(*) as n FROM classifications
     WHERE subgenre IS NOT NULL GROUP BY subgenre ORDER BY n DESC`
  )).map(r => ({ subgenre: String(r.subgenre), count: Number(r.n) }));

  const library = {
    exportedAt: new Date().toISOString(),
    stats,
    genres,
    subgenreList,
    djs,
    djGenres,
    djSubgenres,
    djArchetypeTracks,
    sets,
    setTracks,
    tracks,
  };

  const outPath = join(webRoot, "data", "library.json");
  writeFileSync(outPath, JSON.stringify(library));

  const sizeMB = (JSON.stringify(library).length / 1024 / 1024).toFixed(1);
  console.log(`Done! ${outPath} (${sizeMB} MB)`);
  console.log(`  ${stats.djCount} DJs, ${stats.setCount} sets, ${stats.trackCount} tracks`);
  console.log(`  ${tracks.length} classified tracks for library browser`);
  console.log(`  ${subgenreList.length} distinct subgenres`);
  console.log(`  ${Object.keys(setTracks).length} sets with track data`);
}

main().catch(console.error);
