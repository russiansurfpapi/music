"""End-to-end pipeline: YouTube DJ sets → fingerprint → classify → Study playlists.

Takes one or more YouTube URLs, fingerprints each via Shazam, promotes
orphan tracks into the library, fetches Last.fm tags, classifies, and
optionally rebuilds Study playlists on Spotify.

Usage:
    python3 youtube_to_study.py URL1 URL2 ... --dj <slug>
    python3 youtube_to_study.py URL1 URL2 ... --auto-dj
    python3 youtube_to_study.py URL1 URL2 ... --auto-dj --push
    python3 youtube_to_study.py --resume   # skip fingerprinting, run tags+classify+push

Auto-DJ mode:
    Fetches video titles via yt-dlp, extracts the DJ name from common
    patterns ("DJ @ Venue", "DJ | Event", "DJ Live @ ..."), slugifies it,
    and groups URLs by DJ. Skips URLs already fingerprinted in the DB.
"""

import argparse
import re
import sqlite3
import subprocess
import sys
import time
from typing import Optional

HERE = __import__("pathlib").Path(__file__).parent
DB = HERE / "library.db"


def run_step(label, cmd, bail_on_error=True):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}\n")
    t0 = time.time()
    result = subprocess.run(cmd, cwd=str(HERE))
    elapsed = time.time() - t0
    if result.returncode != 0 and bail_on_error:
        print(f"\n✗ {label} failed (exit {result.returncode}) after {elapsed:.0f}s")
        sys.exit(result.returncode)
    print(f"\n✓ {label} done ({elapsed:.0f}s)")
    return result.returncode


def extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def get_existing_set_ids() -> "set[str]":
    try:
        con = sqlite3.connect(str(DB))
        rows = con.execute("SELECT set_id FROM dj_sets").fetchall()
        con.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def fetch_video_metadata(urls: "list[str]") -> "list[dict]":
    """Fetch title + duration for each URL via yt-dlp."""
    cmd = ["yt-dlp", "--print", "%(id)s\t%(title)s\t%(duration)s", "--no-download"] + urls
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(HERE))
    entries = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) == 3:
            entries.append({"id": parts[0], "title": parts[1], "duration": parts[2]})
    return entries


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def extract_dj_from_title(title: str) -> "tuple[str, str]":
    """Extract DJ name from video title. Returns (dj_slug, title_for_set)."""
    # Strip "Live" suffix from DJ name candidates
    def clean_name(name):
        return re.sub(r"\s+[Ll]ive$", "", name).strip()

    # Try structured separators (order matters: more specific first)
    separators = [" Live @ ", " live @ ", " @ ", " @"]
    for sep in separators:
        if sep in title:
            parts = title.split(sep, 1)
            candidate = clean_name(parts[0].strip())
            if len(candidate) > 40:
                continue
            if candidate.upper().startswith(("FULL SET", "LIVE SET", "DJ SET")):
                continue
            slug = slugify(candidate)
            if slug and len(slug) >= 2:
                return slug, title

    # "NAME — rest" or "NAME - rest" (but not "rest | NAME - sub")
    # Skip if left side is ALL-CAPS+punctuation (likely a show/label name)
    for sep in [" — ", " - "]:
        if sep in title and " | " not in title.split(sep, 1)[0]:
            candidate = clean_name(title.split(sep, 1)[0].strip())
            if len(candidate) > 40:
                continue
            if re.match(r"^[A-Z0-9._·\s]+$", candidate):
                continue
            slug = slugify(candidate)
            if slug and len(slug) >= 2:
                return slug, title

    # "NAME party heaters DJ set" / "NAME DJ Set" / "NAME Live Set"
    # (check BEFORE pipe-split so "RUZE party heaters DJ set | Mixmag" → ruze)
    # But only if no pipe — otherwise "Sunset House Mix | A-Trak" should use pipe logic
    if " | " not in title:
        m = re.match(r"^(.+?)\s*(?:party heaters|DJ [Ss]et|[Ll]ive [Ss]et|[Bb]oiler [Rr]oom|[Mm]ix)", title)
        if m:
            candidate = clean_name(m.group(1).strip(" -|—"))
            slug = slugify(candidate)
            if slug and len(slug) >= 2:
                return slug, title
    else:
        # Has pipes — try regex on first segment only
        first_seg = title.split(" | ", 1)[0]
        m = re.match(r"^(.+?)\s*(?:party heaters|DJ [Ss]et|[Ll]ive [Ss]et|[Bb]oiler [Rr]oom)", first_seg)
        if m:
            candidate = clean_name(m.group(1).strip(" -|—"))
            slug = slugify(candidate)
            if slug and len(slug) >= 2:
                return slug, title

    # "Label/Show | DJ NAME" pattern (e.g., "Sunset House Mix | A-Trak | ...")
    pipe_parts = [p.strip() for p in title.split(" | ")]
    if len(pipe_parts) >= 2:
        candidate = clean_name(pipe_parts[1])
        if 2 <= len(candidate) <= 30:
            slug = slugify(candidate)
            if slug:
                return slug, title

    # "SHOW - ARTIST (episode)" pattern (e.g., "DEEPND.FM - FERRA BLACK (012)")
    m = re.match(r"^[A-Z0-9._·]+(?:\s+[A-Z0-9._·]+)?\s*[-—]\s*(.+?)(?:\s*\(\d+\))?$", title)
    if m:
        candidate = m.group(1).strip()
        slug = slugify(candidate)
        if slug and len(slug) >= 2:
            return slug, title

    # Last resort
    return slugify(title[:30]), title


