"""Resolve unmatched dj_set_tracks → spotify_id.

Two stages:
  1. CACHE  — pull from the library.db ID cache (no API calls). Run first, always.
  2. SEARCH — for tracks still unresolved, hit Spotify API. ZERO retries, instant bail on 429.

Usage:
    python3 resolve_dj_tracks.py cache                # cache-only pass (free)
    python3 resolve_dj_tracks.py search [--max N]     # API pass, capped at N tracks
"""

import argparse
import json
import os
import sys
import time

from spotipy.exceptions import SpotifyException

from auth import get_spotify
from db import connect
from tracklist_scraper import _is_same_track

TRACK_DELAY = float(os.environ.get("SPOTIFY_TRACK_DELAY", "5.0"))  # extra-conservative default; user has hit bans


def _cache_key(artist: str, title: str) -> str:
    return f"{(artist or '').strip().lower()}||{(title or '').strip().lower()}"


def _is_unidentified(artist: str, title: str) -> bool:
    """DJ-set 'ID' placeholder = unidentified track. Never resolve these — they
    match an actual Spotify artist literally named 'ID' and pollute classifications."""
    a = (artist or "").strip().lower()
    t = (title or "").strip().lower()
    return a in {"id", "i.d.", "unknown", "?"} or (a == "id" and t == "id")


# The ID cache lives in library.db (spotify_id_cache). These delegate to the
# one implementation in tracklist_scraper so there is a single writer.
def _load_cache():
    from tracklist_scraper import _load_spotify_cache
    return _load_spotify_cache()


def _save_cache(cache):
    """No-op: the DB-backed cache persists each key as it is assigned."""
    return


def cmd_cache(args) -> None:
    cache = _load_cache()
    print(f"Cache has {len(cache)} entries ({sum(1 for v in cache.values() if v)} hits)")

    with connect() as conn:
        rows = conn.execute(
            "SELECT rowid, raw_artist, raw_title FROM dj_set_tracks "
            "WHERE spotify_id IS NULL AND raw_artist != 'spotify_uri'"
        ).fetchall()
        print(f"Unresolved rows: {len(rows)}")
        hit = 0
        skipped_id = 0
        for r in rows:
            if _is_unidentified(r["raw_artist"], r["raw_title"]):
                skipped_id += 1
                continue
            key = _cache_key(r["raw_artist"], r["raw_title"])
            sid = cache.get(key)
            if sid:
                conn.execute(
                    "UPDATE dj_set_tracks SET spotify_id=? WHERE rowid=?",
                    (sid, r["rowid"]),
                )
                hit += 1
        # Also handle spotify_uri rows: extract ID from the URI in raw_title.
        uri_rows = conn.execute(
            "SELECT rowid, raw_title FROM dj_set_tracks "
            "WHERE spotify_id IS NULL AND raw_artist='spotify_uri'"
        ).fetchall()
        uri_hit = 0
        for r in uri_rows:
            t = (r["raw_title"] or "").strip()
            if t.startswith("spotify:track:"):
                sid = t.split(":")[-1]
                conn.execute(
                    "UPDATE dj_set_tracks SET spotify_id=? WHERE rowid=?",
                    (sid, r["rowid"]),
                )
                uri_hit += 1
        conn.commit()
        # Refresh per-set resolved counts.
        conn.execute("""
            UPDATE dj_sets SET resolved_count = (
                SELECT COUNT(*) FROM dj_set_tracks
                WHERE dj_set_tracks.set_id = dj_sets.set_id
                  AND dj_set_tracks.spotify_id IS NOT NULL
            )
        """)
        conn.commit()
        print(f"  cache hits: {hit}")
        print(f"  uri-extract hits: {uri_hit}")
        print(f"  skipped unidentified (ID/unknown): {skipped_id}")
        print(f"  total newly resolved: {hit + uri_hit}")


import re as _re
from artist_normalize import clean_title as _clean_title, primary_artist as _primary_artist

# A title tag that names a version of a song rather than the song itself.
_VERSION_WORDS = (r"remix|mix|edit|version|bootleg|rework|remaster(?:ed)?|extended|original|radio|club|vip|"
                  r"instrumental|acapella|dub|sped up|slowed|reverb|flip|refix|re-edit|blend")
_DASH_TAG = _re.compile(rf"(?i)\s+[-–—]\s+[^-–—]*\b(?:{_VERSION_WORDS})\b[^-–—]*$")
_PAREN_TAG = _re.compile(rf"(?i)\s*[\(\[][^)\]]*\b(?:{_VERSION_WORDS}|feat\.?|ft\.?|featuring|with)\b[^)\]]*[\)\]]")


def _fix_text(s):
    """Undo UTF-8 read as Latin-1 ("Fiesta MÃ¡gica" -> "Fiesta Mágica")."""
    s = s or ""
    if "Ã" in s or "Â" in s:
        # Latin-1 covers "Ã¡"; Windows-1252 covers "Ã‰" (É), whose second byte Latin-1 can't encode.
        for enc in ("latin-1", "cp1252"):
            try:
                return s.encode(enc).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
    return s


