"""Build cross-DJ subgenre 'study' playlists — one playlist per subgenre
combining tracks from every DJ/source in the library.

Named "<Subgenre Title Case> — Study" so they sit next to the per-DJ Study
playlists and stay distinct from any scraped 'library' playlists.

Zero new Spotify searches: real Spotify IDs (from `tracks.spotify_id`) are
added directly; synthetic `lfm:*` / `fp:*` IDs are resolved via
the library.db ID cache keyed on (artist, title). Missing = skip.

Usage:
    python3 build_subgenre_playlists.py                         # all subgenres ≥ threshold
    python3 build_subgenre_playlists.py --subgenre "deep house" # one subgenre
    python3 build_subgenre_playlists.py --min-tracks 25         # threshold (default 25)
    python3 build_subgenre_playlists.py --dry-run               # preview only
    python3 build_subgenre_playlists.py --cap 500               # cap tracks per playlist
"""

import argparse
import json
import os
import sqlite3
import time

from tracklist_scraper import (
    _load_spotify_cache, _cache_key,
    _get_or_create_playlist, _add_tracks_to_playlist, _sync_playlist_tracks,
    cached_membership,
)
from auth import get_spotify
from db import connect

# The DJ Set Discoveries playlist. Its membership snapshot lives in the
# `playlist_snapshots` table; refresh with snapshot_discovery_playlist.py.
DISCOVERY_PLAYLIST_ID = "4lIwulyeCrzOPTkDoZFe1l"

# Where a subgenre playlist draws its tracks from. `library.db` is the source
# of truth: it holds every track scraped from every DJ set, which is a strict
# superset of what ever made it onto the Spotify discoveries playlist.
SOURCES = {
    # Everything found in any scraped DJ set — the real "discoveries" pool.
    "dj-sets": "AND c.spotify_id IN "
               "(SELECT spotify_id FROM dj_set_tracks WHERE spotify_id IS NOT NULL)",
    # Only what is literally on the DJ Set Discoveries playlist right now.
    "playlist": "AND c.spotify_id IN (SELECT spotify_id FROM _pool)",
    # No restriction at all.
    "library": "",
}


from spotify_budget import guard

def load_pool(playlist_id=DISCOVERY_PLAYLIST_ID):
    """Load the playlist membership snapshot used by --source playlist."""
    with connect() as conn:
        # playlist_tracks is the live membership table (sync_playlists.py);
        # playlist_snapshots is the older single-playlist store kept as a
        # fallback so this keeps working if the sync has not run yet.
        ids = {r[0] for r in conn.execute(
            "SELECT spotify_id FROM playlist_tracks WHERE playlist_id=?",
            (playlist_id,))}
        if not ids:
            ids = {r[0] for r in conn.execute(
                "SELECT spotify_id FROM playlist_snapshots WHERE playlist_id=?",
                (playlist_id,))}
    if not ids:
        raise SystemExit(
            "no membership recorded — run: python3 sync_playlists.py "
            f"--match 'DJ Set Discoveries'  (or snapshot_discovery_playlist.py)")
    return ids


