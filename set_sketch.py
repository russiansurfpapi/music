"""Set Sketcher — iterative set-building with library recommendations.

You give seed tracks (or pick a fork), it reads your classified library + the
transition atlas, and offers 4 "forks" per turn: PARALLEL / TEMPO_UP /
FAMILY_PIVOT / CURTAIN_DROP. Each fork includes 2-3 candidate tracks from your
own library + evidence from observed DJ transitions.

Usage:
  # Seed with 1-2 tracks; start a new sketch
  python3 set_sketch.py --seed "Pépé Bradock - Deep Burnt" --hours 3

  # Pick a fork + add a track to the sketch
  python3 set_sketch.py --pick B --track "Move D - Jus House"

  # Review current state + get next forks
  python3 set_sketch.py

  # Manually append a custom track (any subgenre)
  python3 set_sketch.py --pick custom --track "Herbert - I Hadn't Known"

  # Show the full sketched playlist so far
  python3 set_sketch.py --show-playlist

  # Reset
  python3 set_sketch.py --reset --hours 3 --seed "First track name"
"""

import argparse
import json
import os
import random
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from db import connect
from transition_atlas import bpm_mid, FAMILY, classify_move, MOVE_LABELS, SUBGENRE_BPM

STATE_FILE = ".sketch_state.json"


# ---------------------------------------------------------------------------
# Library lookups
# ---------------------------------------------------------------------------

def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def parse_seed_name(raw: str) -> Tuple[str, str]:
    """Split 'Artist - Title' into (artist, title). Tolerates 'Artist — Title' and 'Artist – Title'."""
    for sep in [" — ", " - ", " – "]:
        if sep in raw:
            a, t = raw.split(sep, 1)
            return a.strip(), t.strip()
    return raw.strip(), ""


_LIBRARY_CACHE = None


def _load_library_cache(conn):
    global _LIBRARY_CACHE
    if _LIBRARY_CACHE is None:
        rows = conn.execute("""
            SELECT t.spotify_id, t.artist, t.title, t.release_year,
                   c.genre, c.subgenre, c.rhythm, c.texture,
                   LOWER(t.artist) AS a_lower, LOWER(t.title) AS t_lower
            FROM tracks t
            LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
        """).fetchall()
        # Precompute normalized keys
        _LIBRARY_CACHE = []
        for r in rows:
            d = dict(r)
            d["_na"] = norm(r["artist"])
            d["_nt"] = norm(r["title"])
            _LIBRARY_CACHE.append(d)
    return _LIBRARY_CACHE


def lookup_library(conn, artist_q: str, title_q: str = "") -> Optional[Dict]:
    """Find a classified track in the library by fuzzy (artist, title) match.
    Uses normalized (accent-stripped, punctuation-stripped) comparison in Python."""
    na = norm(artist_q)
    nt = norm(title_q)
    if not na:
        return None
    lib = _load_library_cache(conn)
    best = None
    best_score = -1
    for r in lib:
        ra = r["_na"]
        rt = r["_nt"]
        score = 0
        if na and (na in ra or ra in na):
            score += 10
        if nt and (nt in rt or rt in nt):
            score += 15
        if nt and rt[:12] == nt[:12] and len(nt) >= 4:
            score += 5
        if score > best_score:
            best_score = score
            best = r
    if best_score < 10:
        return None
    # Strip private fields before returning
    result = {k: v for k, v in best.items() if not k.startswith("_")}
    return result


