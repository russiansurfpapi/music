"""Everything library.db knows about a track: metadata, where it came from,
and which playlists it is on.

    python3 track_story.py "black coffee"
    python3 track_story.py --id 04zrPERrSO4CfyargthKJ9
"""

import argparse

from db import connect


def show(conn, sid):
    t = conn.execute("SELECT * FROM tracks WHERE spotify_id=?", (sid,)).fetchone()
    if not t:
        print(f"no track {sid}")
        return
    c = conn.execute("SELECT genre, subgenre FROM classifications "
                     "WHERE spotify_id=?", (sid,)).fetchone()

    print(f"\n{t['artist']} — {t['title']}")
    print(f"  id {sid}")
    meta = [f"album={t['album']}" if t["album"] else None,
            f"year={t['release_year']}" if t["release_year"] else None,
            f"{t['duration_ms'] // 1000}s" if t["duration_ms"] else None]
    print(f"  {'  '.join(m for m in meta if m) or '(no album metadata)'}")
    if c:
        print(f"  genre={c['genre']}  subgenre={c['subgenre']}")
    tags = [r[0] for r in conn.execute(
        "SELECT tag FROM track_tags WHERE spotify_id=? LIMIT 8", (sid,))]
    print(f"  tags: {', '.join(tags) if tags else '(none)'}")

    print("\n  CAME FROM")
    rows = conn.execute(
        "SELECT * FROM track_provenance WHERE spotify_id=? "
        "ORDER BY source_kind, set_date", (sid,)).fetchall()
    if not rows:
        print("    (unattributed)")
    for r in rows:
        if r["source_kind"] == "dj_set":
            date = r["set_date"] or "no date"
            print(f"    {r['dj_name']:<22} {date:<12} #{r['set_position']:<4} "
                  f"{(r['set_title'] or '')[:34]}")
            if r["scraped_as"] and r["scraped_as"] != t["artist"]:
                print(f"      {'':22} scraped as: {r['scraped_as'][:50]}")
        else:
            print(f"    {r['source_kind']}")

    print("\n  ON PLAYLISTS")
    pls = conn.execute("""
        SELECT p.name, p.managed_kind FROM playlist_tracks pt
        JOIN playlists p ON p.playlist_id = pt.playlist_id
        WHERE pt.spotify_id=? ORDER BY p.name""", (sid,)).fetchall()
    if not pls:
        print("    (none recorded — run sync_playlists.py)")
    for p in pls:
        print(f"    {p['name']}  [{p['managed_kind'] or 'unmanaged'}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="artist or title substring")
    ap.add_argument("--id", help="exact spotify_id")
    args = ap.parse_args()

    with connect() as conn:
        if args.id:
            show(conn, args.id)
            return
        rows = conn.execute(
            "SELECT spotify_id FROM tracks WHERE LOWER(artist) LIKE ? "
            "OR LOWER(title) LIKE ? LIMIT 3",
            (f"%{args.query.lower()}%",) * 2).fetchall()
        if not rows:
            print("no match")
        for r in rows:
            show(conn, r[0])


if __name__ == "__main__":
    main()
