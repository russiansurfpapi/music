"""Record every Spotify playlist and its membership in library.db.

Answers "which playlists is this song in?" — previously unanswerable, since
only a single snapshot of the discoveries playlist was stored.

Two phases, because they cost very different amounts of quota:

  1. The playlist LIST (name, owner, size) — one page per 50 playlists, so
     ~17 requests for 833 playlists. Always run.
  2. Playlist MEMBERSHIP — one page per 100 tracks, per playlist. Expensive,
     so it is skipped entirely for any playlist whose `snapshot_id` has not
     moved. Scoped to `--managed` by default.

Usage:
    python3 sync_playlists.py                  # list + managed membership
    python3 sync_playlists.py --list-only      # just the catalogue, ~17 reqs
    python3 sync_playlists.py --all            # membership for EVERY playlist
    python3 sync_playlists.py --match "House"  # membership for name matches
"""

import argparse

from auth import get_spotify
from db import connect, init
from tracklist_scraper import _spotify_call, playlist_existing_ids

STUDY_SUFFIX = " — study"
GENRE_SUFFIX = " (other) — study"
SESSION_PREFIX = "🎧 study · "

# Guards against a paging loop if `next` never clears.
MAX_PLAYLIST_OFFSET = 5000


from spotify_budget import guard

def _total(pl):
    """Track count. Spotify moved this from `tracks.total` to `items.total`."""
    for key in ("tracks", "items"):
        node = pl.get(key)
        if isinstance(node, dict) and node.get("total") is not None:
            return node["total"]
    return None


def classify(name):
    """(is_managed, kind, key) for a playlist name."""
    low = name.strip().lower()
    if low.endswith(GENRE_SUFFIX):
        return 1, "genre", low[: -len(GENRE_SUFFIX)]
    if low.startswith(SESSION_PREFIX):
        return 1, "session", low[len(SESSION_PREFIX):]
    if low.endswith(STUDY_SUFFIX):
        return 1, "subgenre", low[: -len(STUDY_SUFFIX)]
    return 0, None, None


def sync_list(sp, conn):
    """Upsert the playlist catalogue. ~1 request per 50 playlists.

    Returns (seen, changed_ids). `changed_ids` is the set whose snapshot_id
    moved since we last looked — the only ones whose membership can differ.
    """
    prior = {r[0]: r[1] for r in conn.execute(
        "SELECT playlist_id, snapshot_id FROM playlists")}
    changed = set()
    offset, seen = 0, 0
    while True:
        page = _spotify_call(sp.current_user_playlists, limit=50, offset=offset)
        items = page.get("items") or []
        for pl in items:
            if not pl or not pl.get("name"):
                continue
            managed, kind, key = classify(pl["name"])
            if prior.get(pl["id"]) != pl.get("snapshot_id"):
                changed.add(pl["id"])
            conn.execute("""
                INSERT INTO playlists (playlist_id, name, owner, description,
                                       is_managed, managed_kind, managed_key,
                                       track_count, snapshot_id, synced_at)
                VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(playlist_id) DO UPDATE SET
                    name=excluded.name, owner=excluded.owner,
                    description=excluded.description,
                    is_managed=excluded.is_managed,
                    managed_kind=excluded.managed_kind,
                    managed_key=excluded.managed_key,
                    track_count=excluded.track_count,
                    snapshot_id=excluded.snapshot_id,
                    synced_at=excluded.synced_at
            """, (pl["id"], pl["name"], (pl.get("owner") or {}).get("id"),
                  pl.get("description"), managed, kind, key,
                  _total(pl), pl.get("snapshot_id")))
            seen += 1
        # Commit per page: a long write transaction blocks every other
        # connection, including the budget's own bookkeeping.
        conn.commit()
        # Paginate on `next`, NOT on a short page. Spotify returns 49-item
        # pages mid-listing; breaking on length truncated the catalogue to
        # 547 of 947 playlists, which makes existing playlists look absent
        # and gets duplicates created.
        if not page.get("next") or not items:
            break
        offset += 50
        if offset > MAX_PLAYLIST_OFFSET:
            print(f"  stopping at offset {offset} (safety cap)")
            break
    return seen, changed


def sync_membership(sp, conn, rows, changed=None, force=False):
    """Replace playlist_tracks for each given playlist.

    Skips any playlist whose snapshot_id did not move — Spotify guarantees it
    changes iff the contents did, so re-reading is provably wasted quota. This
    is what keeps a steady-state sync near zero requests.
    """
    skipped = 0
    for i, (pid, name) in enumerate(rows, 1):
        if not force and changed is not None and pid not in changed:
            have = conn.execute(
                "SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id=?",
                (pid,)).fetchone()[0]
            if have:
                skipped += 1
                continue
        ids = playlist_existing_ids(sp, pid)
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
        conn.executemany(
            "INSERT OR IGNORE INTO playlist_tracks (playlist_id, spotify_id) "
            "VALUES (?,?)", [(pid, s) for s in ids])
        conn.execute("UPDATE playlists SET track_count=?, synced_at="
                     "CURRENT_TIMESTAMP WHERE playlist_id=?", (len(ids), pid))
        conn.commit()
        if i % 10 == 0 or i == len(rows):
            print(f"  [{i}/{len(rows)}] {name[:40]} ({len(ids)})", flush=True)
    if skipped:
        print(f"  skipped {skipped} unchanged (snapshot_id match) — "
              f"0 requests spent on them")


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="membership for EVERY playlist (expensive)")
    ap.add_argument("--match", help="membership for playlists whose name contains this")
    ap.add_argument("--force", action="store_true",
                    help="re-read membership even if snapshot_id is unchanged")
    args = ap.parse_args()

    init()
    sp = get_spotify()
    with connect() as conn:
        n, changed = sync_list(sp, conn)
        print(f"catalogued {n} playlists — {len(changed)} changed since last sync")
        if args.list_only:
            return

        if args.all:
            q, p = "SELECT playlist_id, name FROM playlists", ()
        elif args.match:
            q, p = ("SELECT playlist_id, name FROM playlists WHERE name LIKE ?",
                    (f"%{args.match}%",))
        else:
            q, p = ("SELECT playlist_id, name FROM playlists WHERE is_managed=1", ())
        rows = [(r[0], r[1]) for r in conn.execute(q, p)]
        print(f"syncing membership for {len(rows)} playlists")
        sync_membership(sp, conn, rows, changed, args.force)

        tot = conn.execute("SELECT COUNT(*) FROM playlist_tracks").fetchone()[0]
        print(f"\nplaylist_tracks now holds {tot} rows")


if __name__ == "__main__":
    main()
