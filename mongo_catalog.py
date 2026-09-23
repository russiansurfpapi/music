"""Mirror of what is on the playlists, in MongoDB, keyed by subgenre.

Why a second store at all: `library.db` is the source of truth and stays that
way. But answering "what deep house do I own", "which playlist is this track
on", "what did I add this week" means a join across `tracks`, `classifications`
and `playlist_tracks` on a laptop-local SQLite file. This collection answers
those in one indexed lookup, from anywhere, and it is the shape other tools
want: one document per track, carrying its subgenre and the playlists it sits
on.

It is a **mirror, never an origin**. Every field here is derived from
library.db. If the two disagree, library.db wins and `sync_all()` repairs it.
Nothing reads Mongo to decide what to put on a playlist.

Writes are hooked into `tracklist_scraper._record_membership`, which is the one
function every playlist membership change passes through — the same reason the
Spotify budget lives in `spotify_guard`. Policing call sites was tried in this
repo and missed ~90 of them; a chokepoint covers code that does not exist yet.

    python3 mongo_catalog.py sync          # backfill/repair from library.db
    python3 mongo_catalog.py stats
    python3 mongo_catalog.py find "deep house"
    python3 mongo_catalog.py track 4uLU6hMCjMI75M1A2tKUQC
    python3 mongo_catalog.py gaps          # on a playlist but uncatalogued

A track only enters the catalogue if library.db knows its metadata. Roughly
1,200 IDs sit on Study playlists with no row in `tracks` at all — added by a
live Spotify sync rather than by the builders, so nothing ever fetched their
artist/title, and with no metadata they cannot be classified either. `gaps`
lists them. Closing it means one `sp.track(id)` per ID, which the notes in
.claude/rules/spotify-api.md warn is exactly the call pattern that earned a
10-hour ban, so it wants a paced job of its own rather than a flag here.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from db import connect

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

_client = None
_db = None

# A playlist membership write should never be able to break the playlist write
# it is mirroring. After this many consecutive failures we stop trying for the
# rest of the process rather than pay a 10s timeout on every playlist.
_MAX_FAILURES = 3
_failures = 0
_disabled = False


def get_db():
    global _client, _db
    if _db is not None:
        return _db
    from pymongo import MongoClient
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise RuntimeError("MONGODB_URI not set in .env")
    _client = MongoClient(uri, serverSelectionTimeoutMS=10000,
                          connectTimeoutMS=10000)
    _db = _client[os.environ.get("MONGODB_DB", "music")]
    return _db


def tracks():
    return get_db()["tracks"]


def ensure_indexes():
    t = tracks()
    t.create_index("subgenre")
    t.create_index("genre")
    t.create_index("artist")
    t.create_index("playlists.name")
    t.create_index("updated_at")
    # One row per (artist, title) question — "do I already own this" — without
    # needing the Spotify ID, which scraped tracklists often lack.
    t.create_index([("artist", 1), ("title", 1)])
    return t


# Selected by name, not by position, and every one verified against
# PRAGMA table_info. A first version asked for `t.year` — the column is
# `release_year` — and the whole sync silently wrote nothing.
_TRACK_FIELDS = ["artist", "title", "album", "release_year", "duration_ms",
                 "tempo", "energy", "danceability", "valence", "origin"]
_CLASS_FIELDS = ["genre", "subgenre", "production_dna", "rhythm", "texture",
                 "lineage"]


def _rows_for(spotify_ids):
    """Metadata + classification for these IDs, as dicts, from library.db."""
    ids = [i for i in spotify_ids
           if i and not i.startswith("lfm:") and not i.startswith("fp:")]
    if not ids:
        return []
    cols = (["t.spotify_id"] + [f"t.{c}" for c in _TRACK_FIELDS]
            + [f"c.{c}" for c in _CLASS_FIELDS])
    names = ["spotify_id"] + _TRACK_FIELDS + _CLASS_FIELDS
    out = []
    with connect() as conn:
        for i in range(0, len(ids), 500):      # SQLite caps variables per query
            batch = ids[i:i + 500]
            rows = conn.execute(
                "SELECT %s FROM tracks t LEFT JOIN classifications c "
                "  ON c.spotify_id = t.spotify_id "
                "WHERE t.spotify_id IN (%s)"
                % (", ".join(cols), ",".join("?" * len(batch))),
                batch).fetchall()
            out += [dict(zip(names, r)) for r in rows]
    return out


def record_playlist(playlist_id, playlist_name, spotify_ids):
    """Mirror one playlist's membership. Never raises."""
    global _failures, _disabled
    if _disabled:
        return 0
    try:
        from pymongo import UpdateOne
        rows = _rows_for(spotify_ids)
        if not rows:
            return 0
        now = datetime.now(timezone.utc)
        ops = []
        for r in rows:
            doc = {k: v for k, v in r.items() if k != "spotify_id"}
            doc["updated_at"] = now
            ops.append(UpdateOne(
                {"_id": r["spotify_id"]},
                {"$set": doc,
                 # addToSet, so a track on six playlists accumulates all six
                 # instead of the last write winning.
                 "$addToSet": {"playlists": {"playlist_id": playlist_id,
                                             "name": playlist_name}},
                 "$setOnInsert": {"first_seen": now}},
                upsert=True))
        tracks().bulk_write(ops, ordered=False)
        _failures = 0
        return len(ops)
    except Exception as e:
        _failures += 1
        print(f"  [mongo] mirror failed ({_failures}/{_MAX_FAILURES}): {e}",
              file=sys.stderr)
        if _failures >= _MAX_FAILURES:
            _disabled = True
            print("  [mongo] mirroring disabled for this run — library.db is "
                  "unaffected; re-run `python3 mongo_catalog.py sync` later",
                  file=sys.stderr)
        return 0


