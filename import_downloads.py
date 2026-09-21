"""Import manually-saved 1001TL HTMLs from ~/Downloads → full classification pipeline.

Usage:
    python3 import_downloads.py                  # scan Downloads, move, ingest, classify
    python3 import_downloads.py --dry-run        # show what would move
    python3 import_downloads.py --src ~/Desktop  # alternative source directory

Pipeline (Spotify-free — works during 429 bans):
  1. Verify HTML is a real tracklist (has .trackValue elements, not CAPTCHA/homepage shell)
  2. Rename to sets/<slug>-<venue>-<date>_html.html convention
  3. ingest_sets.py  — parse HTML → dj_sets + dj_set_tracks rows
  4. resolve_dj_tracks.py cache — soft-link via the library.db ID cache (no API)
  5. Backfill tracks rows with Last.fm-direct lookup for dj_set_tracks without a tracks match
  6. lastfm_tags.py — enrich pending tracks (Last.fm, separate from Spotify rate limit)
  7. classify.py — rebuild 6-layer classifications
  8. dj_archetype.py --all → DJ_ARCHETYPES.md
"""

import argparse
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup

HERE = Path(__file__).parent
DEFAULT_SRC = Path.home() / "Downloads"
SETS_DIR = HERE / "sets"


def is_real_tracklist(html_path: Path) -> tuple[bool, int]:
    """Returns (is_real, track_count)."""
    try:
        with open(html_path, encoding="utf-8", errors="ignore") as f:
            html = f.read()
        soup = BeautifulSoup(html, "html.parser")
        tv = len(soup.select(".trackValue"))
        if tv == 0:
            return False, 0
        title = soup.select_one("title")
        title_txt = title.text if title else ""
        # Generic homepage redirect: title matches the 1001TL landing page
        if "1001Tracklists ⋅ The World's Leading" in title_txt and tv == 0:
            return False, 0
        return True, tv
    except Exception as e:
        print(f"  ERR reading {html_path.name}: {e}")
        return False, 0


def normalize_filename(original: str) -> str:
    """
    'Mochakk @ Circoloco, DC10 Ibiza, Spain 2025-08-11.html'
        -> 'mochakk-circoloco-dc10-ibiza-spain-2025-08-11_html.html'

    Rules:
      - Split on ' @ ' — left side is DJ(s), right side is venue+date
      - Lowercase, strip punctuation, collapse whitespace to single dash
      - Collapse multiple dashes
      - Append _html.html so it matches the ingest convention
    """
    stem = original.rsplit(".html", 1)[0]
    # Replace ' @ ' separator with dash
    s = stem.replace(" @ ", " - ")
    # Strip accents, lowercase, replace punctuation
    s = s.lower()
    # Common substitutions
    s = s.replace("&", "and").replace("'", "").replace('"', "")
    # Anything non-alphanumeric becomes dash
    s = re.sub(r"[^a-z0-9]+", "-", s)
    # Collapse consecutive dashes
    s = re.sub(r"-+", "-", s).strip("-")
    # Cap length (ingest_sets truncates at 80 anyway for slug parsing)
    if len(s) > 120:
        s = s[:120]
    return f"{s}_html.html"


