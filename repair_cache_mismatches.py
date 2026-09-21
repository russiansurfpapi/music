"""Repair cache entries where the search grabbed the wrong track.

`find_spotify_track` accepted `items[0]` from a Spotify search without checking
that the hit was the track asked for. When a DJ-set tracklist named an obscure
remix Spotify does not carry, the search returned whatever ranked first and the
cache learned that as truth — so a High Contrast jungle mix became Kid Cudi's
"Maui Wowie", which then inherited `dj_set:*` provenance and a seat on
DJ Set Discoveries.

Detection is free: `tracks` already stores the real Spotify metadata for every
cached ID, so the cache key can be compared against it without an API call.
Artist agreement is the signal — titles legitimately differ by remix suffix
("Stranger(DJ BORINGremix)" vs "Stranger - DJ BORING Extended Mix"), artists do
not.

Repairs, all offline:
  1. cache entry  -> NULL (a known miss, so nothing re-learns it)
  2. dj_set_tracks.spotify_id -> NULL for the set rows that mis-resolved
  3. track_sources -> drop `dj_set:*` rows left with no surviving set link

`tracks`, `classifications` and `track_tags` are NOT touched: they describe the
real track correctly. Kid Cudi's row is right; only the claim that a DJ played
it is wrong.

Usage:
    python3 repair_cache_mismatches.py            # report only
    python3 repair_cache_mismatches.py --write
"""

import argparse
import re
import unicodedata

from db import connect

STOP = {"the", "a", "an", "and", "feat", "ft", "featuring", "with", "vs",
        "versus", "presents", "pres", "dj", "mr", "x", "remix", "mix", "edit"}


def toks(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return {w for w in s.split() if w and w not in STOP and len(w) > 1}


def find_mismatches(conn):
    """[(cache_key, spotify_id, real_artist, real_title)] the search got wrong."""
    rows = conn.execute(
        "SELECT sc.cache_key, sc.spotify_id, t.artist, t.title "
        "FROM spotify_id_cache sc JOIN tracks t ON t.spotify_id = sc.spotify_id "
        "WHERE sc.spotify_id IS NOT NULL").fetchall()

    bad = []
    for key, sid, artist, title in rows:
        if "||" not in key:
            continue
        want_artist, want_title = key.split("||", 1)
        ta, aa = toks(want_artist), toks(artist)
        if not ta or not aa:
            continue
        if ta & aa:
            continue  # artist agrees — a remix-suffix title diff is fine
        # No artist in common. Let a strong title agreement rescue it: a
        # tracklist that credited the remixer instead of the original artist
        # is a naming difference, not a wrong track.
        tt, at = toks(want_title), toks(title)
        if tt & at and len(tt & at) >= max(1, min(len(tt), len(at)) // 2):
            continue
        bad.append((key, sid, artist, title))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    with connect() as conn:
        bad = find_mismatches(conn)
        print(f"wrong cache entries: {len(bad)}")

        djt = srcs = plays = 0
        for key, sid, artist, _title in bad:
            want_artist = key.split("||", 1)[0]
            aa = toks(artist)
            for set_id, pos, ra in conn.execute(
                    "SELECT set_id, position, COALESCE(artist, raw_artist) "
                    "FROM dj_set_tracks WHERE spotify_id = ?", (sid,)).fetchall():
                if toks(ra) & aa:
                    continue  # this set really did play the track
                djt += 1
                if args.write:
                    conn.execute(
                        "UPDATE dj_set_tracks SET spotify_id = NULL "
                        "WHERE set_id = ? AND position = ?", (set_id, pos))
            plays += conn.execute(
                "SELECT COUNT(*) FROM playlist_tracks WHERE spotify_id = ?",
                (sid,)).fetchone()[0]
            if args.write:
                conn.execute(
                    "UPDATE spotify_id_cache SET spotify_id = NULL, "
                    "searched_at = CURRENT_TIMESTAMP WHERE cache_key = ?", (key,))

        if args.write:
            # A dj_set: source is only earned by a surviving dj_set_tracks link.
            cur = conn.execute(
                "DELETE FROM track_sources WHERE source LIKE 'dj_set:%' "
                "AND spotify_id NOT IN "
                "(SELECT spotify_id FROM dj_set_tracks WHERE spotify_id IS NOT NULL)")
            srcs = cur.rowcount
            conn.commit()

        print(f"dj_set_tracks links to clear: {djt}")
        print(f"playlist rows affected:       {plays}")
        if args.write:
            print(f"false dj_set: sources dropped:{srcs}")
            print("\nwritten.")
        else:
            print("\n(report only — pass --write to apply)")
            for key, sid, a, t in bad[:15]:
                wa, wt = key.split("||", 1)
                print(f"  {wa[:24]:<24} - {wt[:28]:<28} -> {a[:20]:<20} - {t[:24]}")


if __name__ == "__main__":
    main()