def sync_all(name_like="%— Study"):
    """Rebuild the mirror from library.db. Idempotent; the repair path."""
    ensure_indexes()
    with connect() as conn:
        playlists = conn.execute(
            "SELECT playlist_id, name FROM playlists WHERE name LIKE ?",
            (name_like,)).fetchall()
    print(f"mirroring {len(playlists)} playlists")
    # A track can leave a playlist. Membership is rebuilt rather than merged,
    # so a removal actually disappears instead of lingering forever.
    tracks().update_many({}, {"$set": {"playlists": []}})
    total = 0
    for pid, pname in playlists:
        with connect() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT spotify_id FROM playlist_tracks WHERE playlist_id=?",
                (pid,))]
        n = record_playlist(pid, pname, ids)
        total += n
        print(f"  {n:>5}  {pname}")
    # Tracks that ended up on no playlist carry a stale empty list; harmless,
    # but drop them from the mirror so it only describes what is actually out
    # there on Spotify.
    removed = tracks().delete_many({"playlists": {"$size": 0}}).deleted_count
    print(f"\n{total} track-playlist links written, {removed} orphans dropped")
    print(f"{tracks().count_documents({})} tracks in the catalogue")


def gaps():
    """IDs on a Study playlist that the catalogue cannot describe."""
    with connect() as conn:
        rows = conn.execute("""
            SELECT p.name, COUNT(DISTINCT pt.spotify_id) n
            FROM playlist_tracks pt
            JOIN playlists p ON p.playlist_id = pt.playlist_id
            WHERE p.name LIKE '%— Study'
              AND pt.spotify_id NOT LIKE 'lfm:%'
              AND pt.spotify_id NOT LIKE 'fp:%'
              AND pt.spotify_id NOT IN (SELECT spotify_id FROM tracks)
            GROUP BY p.name ORDER BY n DESC""").fetchall()
        total = conn.execute("""
            SELECT COUNT(DISTINCT pt.spotify_id)
            FROM playlist_tracks pt
            JOIN playlists p ON p.playlist_id = pt.playlist_id
            WHERE p.name LIKE '%— Study'
              AND pt.spotify_id NOT LIKE 'lfm:%'
              AND pt.spotify_id NOT LIKE 'fp:%'
              AND pt.spotify_id NOT IN (SELECT spotify_id FROM tracks)""").fetchone()[0]
    print(f"{total} tracks on Study playlists have no row in library.db `tracks`,")
    print("so they carry no artist/title and cannot be classified or catalogued.\n")
    for name, n in rows[:20]:
        print(f"  {n:>5}  {name}")


def stats():
    t = tracks()
    print(f"tracks: {t.count_documents({})}")
    print("\nby subgenre:")
    for r in t.aggregate([{"$match": {"subgenre": {"$ne": None}}},
                          {"$group": {"_id": "$subgenre", "n": {"$sum": 1}}},
                          {"$sort": {"n": -1}}, {"$limit": 20}]):
        print(f"  {r['n']:>5}  {r['_id']}")


def find(subgenre, limit=25):
    t = tracks()
    n = t.count_documents({"subgenre": subgenre})
    print(f"{n} tracks with subgenre {subgenre!r}\n")
    for d in t.find({"subgenre": subgenre}).limit(limit):
        pls = ", ".join(p["name"] for p in d.get("playlists", []))
        print(f"  {d.get('artist','?')[:28]:<28} — {d.get('title','?')[:34]:<34} {pls}")


def track(spotify_id):
    d = tracks().find_one({"_id": spotify_id})
    if not d:
        print("not in the catalogue"); return
    print(f"{d.get('artist')} — {d.get('title')}")
    print(f"  album    {d.get('album')} ({d.get('year')})")
    print(f"  genre    {d.get('genre')} / {d.get('subgenre')}")
    for p in d.get("playlists", []):
        print(f"  playlist {p['name']}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "sync":
        sync_all()
    elif cmd == "stats":
        stats()
    elif cmd == "find":
        find(" ".join(sys.argv[2:]))
    elif cmd == "track":
        track(sys.argv[2])
    elif cmd == "gaps":
        gaps()
    else:
        sys.exit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    main()