def gather_subgenre_tracks(conn, min_tracks=25, subgenre=None,
                           source="dj-sets", only_ids=None, level="subgenre"):
    """Return {subgenre: [(spotify_id_or_None, artist, title), ...]} ordered.

    Orders by real Spotify IDs first, then synthetic. Within each, most
    recently-seen (most-played-across-sets) first via LEFT JOIN count.

    `source` picks the pool (see SOURCES). `only_ids` is the Spotify-ID
    snapshot required by source="playlist".
    """
    conn.row_factory = sqlite3.Row

    filter_clause = ("AND c.genre = ?" if level == "genre"
                     else "AND c.subgenre = ?") if subgenre else ""
    params = (subgenre,) if subgenre else ()

    if source == "playlist":
        if only_ids is None:
            raise ValueError("source='playlist' needs only_ids")
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _pool (spotify_id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM _pool")
        conn.executemany("INSERT OR IGNORE INTO _pool VALUES (?)",
                         [(i,) for i in only_ids])
    filter_clause += " " + SOURCES[source]

    # Genre level is a catch-all for tracks Last.fm only ever tagged with an
    # umbrella genre ("house", "rock"). Restricted to subgenre IS NULL so it
    # never duplicates what the subgenre playlists already hold.
    if level == "genre":
        group_col, null_guard = "c.genre", "c.genre IS NOT NULL AND c.subgenre IS NULL"
    else:
        group_col, null_guard = "c.subgenre", "c.subgenre IS NOT NULL"

    # Use LEFT JOIN to dj_set_tracks for play-count ordering (popularity signal)
    rows = conn.execute(f"""
        SELECT {group_col} AS grp,
               t.spotify_id,
               t.artist,
               t.title,
               COALESCE(pc.plays, 0) AS plays
        FROM classifications c
        JOIN tracks t ON t.spotify_id = c.spotify_id
        LEFT JOIN (
            SELECT spotify_id, COUNT(*) AS plays
            FROM dj_set_tracks
            WHERE spotify_id IS NOT NULL
            GROUP BY spotify_id
        ) pc ON pc.spotify_id = t.spotify_id
        WHERE {null_guard}
          {filter_clause}
        ORDER BY grp,
                 CASE WHEN t.spotify_id LIKE 'lfm:%' OR t.spotify_id LIKE 'fp:%'
                      THEN 1 ELSE 0 END,
                 plays DESC,
                 t.artist
    """, params).fetchall()

    by_sg = {}
    for r in rows:
        by_sg.setdefault(r["grp"], []).append(
            (r["spotify_id"], r["artist"], r["title"])
        )

    return {sg: tracks for sg, tracks in by_sg.items() if len(tracks) >= min_tracks}


def resolve_track_ids(tracks, cache, cap=None):
    """Return (resolved_ids_in_order, missing_count).

    Real Spotify IDs pass through directly; synthetic lfm:/fp: IDs get looked
    up via `cache[_cache_key(artist, title)]`. Dedupe by final Spotify ID.
    """
    seen = set()
    out = []
    missing = 0
    for spid, artist, title in tracks:
        if spid and not (spid.startswith("lfm:") or spid.startswith("fp:")):
            resolved = spid
        else:
            resolved = cache.get(_cache_key(artist, title))
        if resolved and resolved not in seen:
            out.append(resolved)
            seen.add(resolved)
            if cap and len(out) >= cap:
                break
        elif not resolved:
            missing += 1
    return out, missing


# Words .title() mangles. Keep this list in sync with the playlist names that
# already exist on Spotify — changing an entry renames nothing on its own, it
# just makes the builder create a NEW playlist under the new spelling.
ACRONYMS = {"Uk": "UK", "Dnb": "DnB", "Rnb": "R&B", "Idm": "IDM", "Dj": "DJ"}


def pretty_subgenre(subgenre):
    """Title-case a subgenre, keeping slashes/parentheses and acronyms intact."""
    pretty = " ".join(
        ACRONYMS.get(word, word) for word in subgenre.title().split(" ")
    )
    return pretty.replace("Dnb", "DnB").replace("Rnb", "R&B")


def build_for_subgenre(sp, cache, subgenre, tracks, cap=None, dry_run=False,
                       sync=False, min_tracks=0, level="subgenre"):
    pretty = pretty_subgenre(subgenre)
    # "(Other)" marks a genre catch-all so it never reads as a subgenre playlist.
    suffix = " (Other) — Study" if level == "genre" else " — Study"
    playlist_name = f"{pretty}{suffix}"

    ids, missing = resolve_track_ids(tracks, cache, cap)

    real_count = sum(
        1 for spid, _, _ in tracks
        if spid and not (spid.startswith("lfm:") or spid.startswith("fp:"))
    )
    synth_count = len(tracks) - real_count

    print(f"\n▣ {playlist_name}")
    print(f"  tracks in subgenre:          {len(tracks)}")
    print(f"    with real Spotify IDs:     {real_count}")
    print(f"    synthetic (lfm:/fp:):      {synth_count}")
    print(f"  resolved (in playlist order):{len(ids)}")
    print(f"  not in cache / Spotify:      {missing}")
    if cap and len(ids) >= cap:
        print(f"  capped at {cap}")

    if len(ids) < max(1, min_tracks):
        # The DB threshold counts rows; this counts tracks that actually
        # resolve to a Spotify ID, which is what ends up on the playlist.
        print(f"  only {len(ids)} resolvable — below --min-tracks, skipping")
        return 0

    if dry_run:
        print("  (dry-run)"); return 0


    pid, url = _get_or_create_playlist(sp, playlist_name)
    if not pid:
        print("  FAILED to create/find playlist"); return 0
    print(f"  → {url}")
    if sync:
        # Reuse the stored membership when the catalogue refresh showed this
        # playlist unchanged — saves a read page per 100 tracks, per playlist.
        added, removed = _sync_playlist_tracks(sp, pid, ids,
                                               existing=cached_membership(pid))
        print(f"  synced: +{added} new, -{removed} removed "
              f"({len(ids) - added} already correct)")
    else:
        added = _add_tracks_to_playlist(sp, pid, ids)
        print(f"  added {added} new tracks ({len(ids) - added} were already present)")
    return len(ids)


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subgenre", default=None,
                    help="single subgenre name (exact, case-sensitive)")
    ap.add_argument("--min-tracks", type=int, default=25,
                    help="minimum tracks for a subgenre to get a playlist (default 25)")
    ap.add_argument("--cap", type=int, default=None,
                    help="cap tracks per playlist (default: no cap — Spotify allows 10k)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source", choices=tuple(SOURCES), default="dj-sets",
                    help="'dj-sets' (default) = every track scraped from any DJ "
                         "set, straight out of library.db; 'playlist' = only what "
                         "is on the DJ Set Discoveries playlist right now; "
                         "'library' = every classified track you own")
    ap.add_argument("--level", choices=("subgenre", "genre"), default="subgenre",
                    help="'genre' builds '<Genre> (Other) — Study' catch-alls for "
                         "tracks that only ever got an umbrella tag")
    ap.add_argument("--sync", action="store_true",
                    help="make each playlist contain EXACTLY the selected tracks, "
                         "removing anything else already on it")
    args = ap.parse_args()

    cache = _load_spotify_cache()
    sp = None if args.dry_run else get_spotify()

    if sp is not None and args.sync:
        # ~17 requests refreshes every snapshot_id, which then lets each
        # playlist skip its own read pages. Net saving is large.
        from sync_playlists import sync_list
        with connect() as conn:
            n, changed = sync_list(sp, conn)
        print(f"catalogue refreshed: {n} playlists, {len(changed)} changed")

    only_ids = None
    if args.source == "playlist":
        only_ids = load_pool()
        print(f"Source: DJ Set Discoveries playlist — {len(only_ids)} tracks\n")
    elif args.source == "dj-sets":
        print("Source: library.db — every track scraped from any DJ set\n")
    else:
        print("Source: whole library\n")

    with connect() as conn:
        by_sg = gather_subgenre_tracks(conn, args.min_tracks, args.subgenre,
                                       args.source, only_ids, args.level)

    if not by_sg:
        print(f"no subgenres with ≥{args.min_tracks} tracks")
        return

    # Sort by track count desc for visibility
    ordered = sorted(by_sg.items(), key=lambda kv: -len(kv[1]))
    print(f"Building {len(ordered)} subgenre playlists "
          f"(min {args.min_tracks} tracks each):\n")
    for sg, _ in ordered:
        print(f"  {sg}: {len(by_sg[sg])}")

    for sg, tracks in ordered:
        build_for_subgenre(sp, cache, sg, tracks, args.cap, args.dry_run,
                           sync=args.sync, min_tracks=args.min_tracks,
                           level=args.level)
        if not args.dry_run and len(ordered) > 1:
            time.sleep(3)  # gentle pacing between playlists


if __name__ == "__main__":
    main()