def _base_title(title):
    """The song name with remix/edit/feat tags removed; the raw title if nothing is left."""
    t = _fix_text(title)
    for _ in range(3):
        t2 = _PAREN_TAG.sub("", _DASH_TAG.sub("", t)).strip(" -–—")
        if t2 == t:
            break
        t = t2
    t = _clean_title(t) or t
    t = _re.sub(r"(?i)\s+(?:remix|re-edit|edit|bootleg|rework|vip)$", "", t) or t
    return t.strip() or _fix_text(title).strip()


def _queries(artist, title):
    """Most faithful first: as written, then the base song by the lead artist."""
    a, t = _fix_text(artist), _fix_text(title)
    pa, bt = _primary_artist(a) or a, _base_title(title)
    out = [f"track:{t} artist:{a}", f"track:{bt} artist:{pa}", f"{pa} {bt}"]
    seen, uniq = set(), []
    for q in out:
        if q.lower() not in seen:
            seen.add(q.lower()); uniq.append(q)
    return uniq, a, t, bt


def _search_track(sp, artist: str, title: str):
    """Return spotify_id or None. Raise on 429 — let caller bail.

    Tries the title as written, then the base song by the lead artist (remix/edit/feat
    tags stripped, broken accents repaired). A query whose hits do not match falls
    through to the next one instead of ending the search.
    """
    queries, a, t, bt = _queries(artist, title)
    lead = (_primary_artist(a) or a).lower()
    for q in queries:
        resp = sp.search(q=q, type="track", limit=3)
        items = (resp.get("tracks") or {}).get("items", [])
        for it in items:
            names = " ".join(x["name"].lower() for x in it.get("artists", []))
            if lead and lead in names:
                return it["id"]
        # No artist overlap: the shared verifier, never items[0] blind (the
        # High Contrast -> "Maui Wowie" lesson). Accept a match on either title.
        for it in items:
            if _is_same_track(a, t, it) or _is_same_track(a, bt, it):
                return it["id"]
    return None


def _set_scope(args):
    """SQL clause + params limiting a run to the sets named by --sets (ids, comma-separated,
    or a path to a JSON list of {"id": ...} rows as discover_dj_sets.py --json writes).
    Absent = every set, as before."""
    spec = getattr(args, "sets", None)
    if not spec:
        return "", []
    if os.path.exists(spec):
        ids = [r["id"] if isinstance(r, dict) else r for r in json.load(open(spec))]
    else:
        ids = [x.strip() for x in spec.split(",") if x.strip()]
    if not ids:
        raise SystemExit("--sets named no set ids")
    return f" AND set_id IN ({','.join('?' * len(ids))})", ids


def cmd_search(args) -> None:
    scope_sql, scope_params = _set_scope(args)
    cache = _load_cache()
    sp = get_spotify()
    me = sp.current_user()
    print(f"Authed: {me['display_name']}")

    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT raw_artist, raw_title FROM dj_set_tracks "
            "WHERE spotify_id IS NULL AND raw_artist != 'spotify_uri' "
            "AND length(raw_artist) > 1 AND length(raw_title) > 1 "
            "AND LOWER(raw_artist) NOT IN ('id', 'i.d.', 'unknown', '?')" + scope_sql,
            scope_params,
        ).fetchall()
        retry = getattr(args, "retry_misses", False)
        # Tracks already retried with the cleaned queries are not retried again.
        retried_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".retried_misses.txt")
        retried = set(open(retried_path).read().splitlines()) if (retry and os.path.exists(retried_path)) else set()
        rows = [r for r in rows if _cache_key(r["raw_artist"], r["raw_title"]) not in retried]
        rows = [r for r in rows
                if (cache.get(_cache_key(r["raw_artist"], r["raw_title"])) is None if retry
                    else _cache_key(r["raw_artist"], r["raw_title"]) not in cache
                    and _cache_key(r["raw_artist"], r["raw_title"]) not in getattr(cache, "misses", ()))]
        if args.max:
            rows = rows[:args.max]
        print(f"Will search {len(rows)} unique (artist, title) pairs" + (" (retrying cached misses)" if retry else ""))
        print(f"Pacing: {TRACK_DELAY}s between calls. Will BAIL instantly on 429.")

        searched = 0
        found = 0
        for r in rows:
            key = _cache_key(r["raw_artist"], r["raw_title"])
            try:
                sid = _search_track(sp, r["raw_artist"], r["raw_title"])
            except SpotifyException as e:
                if getattr(e, "http_status", None) == 429:
                    ra = e.headers.get("Retry-After") if getattr(e, "headers", None) else "?"
                    print(f"\n⛔ 429 after {searched} searches (Retry-After: {ra}s). Saving cache, bailing.")
                    _save_cache(cache)
                    return
                print(f"  err on {r['raw_artist']} - {r['raw_title']}: {e}")
                sid = None
            cache[key] = sid
            if retry:
                with open(retried_path, "a") as fh:
                    fh.write(key + "\n")
            if sid:
                found += 1
                conn.execute(
                    "UPDATE dj_set_tracks SET spotify_id=? "
                    "WHERE raw_artist=? AND raw_title=? AND spotify_id IS NULL",
                    (sid, r["raw_artist"], r["raw_title"]),
                )
                # Commit now: the next `cache[key] = ...` writes on its own
                # connection, and an uncommitted UPDATE here self-deadlocks it
                # ("database is locked"). Same trap as cmd_isrc.
                conn.commit()
            searched += 1
            if searched % 10 == 0:
                _save_cache(cache)
                print(f"  [{searched}/{len(rows)}] found {found} so far")
            time.sleep(TRACK_DELAY)

        conn.commit()
        _save_cache(cache)
        # Refresh per-set counts.
        conn.execute("""
            UPDATE dj_sets SET resolved_count = (
                SELECT COUNT(*) FROM dj_set_tracks
                WHERE dj_set_tracks.set_id = dj_sets.set_id
                  AND dj_set_tracks.spotify_id IS NOT NULL
            )
        """)
        conn.commit()
        print(f"\nDone. Searched {searched}, found {found}.")


