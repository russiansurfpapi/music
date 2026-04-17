"""Backfill dj_sets + dj_set_tracks from sets/*.html and spotify_cache.json.

Zero API calls. Idempotent — safe to re-run.

Usage:
    python3 dj_ingest.py                   # ingest all 124 HTML files
    python3 dj_ingest.py --only-favorites  # ingest only the curated 8 DJs
    python3 dj_ingest.py --report          # print summary, no changes
"""

import argparse
import os
import re
import sys
from typing import List, Optional, Tuple

from db import connect
from tracklist_scraper import (
    _cache_key,
    _load_spotify_cache,
    clean_track_name,
    extract_tracks,
)

SETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sets")

# Curated favorite DJs (slug → display name)
FAVORITE_DJS = {
    "shadow-child": "Shadow Child",
    "dj-koze": "DJ Koze",
    "kink": "KiNK",
    "sasha": "Sasha",
    "photek": "Photek",
    "mr-sosa": "Mr. Sosa",
    "olivier-verhaeghe": "Olivier Verhaeghe",
    "ricoshei": "Ricoshei",
    "peggy-gou": "Peggy Gou",
    "mochakk": "Mochakk",
}

# Filename pattern: <stuff>-YYYY-MM-DD[_html].html
DATE_PATTERN = re.compile(r"^(.+?)-(\d{4}-\d{2}-\d{2})(?:_html)?\.html$")