def recommend_from_library(conn, target_subgenre: str, exclude_ids: List[str],
                           prefer_texture: Optional[str] = None,
                           limit: int = 3) -> List[Dict]:
    """Pick candidate tracks from the library matching target subgenre.
    Light diversity: take top 30 matches, sample 3 with texture-preference weighting."""
    exclude_clause = ""
    exclude_params: List = []
    if exclude_ids:
        placeholders = ",".join("?" * len(exclude_ids))
        exclude_clause = f"AND t.spotify_id NOT IN ({placeholders})"
        exclude_params = exclude_ids

    # Prefer tracks that classify exactly to the target subgenre
    q = f"""
        SELECT t.spotify_id, t.artist, t.title, t.release_year,
               c.genre, c.subgenre, c.texture
        FROM tracks t
        JOIN classifications c ON c.spotify_id = t.spotify_id
        WHERE c.subgenre = ?
          AND t.artist != 'ID' AND t.artist != 'id'
          {exclude_clause}
        ORDER BY RANDOM() LIMIT 30
    """
    rows = [dict(r) for r in conn.execute(q, (target_subgenre, *exclude_params)).fetchall()]

    # Fallback: if empty, try genre-level match
    if not rows:
        q = f"""
            SELECT t.spotify_id, t.artist, t.title, t.release_year,
                   c.genre, c.subgenre, c.texture
            FROM tracks t
            JOIN classifications c ON c.spotify_id = t.spotify_id
            WHERE c.genre = ?
              AND t.artist != 'ID' AND t.artist != 'id'
              {exclude_clause}
            ORDER BY RANDOM() LIMIT 30
        """
        # target_subgenre might be a genre in this fallback
        rows = [dict(r) for r in conn.execute(q, (target_subgenre, *exclude_params)).fetchall()]

    # Score by texture preference (if given)
    def score(r):
        s = 0
        if prefer_texture and r["texture"] and prefer_texture in (r["texture"] or ""):
            s += 5
        if r["release_year"]:
            s += 1  # minor preference for tracks with known year
        return s

    rows.sort(key=lambda r: (-score(r), random.random()))
    return rows[:limit]


# ---------------------------------------------------------------------------
# Fork computation
# ---------------------------------------------------------------------------

FORK_TEMPLATES = [
    ("A", "PARALLEL",     "Stay in family, minimal BPM shift"),
    ("B", "TEMPO_UP",     "Add energy, cross into a faster territory"),
    ("C", "FAMILY_PIVOT", "Change sonic color at similar BPM — open a second lane"),
    ("D", "CURTAIN_DROP", "Intentional curveball — pop, rock, or ambient breather"),
]


def get_forks(conn, current_sg: str, position_frac: float,
              like_djs: Optional[List[str]] = None) -> List[Dict]:
    """Return 4 forks with target subgenres, based on observed transitions from the atlas.
    For each move type, pick the most-observed destination.
    If `like_djs` provided, restrict observed transitions to those DJs' sets."""
    dj_clause = ""
    params: List = [current_sg, current_sg]
    if like_djs:
        placeholders = ",".join("?" * len(like_djs))
        dj_clause = f" AND s.dj_slug IN ({placeholders})"
        params.extend(like_djs)
    rows = conn.execute(f"""
        SELECT prev.sg AS prev_sg, curr.sg AS next_sg,
               s.dj_slug, s.set_id, s.set_date,
               curr.position, s.track_count
        FROM (
            SELECT t.set_id, t.position,
                   COALESCE(c.subgenre, c.genre) AS sg
            FROM dj_set_tracks t
            LEFT JOIN classifications c ON c.spotify_id=t.spotify_id
        ) prev
        JOIN (
            SELECT t.set_id, t.position,
                   COALESCE(c.subgenre, c.genre) AS sg
            FROM dj_set_tracks t
            LEFT JOIN classifications c ON c.spotify_id=t.spotify_id
        ) curr ON prev.set_id = curr.set_id AND curr.position = prev.position + 1
        JOIN dj_sets s ON curr.set_id = s.set_id
        WHERE prev.sg = ? AND curr.sg IS NOT NULL AND curr.sg != ?{dj_clause}
    """, params).fetchall()

    # Group destinations by move type
    by_move: Dict[str, Dict[str, List]] = {k: {} for _, k, _ in FORK_TEMPLATES}
    for r in rows:
        mv = classify_move(current_sg, r["next_sg"])
        if mv not in by_move:
            continue
        by_move[mv].setdefault(r["next_sg"], []).append(dict(r))

    # For each move type, pick the TOP destination (most citations)
    forks = []
    for letter, mv, desc in FORK_TEMPLATES:
        dests = by_move.get(mv, {})
        if not dests:
            forks.append({
                "letter": letter, "move": mv, "desc": desc,
                "target_sg": None, "citations": [],
            })
            continue
        top = sorted(dests.items(), key=lambda kv: -len(kv[1]))[0]
        forks.append({
            "letter": letter, "move": mv, "desc": desc,
            "target_sg": top[0], "citations": top[1],
        })
    return forks


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_state() -> Dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: Dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def init_state(hours: float, seeds: List[Dict]) -> Dict:
    target_tracks = int(hours * 60 / 3.3)
    return {
        "started": datetime.now().isoformat(),
        "target_hours": hours,
        "target_tracks": target_tracks,
        "tracks": seeds,
    }


