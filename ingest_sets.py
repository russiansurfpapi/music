"""Ingest 1001TL HTML files in sets/ into the djs / dj_sets / dj_set_tracks tables.

Strategy:
  1. Parse filename → (set_id, dj_slug guess, title, set_date).
  2. Reuse tracklist_scraper.extract_tracks() to pull (artist, title) tuples from HTML.
  3. Soft-link each track to the existing tracks table by fuzzy artist+title match.
  4. Idempotent: skip sets already ingested unless --force.

Usage:
    python3 ingest_sets.py
    python3 ingest_sets.py --force
"""

import argparse
import glob
import os
import re

from bs4 import BeautifulSoup  # noqa — used by tracklist_scraper

from db import connect
from tracklist_scraper import extract_tracks

SETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sets")
DATE_RE = re.compile(r"-(\d{4}-\d{2}-\d{2})")

# Common multi-word DJ names — extend as needed.
KNOWN_DJ_PREFIXES = [
    "above-and-beyond", "shadow-child", "dj-koze", "dj-preach", "dj-shadow",
    "annie-mac", "amelie-lens", "anna-lunoe", "amon-tobin", "porter-robinson",
    "carl-cox", "richie-hawtin", "nina-kraviz", "peggy-gou", "the-blessed-madonna",
    "honey-dijon", "jamie-xx", "four-tet", "floating-points", "ben-ufo",
    "joy-orbison", "objekt", "helena-hauff", "moodymann", "theo-parrish",
    "larry-heard", "frankie-knuckles", "ricardo-villalobos", "daphni",
    "octo-octa", "shanti-celeste", "call-super", "dj-stingray", "palms-trax",
]


def parse_filename(fname: str):
    """Best-effort: filename → (set_id, dj_slug, title, date)."""
    stem = fname.replace("_html.html", "").replace(".html", "")
    m = DATE_RE.search(stem)
    if m:
        date = m.group(1)
        prefix = stem[:m.start()]
    else:
        date, prefix = None, stem

    # Try known multi-word DJ prefixes first.
    dj_slug = None
    for kp in KNOWN_DJ_PREFIXES:
        if prefix.startswith(kp + "-") or prefix == kp:
            dj_slug = kp
            rest = prefix[len(kp):].lstrip("-")
            return stem, dj_slug, rest.replace("-", " ").title(), date

    # Fallback: take first 2 tokens as DJ name.
    tokens = prefix.split("-")
    if len(tokens) >= 3:
        dj_slug = "-".join(tokens[:2])
        title = "-".join(tokens[2:]).replace("-", " ").title()
    else:
        dj_slug = prefix
        title = ""
    return stem, dj_slug, title, date


_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, single-space."""
    s = (s or "").lower()
    return " ".join(_NORM_RE.sub(" ", s).split())


def _tokens(s: str) -> set:
    return set(_norm(s).split()) - {"the", "a", "an", "and", "of", "in", "to", "ft", "feat"}


def lookup_spotify_id(conn, artist: str, title: str):
    """Strict match against tracks table. Returns spotify_id or None.

    Strategy: candidate must be in classifications (not just tracks), and after
    normalization, ALL non-stop-word title tokens must appear in candidate title,
    AND the first artist token must appear in candidate artist string. Prevents
    the LIKE-percent over-match we hit before.
    """
    if not artist or not title:
        return None
    first_artist = artist.split(",")[0].split("&")[0].split(" feat")[0].strip()
    if first_artist.lower() in ("id", "?", "unknown", "spotify_uri", ""):
        return None

    a_tokens = _tokens(first_artist)
    # Strip parens/remix suffix from title for tokens.
    base_title = re.split(r"[(\[]", title)[0]
    t_tokens = _tokens(base_title)
    if not a_tokens or not t_tokens or len(base_title.strip()) < 3:
        return None

    # Get a small candidate set: any classified track containing the first
    # artist token AND first title token. Then verify token overlap in Python.
    a_seed = next(iter(sorted(a_tokens, key=len, reverse=True)))
    t_seed = next(iter(sorted(t_tokens, key=len, reverse=True)))
    if len(a_seed) < 3 or len(t_seed) < 3:
        return None

    rows = conn.execute(
        "SELECT t.spotify_id, t.artist, t.title FROM tracks t "
        "JOIN classifications c ON c.spotify_id = t.spotify_id "
        "WHERE lower(t.artist) LIKE ? AND lower(t.title) LIKE ? "
        "LIMIT 30",
        (f"%{a_seed}%", f"%{t_seed}%"),
    ).fetchall()
    for r in rows:
        cand_a = _tokens(r["artist"])
        cand_t = _tokens(r["title"])
        # Title: all search tokens must appear in candidate (subset).
        if not t_tokens.issubset(cand_t):
            continue
        # Artist: at least one seed token overlap (handles "feat" mismatches).
        if a_tokens.isdisjoint(cand_a):
            continue
        return r["spotify_id"]
    return None


def upsert_dj(conn, slug: str) -> None:
    name = slug.replace("-", " ").title()
    conn.execute(
        "INSERT OR IGNORE INTO djs (slug, name) VALUES (?, ?)",
        (slug, name),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="Re-ingest sets that already exist")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(SETS_DIR, "*.html")))
    print(f"Found {len(files)} HTML files in {SETS_DIR}")

    with connect() as conn:
        ingested = 0
        skipped = 0
        empty = 0
        for path in files:
            fname = os.path.basename(path)
            set_id, dj_slug, title, date = parse_filename(fname)

            existing = conn.execute(
                "SELECT 1 FROM dj_sets WHERE set_id=?", (set_id,)
            ).fetchone()
            if existing and not args.force:
                skipped += 1
                continue

            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                html = f.read()

            tracks = extract_tracks(html, source=fname)
            if not tracks:
                empty += 1
                continue

            upsert_dj(conn, dj_slug)
            resolved = 0

            if existing:
                conn.execute("DELETE FROM dj_set_tracks WHERE set_id=?", (set_id,))
                conn.execute("DELETE FROM dj_sets WHERE set_id=?", (set_id,))

            conn.execute(
                "INSERT INTO dj_sets (set_id, dj_slug, title, set_date, source_file, track_count, resolved_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (set_id, dj_slug, title, date, f"sets/{fname}", len(tracks), 0),
            )
            for pos, item in enumerate(tracks, 1):
                if isinstance(item, tuple):
                    artist, ttl = item[0], item[1]
                elif isinstance(item, dict):
                    artist = item.get("artist", "")
                    ttl = item.get("title", "")
                else:
                    artist, ttl = "", ""
                sid = lookup_spotify_id(conn, artist, ttl)
                if sid:
                    resolved += 1
                conn.execute(
                    "INSERT INTO dj_set_tracks (set_id, position, raw_artist, raw_title, spotify_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (set_id, pos, artist, ttl, sid),
                )
            conn.execute(
                "UPDATE dj_sets SET resolved_count=? WHERE set_id=?",
                (resolved, set_id),
            )
            conn.commit()
            ingested += 1
            print(f"  {dj_slug:25s} {set_id[:60]:60s} {len(tracks)}t / {resolved}r")

        print(f"\nIngested: {ingested}, skipped: {skipped}, empty: {empty}")


if __name__ == "__main__":
    main()