def parse_filename(filename: str, known_slugs: List[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (dj_slug, set_date, title) from a sets/ filename.

    DJ slug = longest known-DJ slug that is a hyphen-bounded prefix of the filename.
    Falls back to the first hyphen-token if no known DJ matches.
    """
    m = DATE_PATTERN.match(filename)
    if not m:
        # Some files have no date in the name
        stem = filename.replace(".html", "")
        date = None
    else:
        stem, date = m.group(1), m.group(2)

    # Match against known slugs (longest first, must be followed by `-` or end)
    dj_slug = None
    for slug in sorted(known_slugs, key=len, reverse=True):
        if stem == slug or stem.startswith(slug + "-"):
            dj_slug = slug
            break

    if dj_slug is None:
        # Fall back to the first 1-2 tokens as the DJ slug
        parts = stem.split("-")
        # Heuristic: 'dj-X', 'mr-X' use 2 tokens; otherwise use 1
        if parts[0] in ("dj", "mr", "mrs", "sir", "lord"):
            dj_slug = "-".join(parts[:2]) if len(parts) >= 2 else parts[0]
        else:
            dj_slug = parts[0]

    # Title is whatever is left after the DJ slug, hyphens to spaces
    rest = stem[len(dj_slug):].lstrip("-")
    title = rest.replace("-", " ").strip() if rest else None

    return dj_slug, date, title


def slug_to_name(slug: str) -> str:
    """Title-case a slug into a display name. 'dj-koze' → 'DJ Koze'."""
    if slug in FAVORITE_DJS:
        return FAVORITE_DJS[slug]
    parts = slug.split("-")
    return " ".join(p.upper() if p in ("dj", "mc", "id") else p.title() for p in parts)


def upsert_dj(conn, slug: str, name: str, is_favorite: bool) -> None:
    conn.execute("""
        INSERT INTO djs (slug, name, is_favorite)
        VALUES (?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            name = excluded.name,
            is_favorite = MAX(djs.is_favorite, excluded.is_favorite)
    """, (slug, name, 1 if is_favorite else 0))


def extract_set_tracks(filepath: str, cache: dict) -> Tuple[List[Tuple[int, str, str, Optional[str]]], int]:
    """Read HTML, extract tracks, resolve via cache. Returns (rows, resolved_count).
    Each row = (position, raw_artist, raw_title, spotify_id_or_None)."""
    filename = os.path.basename(filepath)
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()
    tracks = extract_tracks(html, source=filename)
    rows = []
    resolved = 0
    for position, (artist, title, _src) in enumerate(tracks, start=1):
        # Cache keys in spotify_cache.json use the ORIGINAL title (not clean_track_name),
        # because find_spotify_track() builds the key from the raw title.
        key = _cache_key(artist, title)
        spotify_id = cache.get(key)
        if spotify_id is None:
            # Fallback: try cleaned title (older cache entries may have used it)
            cleaned = clean_track_name(artist, title)
            if cleaned != title:
                spotify_id = cache.get(_cache_key(artist, cleaned))
        if spotify_id:
            resolved += 1
        rows.append((position, artist, title, spotify_id))
    return rows, resolved


def write_set_tracks(conn, set_id: str, rows: List[Tuple[int, str, str, Optional[str]]]) -> None:
    """Replace dj_set_tracks for this set."""
    conn.execute("DELETE FROM dj_set_tracks WHERE set_id = ?", (set_id,))
    for position, artist, title, spotify_id in rows:
        conn.execute("""
            INSERT INTO dj_set_tracks (set_id, position, raw_artist, raw_title, spotify_id)
            VALUES (?, ?, ?, ?, ?)
        """, (set_id, position, artist, title, spotify_id))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only-favorites", action="store_true",
                        help="ingest only the 8 curated favorite DJs")
    parser.add_argument("--report", action="store_true",
                        help="don't ingest, just print existing state")
    args = parser.parse_args()

    conn = connect()
    try:
        if args.report:
            _print_report(conn)
            return

        cache = _load_spotify_cache()
        print(f"Loaded {len(cache)} entries from spotify_cache.json")

        # Seed favorite DJs first
        for slug, name in FAVORITE_DJS.items():
            upsert_dj(conn, slug, name, is_favorite=True)
        conn.commit()

        files = sorted(os.listdir(SETS_DIR))
        files = [f for f in files if f.endswith(".html")]
        print(f"Found {len(files)} HTML files in sets/")

        ingested = 0
        skipped = 0
        for filename in files:
            filepath = os.path.join(SETS_DIR, filename)
            dj_slug, set_date, title = parse_filename(filename, list(FAVORITE_DJS.keys()))

            if args.only_favorites and dj_slug not in FAVORITE_DJS:
                skipped += 1
                continue

            # Register the DJ if new
            if dj_slug not in FAVORITE_DJS:
                upsert_dj(conn, dj_slug, slug_to_name(dj_slug), is_favorite=False)

            set_id = filename.replace(".html", "")
            rows, resolved = extract_set_tracks(filepath, cache)
            track_count = len(rows)

            # Insert/update parent dj_sets row FIRST so FK is satisfied.
            conn.execute("""
                INSERT INTO dj_sets
                    (set_id, dj_slug, title, set_date, source_file, track_count, resolved_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(set_id) DO UPDATE SET
                    dj_slug = excluded.dj_slug,
                    title = excluded.title,
                    set_date = excluded.set_date,
                    track_count = excluded.track_count,
                    resolved_count = excluded.resolved_count,
                    ingested_at = CURRENT_TIMESTAMP
            """, (set_id, dj_slug, title, set_date,
                  os.path.relpath(filepath, os.path.dirname(SETS_DIR)),
                  track_count, resolved))
            write_set_tracks(conn, set_id, rows)
            conn.commit()
            ingested += 1
            if track_count > 0:
                print(f"  [{dj_slug}] {set_id[:60]}: {resolved}/{track_count} resolved")

        print(f"\nIngested {ingested} sets, skipped {skipped}")
        _print_report(conn)
    finally:
        conn.close()


def _print_report(conn):
    print("\n=== Ingestion summary ===")
    rows = conn.execute("""
        SELECT d.slug, d.name, d.is_favorite,
               COUNT(DISTINCT s.set_id) AS sets,
               COALESCE(SUM(s.track_count), 0) AS tracks,
               COALESCE(SUM(s.resolved_count), 0) AS resolved
        FROM djs d
        LEFT JOIN dj_sets s ON s.dj_slug = d.slug
        GROUP BY d.slug
        ORDER BY d.is_favorite DESC, tracks DESC
    """).fetchall()
    print(f"\n{'DJ':<25} {'Fav':<4} {'Sets':>5} {'Tracks':>7} {'Resolved':>9} {'Hit %':>6}")
    print("-" * 65)
    for r in rows:
        if r["sets"] == 0:
            continue
        hit = (100.0 * r["resolved"] / r["tracks"]) if r["tracks"] else 0
        fav = "★" if r["is_favorite"] else ""
        print(f"{r['name'][:24]:<25} {fav:<4} {r['sets']:>5} {r['tracks']:>7} {r['resolved']:>9} {hit:>5.1f}%")
    totals = conn.execute("""
        SELECT COUNT(*) AS sets, SUM(track_count) AS tracks, SUM(resolved_count) AS resolved
        FROM dj_sets
    """).fetchone()
    if totals["sets"]:
        hit = 100.0 * totals["resolved"] / totals["tracks"] if totals["tracks"] else 0
        print("-" * 65)
        print(f"{'TOTAL':<25} {'':<4} {totals['sets']:>5} {totals['tracks']:>7} {totals['resolved']:>9} {hit:>5.1f}%")


if __name__ == "__main__":
    main()