def cmd_isrc(args) -> None:
    """Resolve unmatched tracks that have an ISRC via Spotify isrc: search.
    Near-100% hit rate, 1 API call per track."""
    scope_sql, scope_params = _set_scope(args)
    sp = get_spotify()
    cache = _load_cache()
    me = sp.current_user()
    print(f"Authed: {me['display_name']}")

    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT isrc, raw_artist, raw_title FROM dj_set_tracks "
            "WHERE spotify_id IS NULL AND isrc IS NOT NULL AND length(isrc) > 3" + scope_sql,
            scope_params,
        ).fetchall()
        if args.max:
            rows = rows[:args.max]
        print(f"Will resolve {len(rows)} tracks via ISRC lookup")

        searched = 0
        found = 0
        # Cache writes are buffered and applied only after conn.commit().
        # `cache[key] = ...` writes through on its own connection, and doing
        # that while `conn` holds uncommitted UPDATEs self-deadlocks: SQLite
        # allows a single writer, and busy_timeout cannot help when the
        # blocker is this same process. It crashed the run with
        # "database is locked".
        pending_cache = []

        def _flush_cache():
            for k, v in pending_cache:
                cache[k] = v
            pending_cache.clear()

        for r in rows:
            isrc = r["isrc"]
            # A long run meets transient network failures. Only 429 is a
            # reason to stop; a read timeout is a reason to wait and try
            # again. Letting one propagate killed a resolve mid-run and lost
            # nothing but wasted the operator's attention.
            items = []
            for attempt in range(4):
                try:
                    resp = sp.search(q=f"isrc:{isrc}", type="track", limit=1)
                    items = (resp.get("tracks") or {}).get("items", [])
                    break
                except SpotifyException as e:
                    if getattr(e, "http_status", None) == 429:
                        ra = e.headers.get("Retry-After") if getattr(e, "headers", None) else "?"
                        print(f"\n⛔ 429 after {searched} searches (Retry-After: {ra}s). Saving cache, bailing.")
                        _save_cache(cache)
                        return
                    print(f"  err on ISRC {isrc}: {e}")
                    break
                except Exception as e:          # timeout, DNS, reset connection
                    wait = 2 ** attempt
                    if attempt == 3:
                        print(f"  giving up on ISRC {isrc} after 4 tries: {e}")
                        break
                    print(f"  transient error on {isrc} ({type(e).__name__}); "
                          f"retrying in {wait}s")
                    time.sleep(wait)

            if items:
                sid = items[0]["id"]
                found += 1
                conn.execute(
                    "UPDATE dj_set_tracks SET spotify_id=? WHERE isrc=? AND spotify_id IS NULL",
                    (sid, isrc),
                )
                pending_cache.append(
                    (_cache_key(r["raw_artist"], r["raw_title"]), sid))
            searched += 1
            if searched % 10 == 0:
                conn.commit()
                _flush_cache()
                print(f"  [{searched}/{len(rows)}] found {found} so far")
            time.sleep(TRACK_DELAY)

        conn.commit()
        _flush_cache()
        conn.execute("""
            UPDATE dj_sets SET resolved_count = (
                SELECT COUNT(*) FROM dj_set_tracks
                WHERE dj_set_tracks.set_id = dj_sets.set_id
                  AND dj_set_tracks.spotify_id IS NOT NULL
            )
        """)
        conn.commit()
        print(f"\nDone. Searched {searched} ISRCs, found {found}.")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cache")
    s = sub.add_parser("search")
    s.add_argument("--max", type=int, default=0, help="Cap searches per run (default: all)")
    s.add_argument("--retry-misses", action="store_true", help="Search again tracks an earlier run could not find")
    i = sub.add_parser("isrc")
    i.add_argument("--max", type=int, default=0, help="Cap searches per run (default: all)")
    for p_ in (s, i):
        p_.add_argument("--sets", default=None,
                        help="Only these sets: comma-separated ids, or a discover_dj_sets.py --json file")
    args = ap.parse_args()
    if args.cmd == "cache":
        cmd_cache(args)
    elif args.cmd == "isrc":
        cmd_isrc(args)
    else:
        cmd_search(args)


if __name__ == "__main__":
    main()
