"""Set Sketcher web-app — iterative DJ set builder with fork recommendations.

Reuses set_sketch.py internals. Shared state in .sketch_state.json so the CLI
and web-app see the same sketch.

Run:
    python3 webapp/app.py
    # Open http://127.0.0.1:5000
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from flask import Flask, render_template, request, redirect, url_for, jsonify

# Ensure all modules resolve from repo root
os.chdir(ROOT)

from db import connect
from set_sketch import (
    load_state, save_state, init_state, parse_seed_name,
    lookup_library, recommend_from_library,
    get_forks, current_position_frac, FORK_TEMPLATES,
    analyze_state,
)
from transition_atlas import bpm_mid

app = Flask(__name__, template_folder=os.path.join(HERE, "templates"),
            static_folder=os.path.join(HERE, "static"))


import time as _time

def _quintile_label(frac: float) -> str:
    if frac < 0.2: return "OPEN"
    if frac < 0.4: return "BUILD"
    if frac < 0.6: return "PEAK"
    if frac < 0.8: return "PLATEAU"
    return "CLOSE"


def _is_live(state):
    """True if the most recent track has an `identified_at` timestamp within 60s —
    indicating live_listen.py is actively adding to this sketch."""
    tracks = state.get("tracks") or []
    if not tracks:
        return False
    last = tracks[-1].get("identified_at")
    if not last:
        return False
    try:
        return (_time.time() - float(last)) < 90
    except Exception:
        return False


def _enrich_forks(conn, state, like_djs=None):
    """Build fork-render data (target sg, citations, candidate tracks from library)."""
    tracks = state.get("tracks", [])
    if not tracks:
        return []
    current_sg = tracks[-1].get("subgenre") or tracks[-1].get("genre")
    if not current_sg:
        return []

    forks = get_forks(conn, current_sg, current_position_frac(state),
                      like_djs=like_djs)
    exclude_ids = [t.get("spotify_id") for t in tracks if t.get("spotify_id")]

    b_curr = bpm_mid(current_sg)
    enriched = []
    for f in forks:
        if not f["target_sg"]:
            enriched.append({
                "letter": f["letter"], "move": f["move"], "desc": f["desc"],
                "target_sg": None, "b_next": None, "delta": None,
                "citations": [], "candidates": [],
            })
            continue
        b_next = bpm_mid(f["target_sg"])
        delta = (b_next - b_curr) if (b_curr and b_next) else None
        djs_seen = sorted({c["dj_slug"] for c in f["citations"]})[:4]
        cands = recommend_from_library(conn, f["target_sg"],
                                       exclude_ids=exclude_ids, limit=4)
        enriched.append({
            "letter": f["letter"],
            "move": f["move"],
            "desc": f["desc"],
            "target_sg": f["target_sg"],
            "b_next": b_next,
            "delta": delta,
            "n_citations": len(f["citations"]),
            "djs": djs_seen,
            "candidates": cands,
        })
    return enriched


@app.route("/", methods=["GET"])
def index():
    state = load_state()
    # DJ filter persists via query param (?like=dj1,dj2)
    like_param = request.args.get("like", "").strip()
    like_djs = [d.strip() for d in like_param.split(",") if d.strip()] or None
    with connect() as conn:
        enriched = _enrich_forks(conn, state, like_djs=like_djs) if state.get("tracks") else []
        # Pull available DJs for the dropdown
        all_djs = [r["dj_slug"] for r in conn.execute(
            "SELECT dj_slug FROM dj_sets GROUP BY dj_slug HAVING COUNT(*) >= 2 ORDER BY dj_slug"
        ).fetchall()]
    n = len(state.get("tracks", []))
    target = state.get("target_tracks", 0)
    frac = n / max(1, target) if target else 0
    context = {
        "state": state,
        "tracks": state.get("tracks", []),
        "n": n,
        "target": target,
        "hours": state.get("target_hours", 3),
        "pct": int(100 * frac),
        "quintile": _quintile_label(frac) if state.get("tracks") else "—",
        "current_sg": (state.get("tracks") or [{}])[-1].get("subgenre")
                      or (state.get("tracks") or [{}])[-1].get("genre") if state.get("tracks") else None,
        "current_bpm": None,
        "forks": enriched,
        "like_param": like_param,
        "all_djs": all_djs,
        "signals": analyze_state(state),
        "live_mode": _is_live(state),
    }
    if context["current_sg"]:
        context["current_bpm"] = bpm_mid(context["current_sg"])
    return render_template("index.html", **context)


@app.route("/seed", methods=["POST"])
def seed():
    raw_seeds = request.form.getlist("seed")
    if not raw_seeds:
        text = request.form.get("seeds_text", "")
        raw_seeds = [s.strip() for s in text.splitlines() if s.strip()]
    hours = float(request.form.get("hours", 3))

    with connect() as conn:
        seeds = []
        for s in raw_seeds:
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
            else:
                seeds.append({
                    "artist": a, "title": t, "spotify_id": None,
                    "genre": None, "subgenre": None,
                    "rhythm": None, "texture": None, "release_year": None,
                })
    state = init_state(hours, seeds)
    save_state(state)
    return redirect(url_for("index"))


@app.route("/add", methods=["POST"])
def add_track():
    pick = request.form.get("pick", "custom").upper()
    artist = request.form.get("artist", "").strip()
    title = request.form.get("title", "").strip()
    spotify_id = request.form.get("spotify_id", "").strip() or None
    state = load_state()
    if not state:
        return redirect(url_for("index"))

    with connect() as conn:
        if spotify_id:
            # Look up full classification by spotify_id
            r = conn.execute("""
                SELECT t.spotify_id, t.artist, t.title, t.release_year,
                       c.genre, c.subgenre, c.rhythm, c.texture
                FROM tracks t
                LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
                WHERE t.spotify_id = ?
            """, (spotify_id,)).fetchone()
            if r:
                state["tracks"].append({
                    "artist": r["artist"], "title": r["title"],
                    "spotify_id": r["spotify_id"],
                    "genre": r["genre"], "subgenre": r["subgenre"],
                    "rhythm": r["rhythm"], "texture": r["texture"],
                    "release_year": r["release_year"],
                    "picked_fork": pick,
                })
            else:
                state["tracks"].append({
                    "artist": artist, "title": title, "spotify_id": spotify_id,
                    "picked_fork": pick,
                })
        else:
            # Fuzzy lookup for typed custom track
            m = lookup_library(conn, artist, title)
            if m:
                state["tracks"].append({
                    "artist": m["artist"], "title": m["title"],
                    "spotify_id": m["spotify_id"],
                    "genre": m["genre"], "subgenre": m["subgenre"],
                    "rhythm": m["rhythm"], "texture": m["texture"],
                    "release_year": m["release_year"],
                    "picked_fork": pick,
                })
            else:
                state["tracks"].append({
                    "artist": artist, "title": title, "spotify_id": None,
                    "genre": None, "subgenre": None, "rhythm": None, "texture": None,
                    "release_year": None, "picked_fork": pick,
                })
    save_state(state)
    return redirect(url_for("index"))


@app.route("/reset", methods=["POST"])
def reset():
    if os.path.exists(".sketch_state.json"):
        os.remove(".sketch_state.json")
    return redirect(url_for("index"))


@app.route("/undo", methods=["POST"])
def undo():
    state = load_state()
    if state and state.get("tracks"):
        state["tracks"].pop()
        save_state(state)
    return redirect(url_for("index"))


@app.route("/api/state", methods=["GET"])
def api_state():
    return jsonify(load_state())


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)