def dedupe_urls(urls: "list[str]") -> "list[str]":
    """Remove duplicate video IDs from URL list."""
    seen = set()
    deduped = []
    for url in urls:
        vid = extract_video_id(url)
        if vid and vid not in seen:
            seen.add(vid)
            deduped.append(url)
        elif not vid:
            deduped.append(url)
    return deduped


def main():
    ap = argparse.ArgumentParser(description="YouTube DJ sets → Study playlists pipeline")
    ap.add_argument("urls", nargs="*", help="YouTube URLs to fingerprint")
    ap.add_argument("--dj", help="DJ slug (required unless --auto-dj or --resume)")
    ap.add_argument("--auto-dj", action="store_true",
                    help="Auto-detect DJ slug from video title")
    ap.add_argument("--backend", choices=["shazam", "acrcloud", "merge"], default="shazam")
    ap.add_argument("--chunk", type=int, default=20)
    ap.add_argument("--step", type=int, default=45)
    ap.add_argument("--push", action="store_true", help="Push updated Study playlists to Spotify")
    ap.add_argument("--resume", action="store_true",
                    help="Skip fingerprinting; just run tags → classify → push")
    ap.add_argument("--skip-tags", action="store_true", help="Skip Last.fm tagging step")
    ap.add_argument("--skip-classify", action="store_true", help="Skip classification step")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be processed without running")
    args = ap.parse_args()

    if not args.resume and not args.urls:
        ap.error("provide YouTube URLs or use --resume")
    if not args.resume and not args.dj and not args.auto_dj:
        ap.error("--dj or --auto-dj is required when fingerprinting")

    # Step 1: Fingerprint
    if not args.resume:
        urls = dedupe_urls(args.urls)
        existing = get_existing_set_ids()

        # Filter out already-processed sets
        fresh_urls = []
        skipped = []
        for url in urls:
            vid = extract_video_id(url)
            if vid and vid in existing:
                skipped.append((vid, url))
            else:
                fresh_urls.append(url)

        if skipped:
            print(f"\n  Skipping {len(skipped)} already-fingerprinted set(s):")
            for vid, url in skipped:
                print(f"    • {vid}")

        if not fresh_urls:
            print("\n  All URLs already processed. Use --resume to re-run tags/classify.")
            if not args.skip_tags or not args.skip_classify:
                pass  # fall through to tags/classify
            else:
                return

        if args.auto_dj:
            print(f"\n  Fetching metadata for {len(fresh_urls)} video(s)...")
            meta = fetch_video_metadata(fresh_urls)
            jobs = []
            for entry in meta:
                vid_id = entry["id"]
                title = entry["title"]
                dj_slug, set_title = extract_dj_from_title(title)
                url = next((u for u in fresh_urls if vid_id in u), fresh_urls[0])
                jobs.append({"url": url, "dj": dj_slug, "title": set_title, "id": vid_id})

            # Print plan
            print(f"\n  Plan ({len(jobs)} set(s)):")
            for j in jobs:
                print(f"    • [{j['dj']}] {j['title']}")

            if args.dry_run:
                print("\n  --dry-run: stopping here.")
                return

            for i, job in enumerate(jobs, 1):
                run_step(
                    f"[{i}/{len(jobs)}] {job['dj']} — {job['title'][:50]}",
                    [sys.executable, "identify_youtube_set.py", job["url"],
                     "--dj", job["dj"],
                     "--title", job["title"],
                     "--backend", args.backend,
                     "--chunk", str(args.chunk),
                     "--step", str(args.step)],
                    bail_on_error=False,
                )
        else:
            if args.dry_run:
                print(f"\n  --dry-run: would process {len(fresh_urls)} URL(s) as --dj {args.dj}")
                return
            for i, url in enumerate(fresh_urls, 1):
                run_step(
                    f"[{i}/{len(fresh_urls)}] Fingerprinting: {url}",
                    [sys.executable, "identify_youtube_set.py", url,
                     "--dj", args.dj,
                     "--backend", args.backend,
                     "--chunk", str(args.chunk),
                     "--step", str(args.step)],
                )

    # Step 2a: Resolve Spotify IDs — cache first (free)
    run_step("Resolve: cache pass (no API calls)",
             [sys.executable, "resolve_dj_tracks.py", "cache"],
             bail_on_error=False)

    # Step 2b: Resolve via ISRC (near-100% hit rate, 1 API call each)
    run_step("Resolve: ISRC lookup on Spotify",
             [sys.executable, "resolve_dj_tracks.py", "isrc"],
             bail_on_error=False)

    # Step 2c: Resolve remaining via artist+title search
    run_step("Resolve: Spotify search fallback",
             [sys.executable, "resolve_dj_tracks.py", "search"],
             bail_on_error=False)

    # Step 3: Last.fm tags
    if not args.skip_tags:
        run_step("Last.fm: promote orphans + fetch tags",
                 [sys.executable, "lastfm_tags.py"])

    # Step 4: Classify
    if not args.skip_classify:
        run_step("Classify all tracks",
                 [sys.executable, "classify.py"])

    # Step 5: Push Study playlists
    if args.push:
        run_step("Rebuild + push Study playlists",
                 [sys.executable, "sessions.py", "push", "--all"],
                 bail_on_error=False)

    print(f"\n{'='*60}")
    print("  Pipeline complete!")
    if not args.push:
        print("  Run with --push to update Spotify Study playlists")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
