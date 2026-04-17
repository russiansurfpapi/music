"""Auto-detect + generate structured listening-session playlists.

Kinds:
  - ab         "A/B: X vs Y"            4-5 A → 4-5 B → 2-3 that match both  (~10-13 tracks)
  - evolution  "Evolution: <genre>"     chronological by release_year         (~15-20 tracks)
  - thread     "Thread: <element> across genres"                              (~12-15 tracks)
  - focus      "Focus: <subgenre>"      deep single-subgenre                  (~20-25 tracks)
  - (set — deferred; needs tempo/energy we don't have)

Detection scans `classifications` + `tracks`; viability thresholds encode the spec:
  ab:        both sides ≥5 tracks
  evolution: genre spans ≥3 decades AND ≥15 tracks
  thread:    DNA/rhythm canonical appears in ≥3 distinct genres with ≥8 total tracks
  focus:     subgenre with ≥15 tracks

Usage:
    python3 sessions.py detect
    python3 sessions.py build --all              # build all viable, save to sessions table (no Spotify push)
    python3 sessions.py build --id ab:909-vs-808
    python3 sessions.py push --id ab:909-vs-808  # create/update on Spotify
    python3 sessions.py push --all
"""

import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

import requests
import yaml
from spotipy.exceptions import SpotifyException

from auth import get_spotify
from db import connect

random.seed(42)


# ---------------------------------------------------------------------------
# Schema — sessions table lives here so this file is self-contained.
# ---------------------------------------------------------------------------

SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                   TEXT PRIMARY KEY,
    kind                 TEXT NOT NULL,
    name                 TEXT NOT NULL,
    description          TEXT,
    track_ids_json       TEXT NOT NULL,
    spotify_playlist_id  TEXT,
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_kind ON sessions(kind);
"""


def _ensure_schema(conn) -> None:
    conn.executescript(SESSIONS_SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

def _slug(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# ---------------------------------------------------------------------------
# Reading classifications
# ---------------------------------------------------------------------------

def _load_tracks(conn) -> List[dict]:
    """Return all classified tracks joined with year + title/artist."""
    rows = conn.execute("""
        SELECT t.spotify_id AS id, t.title, t.artist, t.release_year,
               c.genre, c.subgenre, c.rhythm,
               c.production_dna, c.texture, c.lineage, c.confidence
        FROM tracks t
        JOIN classifications c ON c.spotify_id = t.spotify_id
        WHERE c.genre IS NOT NULL
    """).fetchall()
    out = []
    for r in rows:
        def _list(col):
            try:
                return json.loads(col) if col else []
            except (json.JSONDecodeError, TypeError):
                return []
        out.append({
            "id": r["id"], "title": r["title"], "artist": r["artist"],
            "year": r["release_year"],
            "genre": r["genre"], "subgenre": r["subgenre"], "rhythm": r["rhythm"],
            "dna": _list(r["production_dna"]),
            "texture": _list(r["texture"]),
            "lineage": _list(r["lineage"]),
            "confidence": r["confidence"],
        })
    return out


def _index_by(tracks: List[dict], key_fn) -> Dict[str, List[dict]]:
    idx: Dict[str, List[dict]] = defaultdict(list)
    for t in tracks:
        v = key_fn(t)
        if isinstance(v, list):
            for x in v:
                if x:
                    idx[x].append(t)
        elif v:
            idx[v].append(t)
    return idx


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

# Semantically interesting A/B pairs. Only generated if both sides have ≥5 tracks.
AB_PAIRS = [
    # production_dna layer
    ("dna", "909", "808"),
    ("dna", "analog", "sample-based"),
    ("dna", "breakbeat-based", "synth-driven"),
    # subgenre
    ("subgenre", "deep house", "tech house"),
    ("subgenre", "minimal techno", "industrial techno"),
    ("subgenre", "boom bap", "trap"),
    ("subgenre", "uk garage", "dubstep (original)"),
    ("subgenre", "chicago house", "detroit techno"),
    ("subgenre", "dream pop", "shoegaze"),
    # rhythm
    ("rhythm", "four-on-the-floor", "breakbeat"),
    ("rhythm", "four-on-the-floor", "dembow"),
    ("rhythm", "halftime", "2-step/shuffle"),
    # texture
    ("texture", "dark", "warm/soulful"),
    ("texture", "stripped/minimal", "atmospheric"),
    ("texture", "raw/gritty", "warm/soulful"),
    # lineage
    ("lineage", "chicago", "detroit"),
    ("lineage", "south london", "new york"),
    ("lineage", "atlanta", "los angeles"),
    ("lineage", "70s", "20s"),
]


def _tracks_by_layer_value(tracks, layer: str, value: str) -> List[dict]:
    """Return tracks matching (layer, value)."""
    out = []
    for t in tracks:
        if layer == "genre" and t["genre"] == value:           out.append(t)
        elif layer == "subgenre" and t["subgenre"] == value:   out.append(t)
        elif layer == "rhythm" and t["rhythm"] == value:       out.append(t)
        elif layer == "dna" and value in t["dna"]:             out.append(t)
        elif layer == "texture" and value in t["texture"]:     out.append(t)
        elif layer == "lineage" and value in t["lineage"]:     out.append(t)
    return out


def detect_ab(tracks) -> List[dict]:
    sessions = []
    for layer, a, b in AB_PAIRS:
        ta = _tracks_by_layer_value(tracks, layer, a)
        tb = _tracks_by_layer_value(tracks, layer, b)
        if len(ta) >= 5 and len(tb) >= 5:
            sessions.append({
                "id": f"ab:{_slug(a)}-vs-{_slug(b)}",
                "kind": "ab", "layer": layer, "a": a, "b": b,
                "a_count": len(ta), "b_count": len(tb),
            })
    return sessions


def detect_evolution(tracks) -> List[dict]:
    by_genre = _index_by(tracks, lambda t: t["genre"])
    sessions = []
    for genre, ts in by_genre.items():
        years = sorted({t["year"] for t in ts if t["year"]})
        if len(years) < 3:
            continue
        decades = {y // 10 * 10 for y in years}
        if len(decades) < 3 or len(ts) < 15:
            continue
        sessions.append({
            "id": f"evolution:{_slug(genre)}",
            "kind": "evolution", "genre": genre,
            "track_count": len(ts), "decades": sorted(decades),
            "year_range": (min(years), max(years)),
        })
    return sessions


def detect_thread(tracks) -> List[dict]:
    """Find DNA/rhythm canonicals that appear across ≥3 distinct genres."""
    sessions = []
    for layer in ("dna", "rhythm"):
        if layer == "dna":
            values = set()
            for t in tracks:
                values.update(t["dna"])
        else:
            values = {t["rhythm"] for t in tracks if t["rhythm"]}
        for v in values:
            ts = _tracks_by_layer_value(tracks, layer, v)
            genres = {t["genre"] for t in ts}
            if len(genres) >= 3 and len(ts) >= 8:
                sessions.append({
                    "id": f"thread:{layer}-{_slug(v)}",
                    "kind": "thread", "layer": layer, "value": v,
                    "genres": sorted(genres), "track_count": len(ts),
                })
    return sessions


def detect_focus(tracks) -> List[dict]:
    by_sub = _index_by(tracks, lambda t: t["subgenre"])
    sessions = []
    for sub, ts in by_sub.items():
        if len(ts) >= 15:
            sessions.append({
                "id": f"focus:{_slug(sub)}",
                "kind": "focus", "subgenre": sub, "track_count": len(ts),
            })
    return sessions


def detect_all(tracks) -> List[dict]:
    return (detect_ab(tracks) + detect_evolution(tracks)
            + detect_thread(tracks) + detect_focus(tracks))


# ---------------------------------------------------------------------------
# Building (turning a detected session into an ordered track list)
# ---------------------------------------------------------------------------

def _year_bucket_sample(ts: List[dict], n: int) -> List[dict]:
    """Pick n tracks spread across release-year buckets for even coverage."""
    ts = [t for t in ts if t["year"]]
    if len(ts) <= n:
        return sorted(ts, key=lambda x: x["year"])
    ts_sorted = sorted(ts, key=lambda x: x["year"])
    step = len(ts_sorted) / n
    picks = [ts_sorted[int(i * step)] for i in range(n)]
    return picks


def _dedupe_keep_order(ts: List[dict]) -> List[dict]:
    seen = set()
    out = []
    for t in ts:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        out.append(t)
    return out


def build_ab(tracks, spec) -> Tuple[List[str], str]:
    a, b = spec["a"], spec["b"]
    ta = _tracks_by_layer_value(tracks, spec["layer"], a)
    tb = _tracks_by_layer_value(tracks, spec["layer"], b)
    both = [t for t in ta if t in tb]
    only_a = [t for t in ta if t not in both]
    only_b = [t for t in tb if t not in both]

    random.shuffle(only_a); random.shuffle(only_b); random.shuffle(both)
    picks = only_a[:8] + only_b[:8] + both[:4]
    picks = _dedupe_keep_order(picks)
    desc = (f"First 8: {a}. Next 8: {b}. Final 4: tracks that blur the line. "
            f"LISTEN FOR — what stays constant between {a} and {b} (often tempo or groove skeleton), "
            f"and what shifts (texture, rhythm feel, harmonic vocabulary). "
            f"The distinction is the lesson — name the difference in your own words before labels.")
    return [t["id"] for t in picks], desc


def build_evolution(tracks, spec) -> Tuple[List[str], str]:
    ts = [t for t in tracks if t["genre"] == spec["genre"] and t["year"]]
    picks = _year_bucket_sample(ts, 80)
    picks.sort(key=lambda t: t["year"])
    yr_lo, yr_hi = spec["year_range"]
    desc = (f"Chronological timeline of {spec['genre']}, {yr_lo}–{yr_hi}. "
            f"{len(picks)} tracks across {len(spec['decades'])} decades. "
            f"LISTEN FOR — production eras: analog hardware (warm, dirty) → digital precision (bright, clean) → "
            f"software-everything (anything, often referential). The genre label stays constant; the sound underneath doesn't.")
    return [t["id"] for t in picks], desc


def build_thread(tracks, spec) -> Tuple[List[str], str]:
    ts = _tracks_by_layer_value(tracks, spec["layer"], spec["value"])
    by_genre: Dict[str, List[dict]] = defaultdict(list)
    for t in ts:
        by_genre[t["genre"]].append(t)
    for g in by_genre:
        random.shuffle(by_genre[g])

    order = sorted(by_genre.keys(), key=lambda g: -len(by_genre[g]))
    picks: List[dict] = []
    target = 80
    while len(picks) < target:
        added = False
        for g in order:
            if by_genre[g]:
                picks.append(by_genre[g].pop())
                added = True
                if len(picks) >= target:
                    break
        if not added:
            break
    label = spec["value"]
    desc = (f"The {label} across {len(order)} genres: "
            f"{', '.join(order[:6])}{'…' if len(order) > 6 else ''}. "
            f"LISTEN FOR — the {label} itself: notice how it survives intact while the music around it changes completely. "
            f"Tempo shifts, instrumentation shifts, but the thread holds. {len(picks)} tracks.")
    return [t["id"] for t in picks], desc


def build_focus(tracks, spec) -> Tuple[List[str], str]:
    ts = [t for t in tracks if t["subgenre"] == spec["subgenre"]]
    ts_with_year = [t for t in ts if t["year"]]
    ts_no_year = [t for t in ts if not t["year"]]
    # Full subgenre, up to cap. Sort by year asc, unyear'd at end.
    CAP = 500
    ts_with_year.sort(key=lambda t: t["year"])
    picks = (ts_with_year + ts_no_year)[:CAP]
    if picks and ts_with_year:
        yr_lo = ts_with_year[0]["year"]
        yr_hi = ts_with_year[-1]["year"] if ts_with_year else None
        yr = f", {yr_lo}–{yr_hi}"
    else:
        yr = ""
    cue = _listen_cue_for_subgenre(spec["subgenre"])
    listen_for = f" LISTEN FOR — {cue}." if cue else ""
    desc = (f"Every {spec['subgenre']} track in my library"
            + yr + f" ({len(picks)} tracks)."
            + listen_for
            + " Put it on, stay put — this is immersion, not a lesson.")
    return [t["id"] for t in picks], desc


NAME_PREFIX = "🎧 Study · "


# ---------------------------------------------------------------------------
# Glossary-aware "listen for" cues per subgenre.
# ---------------------------------------------------------------------------

_GLOSSARY: Optional[Dict[str, Dict]] = None


def _load_glossary() -> Dict[str, Dict]:
    """Load dj_glossary.yaml once. Key subgenres by lowercase name AND canonical id."""
    global _GLOSSARY
    if _GLOSSARY is not None:
        return _GLOSSARY
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dj_glossary.yaml")
    if not os.path.exists(path):
        _GLOSSARY = {}
        return _GLOSSARY
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    subgenres = data.get("subgenres") or {}
    out: Dict[str, Dict] = {}
    for canonical_id, entry in subgenres.items():
        display = (entry.get("name") or canonical_id).lower()
        out[display] = entry
        out[canonical_id.replace("_", " ")] = entry
    _GLOSSARY = out
    return _GLOSSARY


def _listen_cue_for_subgenre(subgenre: str) -> str:
    """Return a short 'listen for' string drawn from glossary sonic_markers, or a generic fallback."""
    g = _load_glossary()
    entry = g.get(subgenre.lower())
    if entry:
        markers = entry.get("sonic_markers") or []
        if markers:
            # Take 1-2 most specific markers, collapsed.
            pick = markers[:2]
            return " • ".join(m.strip().rstrip(".") for m in pick)
    return ""


def build_session(tracks, spec) -> Tuple[List[str], str, str]:
    kind = spec["kind"]
    if kind == "ab":
        ids, desc = build_ab(tracks, spec)
        name = f"{NAME_PREFIX}A/B: {spec['a']} vs {spec['b']}"
    elif kind == "evolution":
        ids, desc = build_evolution(tracks, spec)
        name = f"{NAME_PREFIX}Evolution: {spec['genre']}"
    elif kind == "thread":
        ids, desc = build_thread(tracks, spec)
        name = f"{NAME_PREFIX}Thread: {spec['value']} across genres"
    elif kind == "focus":
        ids, desc = build_focus(tracks, spec)
        name = f"{NAME_PREFIX}Focus: {spec['subgenre']}"
    else:
        raise ValueError(f"unknown kind: {kind}")
    return ids, name, desc


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _upsert_session(conn, sid, kind, name, desc, track_ids):
    conn.execute(
        """INSERT INTO sessions (id, kind, name, description, track_ids_json)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name,
             description=excluded.description,
             track_ids_json=excluded.track_ids_json,
             updated_at=CURRENT_TIMESTAMP""",
        (sid, kind, name, desc, json.dumps(track_ids)),
    )


# ---------------------------------------------------------------------------
# Spotify push — uses /me/playlists via requests (sp.user_playlist_create 403s)
# ---------------------------------------------------------------------------

def _sp_token(sp) -> str:
    return sp.auth_manager.get_access_token(as_dict=False)


def _clean_desc(description: str) -> str:
    # Spotify 400s on newlines in description. Collapse to single spaces.
    d = re.sub(r"\s+", " ", description or "").strip()
    return d[:300]


def _create_playlist(sp, user_id: str, name: str, description: str) -> str:
    # /me/playlists works in dev mode; /users/{id}/playlists returns 403.
    token = _sp_token(sp)
    r = requests.post(
        "https://api.spotify.com/v1/me/playlists",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"name": name, "description": _clean_desc(description), "public": False},
        timeout=30,
    )
    if r.status_code == 429:
        raise RuntimeError(f"429 creating playlist (Retry-After: {r.headers.get('Retry-After')})")
    r.raise_for_status()
    return r.json()["id"]


def _replace_playlist_tracks(sp, playlist_id: str, track_ids: List[str]) -> None:
    """Append tracks via spotipy (direct requests 403 in dev mode)."""
    for i in range(0, len(track_ids), 100):
        if i > 0:
            time.sleep(0.6)
        try:
            sp.playlist_add_items(playlist_id, track_ids[i:i + 100])
        except SpotifyException as e:
            if getattr(e, "http_status", None) == 429:
                ra = e.headers.get("Retry-After") if getattr(e, "headers", None) else "?"
                raise RuntimeError(f"429 on add (Retry-After: {ra})") from e
            raise


def _clear_playlist(sp, playlist_id: str) -> None:
    """Remove all tracks from a playlist by reading current contents and removing them."""
    current_ids: List[str] = []
    offset = 0
    while True:
        resp = sp.playlist_items(playlist_id, limit=100, offset=offset,
                                  fields="items(track(id)),next", additional_types=("track",))
        items = resp.get("items", [])
        for it in items:
            t = it.get("track") or {}
            if t.get("id"):
                current_ids.append(t["id"])
        if not resp.get("next") or len(items) < 100:
            break
        offset += 100
    for i in range(0, len(current_ids), 100):
        sp.playlist_remove_all_occurrences_of_items(playlist_id, current_ids[i:i + 100])
        time.sleep(0.4)


def push_session(conn, sp, user_id: str, sid: str) -> None:
    row = conn.execute(
        "SELECT name, description, track_ids_json, spotify_playlist_id FROM sessions WHERE id=?",
        (sid,),
    ).fetchone()
    if not row:
        print(f"  {sid}: not in DB — run build first")
        return
    name, desc, ids_json, pl_id = row
    ids = json.loads(ids_json)
    if not ids:
        print(f"  {sid}: no tracks")
        return
    if not pl_id:
        pl_id = _create_playlist(sp, user_id, name, desc or "")
        conn.execute("UPDATE sessions SET spotify_playlist_id=? WHERE id=?", (pl_id, sid))
        conn.commit()
        print(f"  created: {name} ({pl_id})")
    else:
        _clear_playlist(sp, pl_id)
    _replace_playlist_tracks(sp, pl_id, ids)
    print(f"  pushed: {name} — {len(ids)} tracks")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_detect(args) -> None:
    with connect() as conn:
        tracks = _load_tracks(conn)
        print(f"Loaded {len(tracks)} classified tracks.\n")
        by_kind: Dict[str, List[dict]] = defaultdict(list)
        for s in detect_all(tracks):
            by_kind[s["kind"]].append(s)
        for kind in ("ab", "evolution", "thread", "focus"):
            sessions = by_kind[kind]
            print(f"── {kind.upper()} ── {len(sessions)} viable")
            for s in sessions:
                if kind == "ab":
                    print(f"  {s['id']:50s} A={s['a_count']:>4} B={s['b_count']:>4}")
                elif kind == "evolution":
                    lo, hi = s["year_range"]
                    print(f"  {s['id']:50s} n={s['track_count']:>4} {lo}–{hi}")
                elif kind == "thread":
                    print(f"  {s['id']:50s} n={s['track_count']:>4} genres={len(s['genres'])}")
                elif kind == "focus":
                    print(f"  {s['id']:50s} n={s['track_count']:>4}")
            print()


def cmd_build(args) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        tracks = _load_tracks(conn)
        sessions = detect_all(tracks)
        if args.id:
            sessions = [s for s in sessions if s["id"] == args.id]
            if not sessions:
                print(f"No session with id {args.id}")
                sys.exit(1)
        n = 0
        for s in sessions:
            ids, name, desc = build_session(tracks, s)
            if not ids:
                continue
            _upsert_session(conn, s["id"], s["kind"], name, desc, ids)
            n += 1
            print(f"  built: {name} ({len(ids)} tracks)")
        conn.commit()
        print(f"\nBuilt/updated {n} sessions.")


def cmd_update_descriptions(args) -> None:
    """Update name/description on existing Spotify playlists. Doesn't touch tracks.

    BAILS INSTANTLY on 429 — no retries, per repo rules (bans escalate fast).
    """
    with connect() as conn:
        sp = get_spotify()
        rows = conn.execute(
            "SELECT id, name, description, spotify_playlist_id FROM sessions "
            "WHERE spotify_playlist_id IS NOT NULL ORDER BY kind, id"
        ).fetchall()
        done = 0
        for row in rows:
            sid, name, desc, pl_id = row
            try:
                sp.playlist_change_details(
                    pl_id, name=name, description=_clean_desc(desc or "")
                )
                done += 1
                print(f"  updated: {name}")
            except SpotifyException as e:
                status = getattr(e, "http_status", None)
                if status == 429:
                    ra = e.headers.get("Retry-After") if getattr(e, "headers", None) else "?"
                    print(f"\n⛔ 429 after {done} updates — Retry-After: {ra}s. BAILING.")
                    return
                if status == 403:
                    print(f"  {sid}: 403 — skip (dev mode)")
                    continue
                raise
            time.sleep(1.2)  # conservative — 50 updates/min, well under ceiling


def cmd_push(args) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        sp = get_spotify()
        me = sp.current_user()
        user_id = me["id"]
        print(f"Authed as: {me['display_name']} ({user_id})\n")
        if args.id:
            rows = conn.execute(
                "SELECT id FROM sessions WHERE id=?", (args.id,)
            ).fetchall()
        elif args.ids_file:
            with open(args.ids_file) as f:
                wanted = {line.strip() for line in f if line.strip() and not line.startswith("#")}
            rows = conn.execute(
                "SELECT id FROM sessions WHERE id IN ({})".format(
                    ",".join("?" * len(wanted))), tuple(wanted)
            ).fetchall()
        else:
            rows = conn.execute("SELECT id FROM sessions ORDER BY kind, id").fetchall()

        BATCH = 25
        BATCH_PAUSE = 60
        try:
            for i, row in enumerate(rows):
                push_session(conn, sp, user_id, row[0])
                conn.commit()
                time.sleep(1.5)
                if (i + 1) % BATCH == 0 and i + 1 < len(rows):
                    print(f"\n  — batch pause {BATCH_PAUSE}s —\n")
                    time.sleep(BATCH_PAUSE)
        except Exception as e:
            conn.commit()
            print(f"\n⛔ {e}")
            sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detect")
    b = sub.add_parser("build"); b.add_argument("--id"); b.add_argument("--all", action="store_true")
    p = sub.add_parser("push"); p.add_argument("--id"); p.add_argument("--all", action="store_true"); p.add_argument("--ids-file")
    sub.add_parser("update-descriptions")
    args = ap.parse_args()
    {"detect": cmd_detect, "build": cmd_build, "push": cmd_push,
     "update-descriptions": cmd_update_descriptions}[args.cmd](args)


if __name__ == "__main__":
    main()