def current_position_frac(state: Dict) -> float:
    n = len(state.get("tracks", []))
    target = state.get("target_tracks", 50)
    return min(1.0, n / max(1, target))


def _current_plateau(tracks: List[Dict]) -> Tuple[Optional[str], int]:
    """Return (subgenre, consecutive count at end of sketch)."""
    if not tracks:
        return None, 0
    last_sg = tracks[-1].get("subgenre") or tracks[-1].get("genre")
    if not last_sg:
        return None, 0
    n = 0
    for t in reversed(tracks):
        sg = t.get("subgenre") or t.get("genre")
        if sg == last_sg:
            n += 1
        else:
            break
    return last_sg, n


def _current_family_run(tracks: List[Dict]) -> Tuple[Optional[str], int]:
    """Run of consecutive tracks in the same family at the end."""
    if not tracks:
        return None, 0
    def fam(t):
        sg = t.get("subgenre") or t.get("genre")
        return FAMILY.get(sg or "", None)
    last_fam = fam(tracks[-1])
    if not last_fam:
        return None, 0
    n = 0
    for t in reversed(tracks):
        if fam(t) == last_fam:
            n += 1
        else:
            break
    return last_fam, n


def analyze_state(state: Dict) -> List[Dict]:
    """Return advisory signals based on current sketch state.
    Each signal: {level: 'info'|'warn'|'nudge', text: str}"""
    tracks = state.get("tracks", [])
    signals: List[Dict] = []
    if len(tracks) < 2:
        return signals

    # Plateau at end
    last_sg, run = _current_plateau(tracks)
    if last_sg:
        if run == 1:
            pass  # no advisory — just changed
        elif run == 2:
            signals.append({"level": "info",
                "text": f"2 tracks in **{last_sg}** — normal plateau. Could hold or pivot."})
        elif run == 3:
            signals.append({"level": "nudge",
                "text": f"3 tracks in **{last_sg}** — most DJs would transition by now (median run across the library = 1 track). Consider a move."})
        elif run >= 4:
            signals.append({"level": "warn",
                "text": f"**{run} consecutive tracks in {last_sg}** — this is a long plateau. Only ~5% of observed DJ set-runs are this sustained. Transition strongly suggested unless you're doing a PLATEAU-shape set intentionally."})

    # Family run at end (crosses past subgenre changes within the same family)
    last_fam, fam_run = _current_family_run(tracks)
    if last_fam and fam_run >= 5 and fam_run > run + 1:
        signals.append({"level": "nudge",
            "text": f"**{fam_run} tracks in family `{last_fam}`** (crossing subgenre changes within-family). The room has been in one sonic territory — a family pivot would change the air."})

    # Subgenre dominance overall
    sub_counts: Dict[str, int] = {}
    for t in tracks:
        sg = t.get("subgenre") or t.get("genre")
        if sg:
            sub_counts[sg] = sub_counts.get(sg, 0) + 1
    if sub_counts:
        top_sg, top_n = max(sub_counts.items(), key=lambda kv: kv[1])
        share = top_n / len(tracks)
        if share > 0.6 and len(tracks) >= 8:
            signals.append({"level": "info",
                "text": f"{int(100*share)}% of your set is **{top_sg}** — that's PLATEAU-shape territory (think Gorgon City 61% deep house). If unintended, diversify next."})
        elif share > 0.4 and len(tracks) >= 12:
            signals.append({"level": "info",
                "text": f"{int(100*share)}% is **{top_sg}** — balanced toward one lane. DJ median at this point: 25-35% top-subgenre share."})

    # Progress awareness at key quintile boundaries
    frac = current_position_frac(state)
    n = len(tracks)
    target = state.get("target_tracks", 50)
    if 0.18 <= frac <= 0.22 and n == int(0.2 * target):
        signals.append({"level": "info",
            "text": "You're entering the **BUILD** quintile. This is where most sets introduce a second lane — a family pivot lands well here."})
    elif 0.38 <= frac <= 0.42 and n == int(0.4 * target):
        signals.append({"level": "info",
            "text": "You're entering **PEAK**. Across the dataset, electro and tech house surge 14-22% here. If you haven't tempo-upped yet, now's the moment."})
    elif 0.58 <= frac <= 0.62 and n == int(0.6 * target):
        signals.append({"level": "info",
            "text": "You're entering **PLATEAU**. This is the longest sustained-energy stretch. Anchor tracks / crowd-pleasers tend to cluster here."})
    elif 0.78 <= frac <= 0.82 and n == int(0.8 * target):
        signals.append({"level": "nudge",
            "text": "You're entering **CLOSE**. Across the dataset, deep house re-appears at 23% in the final quintile — the warm return-home move."})

    return signals


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_state(state: Dict):
    tracks = state.get("tracks", [])
    n = len(tracks)
    target = state.get("target_tracks", 0)
    frac = n / max(1, target)
    hrs = state.get("target_hours", "?")
    print(f"\n🎧 **Current sketch** — {n}/{target} tracks ({int(100*frac)}% through, target {hrs}h)\n")
    for i, t in enumerate(tracks, 1):
        sg = t.get("subgenre") or t.get("genre") or "?"
        tex = (t.get("texture") or "")[:40]
        print(f"  {i:2}. [{sg:20}] {t['artist']} — {t['title']}  {bpm_mid(sg) or '?'} BPM")
    if n:
        last = tracks[-1]
        cur_sg = last.get("subgenre") or last.get("genre")
        print(f"\n  → Current position: {cur_sg} ({bpm_mid(cur_sg) or '?'} BPM)")
        pos_lbl = "OPEN" if frac < 0.2 else "BUILD" if frac < 0.4 else "PEAK" if frac < 0.6 else "PLATEAU" if frac < 0.8 else "CLOSE"
        print(f"  → Quintile: {pos_lbl}")

        # Advisories based on state
        signals = analyze_state(state)
        if signals:
            print("\n  ADVISORIES:")
            for s in signals:
                marker = {"info": "·", "nudge": "⚠", "warn": "🚨"}.get(s["level"], "·")
                print(f"    {marker} {s['text']}")