def run(cmd: list[str]) -> int:
    print(f"\n▶ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(HERE))


def backfill_lastfm_bypass() -> int:
    """For each dj_set_tracks row with spotify_id (cache-resolved) but no matching tracks
    row, create a minimal tracks row with cleaned artist+title + lastfm_status='pending'.
    This lets lastfm_tags.py enrich them without needing Spotify metadata."""
    conn = sqlite3.connect(HERE / "library.db")
    conn.row_factory = sqlite3.Row

    def clean_artist(raw: str) -> str:
        if not raw:
            return ""
        s = re.split(r"/index\.html", raw)[0]
        s = re.sub(r"<[^>]+>", "", s)
        # Split on featuring markers, keep primary artist
        s = re.split(
            r"(?i)\s*(?:ft\.|feat\.|featuring|&|vs\.?|\+|\bx\b|\bw/)\s*",
            s, maxsplit=1,
        )[0]
        s = re.sub(r"(?<=[a-z])ft\.", "", s)
        return re.sub(r"[\s,./]+$", "", s.strip())

    # Case A: has spotify_id, but missing from tracks table → insert minimal row
    a_rows = conn.execute("""
        SELECT DISTINCT t.spotify_id, t.raw_artist, t.raw_title
        FROM dj_set_tracks t
        LEFT JOIN tracks tr ON tr.spotify_id = t.spotify_id
        WHERE t.spotify_id IS NOT NULL AND tr.spotify_id IS NULL
    """).fetchall()

    inserted = 0
    for r in a_rows:
        artist = clean_artist(r["raw_artist"])
        title = (r["raw_title"] or "").strip()
        if not artist or not title or len(artist) < 2:
            continue
        conn.execute(
            "INSERT INTO tracks (spotify_id, artist, title, lastfm_status) "
            "VALUES (?, ?, ?, 'pending') ON CONFLICT(spotify_id) DO NOTHING",
            (r["spotify_id"], artist, title),
        )
        inserted += 1

    # Case B: no spotify_id at all — generate synthetic lfm:<hash> ID so they can
    # still be classified. When Spotify is available again, resolve --search can
    # upgrade these with real IDs.
    b_rows = conn.execute("""
        SELECT rowid, raw_artist, raw_title FROM dj_set_tracks
        WHERE spotify_id IS NULL
          AND length(raw_artist) > 1 AND length(raw_title) > 1
          AND LOWER(raw_artist) NOT IN ('id', 'i.d.', 'unknown', '?', 'spotify_uri')
    """).fetchall()

    synth = 0
    for r in b_rows:
        artist = clean_artist(r["raw_artist"])
        title = (r["raw_title"] or "").strip()
        if not artist or len(artist) < 2:
            continue
        key = f"{artist.lower()}||{title.lower()}"
        sid = "lfm:" + hashlib.md5(key.encode()).hexdigest()[:16]
        # Check if a track row with this synthetic id already exists
        exists = conn.execute(
            "SELECT 1 FROM tracks WHERE spotify_id=?", (sid,)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO tracks (spotify_id, artist, title, lastfm_status) "
                "VALUES (?, ?, ?, 'pending')",
                (sid, artist, title),
            )
        conn.execute(
            "UPDATE dj_set_tracks SET spotify_id=? WHERE rowid=?",
            (sid, r["rowid"]),
        )
        synth += 1

    conn.commit()
    conn.close()
    print(f"  Backfilled {inserted} from cache-resolved, {synth} via synthetic lfm: IDs")
    return inserted + synth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="Source dir (default: ~/Downloads)")
    ap.add_argument("--dry-run", action="store_true", help="Show moves without executing")
    ap.add_argument("--skip-pipeline", action="store_true", help="Just move+rename, don't run ingest/classify")
    args = ap.parse_args()

    src = Path(os.path.expanduser(args.src))
    if not src.is_dir():
        print(f"Source directory doesn't exist: {src}")
        sys.exit(1)

    SETS_DIR.mkdir(exist_ok=True)

    htmls = sorted(src.glob("*.html"))
    print(f"Scanning {src} — found {len(htmls)} .html files")

    # Load existing (dj-first-token, date) pairs from DB to catch dupes even when
    # filenames differ (manual-rename variations).
    conn = sqlite3.connect(HERE / "library.db")
    existing = {
        (r[0].split("-", 1)[0], r[1])
        for r in conn.execute("SELECT dj_slug, set_date FROM dj_sets").fetchall()
        if r[1]
    }
    conn.close()

    moved = 0
    skipped_shell = 0
    skipped_exists = 0
    skipped_dupe = 0
    for h in htmls:
        ok, tv = is_real_tracklist(h)
        if not ok:
            print(f"  SKIP (shell/empty, {tv} tracks): {h.name}")
            skipped_shell += 1
            continue
        target_name = normalize_filename(h.name)
        target = SETS_DIR / target_name
        if target.exists():
            print(f"  SKIP (already in sets/): {h.name}")
            skipped_exists += 1
            continue
        # DB-aware dedup: extract (dj first token, date) from target filename
        stem = target_name.replace("_html.html", "")
        date_m = re.search(r"(\d{4}-\d{2}-\d{2})", stem)
        dj_first = stem.split("-", 1)[0]
        if date_m and (dj_first, date_m.group(1)) in existing:
            print(f"  SKIP (dupe in DB: {dj_first} / {date_m.group(1)}): {h.name}")
            skipped_dupe += 1
            continue
        action = "WOULD MOVE" if args.dry_run else "MOVE"
        print(f"  {action} ({tv} tracks): {h.name} → {target_name}")
        if not args.dry_run:
            shutil.move(str(h), str(target))
        moved += 1

    print(f"\nSummary: {moved} moved, {skipped_shell} shells skipped, "
          f"{skipped_exists} already in sets/, {skipped_dupe} already in DB")

    if args.dry_run or args.skip_pipeline or moved == 0:
        return

    # Run the full pipeline
    print("\n=== Running pipeline ===")
    if run(["python3", "ingest_sets.py"]) != 0:
        print("ingest_sets.py failed"); return
    if run(["python3", "resolve_dj_tracks.py", "cache"]) != 0:
        print("resolve_dj_tracks.py cache failed"); return
    print("\n▶ Last.fm bypass backfill")
    backfill_lastfm_bypass()
    if run(["python3", "lastfm_tags.py"]) != 0:
        print("lastfm_tags.py failed"); return
    if run(["python3", "classify.py"]) != 0:
        print("classify.py failed"); return
    if run(["python3", "dj_archetype.py", "--all", "--min-sets", "1",
            "--md", "DJ_ARCHETYPES.md"]) != 0:
        print("dj_archetype.py failed"); return

    print("\n✓ Done. DJ_ARCHETYPES.md updated.")


if __name__ == "__main__":
    main()
