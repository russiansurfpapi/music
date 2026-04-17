"""Daily listening suggestion: 3 things to listen to today.

Logic:
  1. RECENTLY TRENDING — look at tracks added in last 30 days; surface the subgenre
     that's spiked relative to baseline. Suggest its Focus playlist.
  2. ROOTS GAP — find a subgenre with strong canonical importance (from dj_glossary.yaml)
     where the user's library is thin (<30 tracks). Suggest its Focus playlist as homework.
  3. ONE 3% CHAIN — rotate through the 10 3% chains based on last_suggested_at
     (tracked in local state file).

Usage:
    python3 suggest.py
    python3 suggest.py --mark-played chain:chicago-house  # record that I listened
"""

import argparse
import json
import os
import random
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import yaml

from db import connect

_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(_HERE, "suggest_state.json")
GLOSSARY_PATH = os.path.join(_HERE, "dj_glossary.yaml")

# Canonically important subgenres that a DJ-adjacent listener should have ≥30 tracks of.
# Used for the ROOTS GAP suggestion.
CANONICAL_FOUNDATIONS = [
    "chicago house", "detroit techno", "acid house", "deep house",
    "italo disco", "dub", "dubstep (original)", "uk garage",
    "jungle/dnb", "boom bap", "electro", "footwork",
]


def _load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"played": {}}
    with open(STATE_PATH) as f:
        return json.load(f)


def _save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _trending(conn) -> Optional[Dict]:
    """Find a subgenre spiking in the last 30 days vs prior 90 days."""
    rows = conn.execute("""
        SELECT c.subgenre, t.added_at
        FROM tracks t
        JOIN classifications c ON c.spotify_id = t.spotify_id
        JOIN track_sources s ON s.spotify_id = t.spotify_id AND s.source='liked'
        WHERE c.subgenre IS NOT NULL AND t.added_at IS NOT NULL
    """).fetchall()
    now = datetime.now()
    cut_30 = now - timedelta(days=30)
    cut_120 = now - timedelta(days=120)

    recent: Counter = Counter()
    baseline: Counter = Counter()
    for r in rows:
        sub = r["subgenre"]
        try:
            added = datetime.fromisoformat(r["added_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            continue
        if added >= cut_30:
            recent[sub] += 1
        elif added >= cut_120:
            baseline[sub] += 1

    # Score = recent per day / baseline per day. Need at least 5 recent tracks.
    best = None
    best_score = 0.0
    for sub, rc in recent.items():
        if rc < 5:
            continue
        bc = baseline.get(sub, 0)
        recent_rate = rc / 30
        baseline_rate = (bc / 90) if bc > 0 else 0.01
        score = recent_rate / baseline_rate
        if score > best_score:
            best_score = score
            best = {"subgenre": sub, "recent_n": rc, "baseline_n": bc, "score": round(score, 2)}

    if not best:
        return None

    pl = conn.execute(
        "SELECT name, spotify_playlist_id FROM sessions WHERE id=?",
        (f"focus:{_slugish(best['subgenre'])}",),
    ).fetchone()
    if pl:
        best["playlist_name"] = pl["name"]
        best["playlist_id"] = pl["spotify_playlist_id"]
    return best


def _slugish(s: str) -> str:
    import re
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _roots_gap(conn) -> Optional[Dict]:
    """Find a canonical subgenre where library is thin."""
    gaps = []
    for sub in CANONICAL_FOUNDATIONS:
        r = conn.execute(
            "SELECT COUNT(*) FROM classifications WHERE lower(subgenre)=?",
            (sub.lower(),),
        ).fetchone()
        n = r[0] if r else 0
        if n < 30:
            gaps.append((sub, n))
    if not gaps:
        return None
    gaps.sort(key=lambda x: x[1])
    sub, n = gaps[0]
    pl = conn.execute(
        "SELECT name, spotify_playlist_id FROM sessions WHERE id=?",
        (f"focus:{_slugish(sub)}",),
    ).fetchone()
    return {
        "subgenre": sub, "library_count": n,
        "playlist_name": pl["name"] if pl else None,
        "playlist_id": pl["spotify_playlist_id"] if pl else None,
    }


def _chain_rotation(conn, state: dict) -> Optional[Dict]:
    """Pick a 3% chain that hasn't been played recently."""
    rows = conn.execute(
        "SELECT id, name, spotify_playlist_id FROM sessions WHERE kind='chain' ORDER BY id"
    ).fetchall()
    played = state.get("played", {})
    candidates = sorted(rows, key=lambda r: played.get(r["id"], ""))
    if not candidates:
        return None
    pick = candidates[0]
    return {
        "id": pick["id"],
        "name": pick["name"],
        "playlist_id": pick["spotify_playlist_id"],
        "last_played": played.get(pick["id"], "never"),
    }


def _glossary_blurb(subgenre: str) -> Optional[str]:
    if not os.path.exists(GLOSSARY_PATH):
        return None
    with open(GLOSSARY_PATH) as f:
        data = yaml.safe_load(f) or {}
    subs = data.get("subgenres", {})
    for k, v in subs.items():
        if (v.get("name") or "").lower() == subgenre.lower():
            markers = v.get("sonic_markers") or []
            if markers:
                return markers[0].strip().rstrip(".")
    return None


def cmd_suggest() -> None:
    with connect() as conn:
        state = _load_state()

        print("═" * 60)
        print("  TODAY — three things to listen to")
        print("═" * 60)

        trending = _trending(conn)
        if trending:
            print(f"\n▸ TRENDING — you've saved {trending['recent_n']} {trending['subgenre']} tracks in 30 days")
            print(f"  (baseline: {trending['baseline_n']} in prior 90 days — spike factor {trending['score']}×)")
            if trending.get("playlist_name"):
                blurb = _glossary_blurb(trending["subgenre"])
                print(f"  → Put on: {trending['playlist_name']}")
                if blurb:
                    print(f"    Listen for: {blurb}.")
        else:
            print("\n▸ TRENDING — no clear spike this month.")

        gap = _roots_gap(conn)
        if gap:
            print(f"\n▸ ROOTS GAP — your library has only {gap['library_count']} {gap['subgenre']} tracks")
            print(f"  This is a canonical foundation; thin here means missing context for everything downstream.")
            if gap.get("playlist_name"):
                blurb = _glossary_blurb(gap["subgenre"])
                print(f"  → Homework: {gap['playlist_name']}")
                if blurb:
                    print(f"    Listen for: {blurb}.")
        else:
            print("\n▸ ROOTS GAP — you have ≥30 tracks across all canonical foundations. Good.")

        chain = _chain_rotation(conn, state)
        if chain:
            print(f"\n▸ 3% CHAIN — rotation pick (last played: {chain['last_played']})")
            print(f"  → {chain['name']}")
            print(f"    Commit 25 minutes, headphones, start to finish.")
            print(f"    Mark done: python3 suggest.py --mark-played {chain['id']}")
        print()


def cmd_mark_played(session_id: str) -> None:
    state = _load_state()
    state.setdefault("played", {})[session_id] = datetime.now().isoformat(timespec="minutes")
    _save_state(state)
    print(f"Marked {session_id} played at {state['played'][session_id]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mark-played", help="Mark a session as played today")
    args = ap.parse_args()
    if args.mark_played:
        cmd_mark_played(args.mark_played)
    else:
        cmd_suggest()


if __name__ == "__main__":
    main()