def render_forks(conn, state: Dict, forks: List[Dict]):
    tracks = state.get("tracks", [])
    if not tracks:
        return
    current_sg = tracks[-1].get("subgenre") or tracks[-1].get("genre")
    b_curr = bpm_mid(current_sg)
    exclude_ids = [t.get("spotify_id") for t in tracks if t.get("spotify_id")]

    print("\n## Forks from here\n")
    for f in forks:
        if not f["target_sg"]:
            print(f"  [{f['letter']}] {f['move']} — (no observed moves for this type)")
            continue
        b_next = bpm_mid(f["target_sg"])
        delta = (b_next - b_curr) if (b_curr and b_next) else None
        delta_s = f"Δ{delta:+d} BPM" if delta is not None else ""
        n_citations = len(f["citations"])
        djs = sorted({c["dj_slug"] for c in f["citations"]})[:3]
        djs_str = ", ".join(djs) + (f" +{len(f['citations'])-3} more" if len(djs) < len({c['dj_slug'] for c in f['citations']}) else "")

        print(f"### [{f['letter']}] {f['move']} → **{f['target_sg']}** ({b_next or '??'} BPM, `{delta_s}`)")
        print(f"_{f['desc']}_")
        print(f"_{n_citations} observed transitions — e.g., {djs_str}_\n")

        # Candidates from library
        cands = recommend_from_library(conn, f["target_sg"], exclude_ids=exclude_ids, limit=3)
        if cands:
            print("**From your library:**")
            for c in cands:
                yr = f"({c['release_year']})" if c.get("release_year") else ""
                tex = (c.get("texture") or "").replace('"', '').replace("[", "").replace("]", "")[:30]
                print(f"  - `{c['artist']} — {c['title']}` {yr}  _{tex}_")
        else:
            print("  _(no matches in your library for this subgenre — try a custom pick)_")
        print()

        # One evidence citation
        if f["citations"]:
            ev = f["citations"][0]
            frac_ev = ev["position"] / max(1, ev["track_count"])
            print(f"**Evidence:** {ev['dj_slug']} @ {ev['set_date']} went to `{f['target_sg']}` at track #{ev['position']} ({int(100*frac_ev)}% through)")
        print()

    print(f"## How to pick\n")
    print(f"`python3 set_sketch.py --pick A --track \"Artist - Title\"`")
    print(f"(or `--pick custom --track \"...\"` to go off-menu)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def seed_sketch(conn, seeds_raw: List[str], hours: float) -> Dict:
    seeds = []
    for s in seeds_raw:
        a, t = parse_seed_name(s)
        m = lookup_library(conn, a, t)
        if m:
            seeds.append({
                "artist": m["artist"], "title": m["title"],
                "spotify_id": m["spotify_id"],
                "genre": m["genre"], "subgenre": m["subgenre"],
                "rhythm": m["rhythm"], "texture": m["texture"],
                "release_year": m["release_year"],
            })
            print(f"✓ Seeded: {m['artist']} — {m['title']}  [{m['subgenre'] or m['genre']}]")
        else:
            # Fallback: track not in classified library, still add it with unknown classification
            seeds.append({
                "artist": a, "title": t, "spotify_id": None,
                "genre": None, "subgenre": None,
                "rhythm": None, "texture": None,
                "release_year": None,
            })
            print(f"⚠ Seed not in classified library: {a} — {t}. Added as unclassified.")
    return init_state(hours, seeds)


def pick_and_add(conn, state: Dict, pick: str, track_raw: str) -> Dict:
    a, t = parse_seed_name(track_raw)
    m = lookup_library(conn, a, t)
    if m:
        new_entry = {
            "artist": m["artist"], "title": m["title"],
            "spotify_id": m["spotify_id"],
            "genre": m["genre"], "subgenre": m["subgenre"],
            "rhythm": m["rhythm"], "texture": m["texture"],
            "release_year": m["release_year"],
            "picked_fork": pick.upper(),
        }
    else:
        new_entry = {
            "artist": a, "title": t, "spotify_id": None,
            "genre": None, "subgenre": None, "rhythm": None, "texture": None,
            "release_year": None, "picked_fork": pick.upper(),
        }
    state["tracks"].append(new_entry)
    print(f"✓ Added (fork {pick.upper()}): {a} — {t}  [{new_entry.get('subgenre') or new_entry.get('genre') or '?'}]")
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", nargs="*", help="Seed track(s) (Artist - Title)")
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--pick", help="Fork letter (A/B/C/D/custom)")
    ap.add_argument("--track", help="Track to add with the pick")
    ap.add_argument("--reset", action="store_true", help="Start fresh")
    ap.add_argument("--show-playlist", action="store_true")
    ap.add_argument("--like", help="Comma-separated DJ slugs to bias forks (e.g., 'duke-dumont,mochakk')")
    args = ap.parse_args()

    state = {} if args.reset else load_state()

    with connect() as conn:
        # Initialize or append to state
        if args.seed:
            state = seed_sketch(conn, args.seed, args.hours)
            save_state(state)
        elif args.pick and args.track:
            if not state:
                print("No sketch in progress. Start with --seed first.")
                return
            state = pick_and_add(conn, state, args.pick, args.track)
            save_state(state)

        if args.show_playlist:
            render_state(state)
            return

        if not state.get("tracks"):
            ap.print_help()
            return

        # Show state + offer next forks
        render_state(state)
        current_sg = (state["tracks"][-1].get("subgenre")
                      or state["tracks"][-1].get("genre"))
        if not current_sg:
            print("\nLast track is unclassified — can't compute forks. Add another seed or track with a known classification.")
            return
        like_djs = [d.strip() for d in (args.like or "").split(",") if d.strip()] or None
        if like_djs:
            print(f"\n_Biased to DJs: {', '.join(like_djs)}_")
        forks = get_forks(conn, current_sg, current_position_frac(state),
                          like_djs=like_djs)
        render_forks(conn, state, forks)


if __name__ == "__main__":
    main()
