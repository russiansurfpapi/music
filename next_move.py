"""Next-Move Advisor — given your current subgenre and position, recommend where
to go next, with direct citations of DJs who made the same move at similar
positions in their sets.

Two modes:

1) Fractional / track-index mode (original):
     python3 next_move.py --from "soulful house" --position 5 --hours 3
     python3 next_move.py --from "tech house" --pct 40

2) Template-aware mode (uses set_templates table built by extract_templates.py):
     python3 next_move.py --from "deep house" --minute 40 --set-length 90 --bpm 122
     python3 next_move.py --from "deep house" --minute 40 --set-length 90 --bpm 122 --energy 0.6
     python3 next_move.py --from "deep house" --like-template "967KNadI7no:peak" --bpm 122
"""

import argparse
import json
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from db import connect
from transition_atlas import (
    SUBGENRE_BPM, FAMILY, bpm_mid, classify_move, MOVE_LABELS
)
from extract_templates import load_template


# ---------------------------------------------------------------- role helpers

ROLE_BOUNDS: List[Tuple[str, float, float]] = [
    ("opener", 0.0, 0.2),
    ("warmup", 0.2, 0.5),
    ("peak",   0.5, 0.8),
    ("closer", 0.8, 1.0),
]


def role_for_fraction(frac: float) -> str:
    for name, s, e in ROLE_BOUNDS:
        if s <= frac < e:
            return name
    return "closer"


def position_label(frac: float) -> str:
    if frac < 0.2:   return "OPEN"
    if frac < 0.4:   return "BUILD"
    if frac < 0.6:   return "PEAK"
    if frac < 0.8:   return "PLATEAU"
    return "CLOSE"


# ----------------------------------------------- legacy (fractional) mode ----

def find_transitions(conn, from_sg: str, target_frac: float,
                     window: float = 0.15, dj_filter: str = None,
                     venue_filter: str = None):
    """Return transitions from `from_sg` at position fraction within +/-window of target_frac."""
    q = """
        SELECT t.set_id, t.position, t.raw_artist, t.raw_title,
               s.dj_slug, s.set_date, s.track_count,
               COALESCE(c.subgenre, c.genre) AS sg
        FROM dj_set_tracks t
        JOIN dj_sets s ON t.set_id = s.set_id
        LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
        WHERE 1=1
    """
    params: List = []
    if dj_filter:
        q += " AND s.dj_slug = ?"
        params.append(dj_filter)
    if venue_filter:
        q += " AND s.set_id LIKE ?"
        params.append(f"%{venue_filter}%")
    q += " ORDER BY s.set_id, t.position"
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]

    transitions = []
    for i in range(1, len(rows)):
        if rows[i]["set_id"] != rows[i-1]["set_id"]:
            continue
        prev = rows[i-1]["sg"]
        nxt = rows[i]["sg"]
        if not prev or not nxt or prev == nxt:
            continue
        if prev.lower() != from_sg.lower():
            continue
        n_total = rows[i]["track_count"] or len(rows)
        frac = rows[i]["position"] / max(1, n_total)
        if abs(frac - target_frac) > window:
            continue
        transitions.append({
            "prev_sg": prev,
            "next_sg": nxt,
            "dj_slug": rows[i]["dj_slug"],
            "set_id": rows[i]["set_id"],
            "set_date": rows[i]["set_date"],
            "track_count": n_total,
            "position": rows[i]["position"],
            "frac": frac,
            "prev_artist": rows[i-1]["raw_artist"],
            "prev_title": rows[i-1]["raw_title"],
            "next_artist": rows[i]["raw_artist"],
            "next_title": rows[i]["raw_title"],
        })
    return transitions


def why_for_move(mv: str, delta: Optional[int]) -> str:
    reasons = {
        "PARALLEL": "Same family, minimal BPM shift. Safe groove-stay that changes the mood without breaking flow.",
        "TEMPO_UP": "Faster target — adds energy. Works at BUILD/PEAK quintiles. Risky late in a set.",
        "TEMPO_DOWN": "Come-down move. Ease out of peak energy. Best at PLATEAU or CLOSE.",
        "FAMILY_PIVOT": "Cross-family at similar energy — changes sonic color without jarring the dancefloor. Often introduces a second lane you can return to.",
        "CURTAIN_DROP": "Intentional curveball into pop/rock/ambient. A breather or a signature statement. Use sparingly — landing hard, or as a reset.",
        "SAME": "No transition — you're staying in the lane.",
    }
    return reasons.get(mv, "")


def recommend(conn, from_sg: str, target_frac: float, window: float = 0.15,
              dj_filter: str = None, venue_filter: str = None, top_n: int = 5):
    trans = find_transitions(conn, from_sg, target_frac, window,
                             dj_filter=dj_filter, venue_filter=venue_filter)
    if not trans:
        broad = find_transitions(conn, from_sg, target_frac, window=0.5,
                                 dj_filter=dj_filter, venue_filter=venue_filter)
        if broad:
            print(f"_No moves observed at position {int(target_frac*100)}% +/-{int(window*100)}%. "
                  f"Falling back to all positions: {len(broad)} moves._\n")
            trans = broad
        else:
            print(f"No observed moves from `{from_sg}`. Try a different starting subgenre or loosen filters.")
            return

    by_dest: Dict[str, List] = defaultdict(list)
    for t in trans:
        by_dest[t["next_sg"]].append(t)

    ranked = sorted(by_dest.items(), key=lambda kv: -len(kv[1]))

    b_from = bpm_mid(from_sg)
    pos_lbl = position_label(target_frac)
    print(f"# Next-move advisor")
    print(f"**You're at:** `{from_sg}` ({b_from or '??'} BPM)  |  "
          f"**Position:** {int(100*target_frac)}% through the set ({pos_lbl})")
    if dj_filter or venue_filter:
        f = []
        if dj_filter:    f.append(f"DJ=**{dj_filter}**")
        if venue_filter: f.append(f"venue=**{venue_filter}**")
        print(f"**Filter:** {', '.join(f)}")
    print(f"\n{len(trans)} observed moves from here. Top {top_n} directions with evidence:\n")

    for rank, (dest, cites) in enumerate(ranked[:top_n], 1):
        mv = classify_move(from_sg, dest)
        b_dest = bpm_mid(dest)
        delta = (b_dest - b_from) if (b_from and b_dest) else None
        delta_s = f"D{delta:+d} BPM" if delta is not None else "D?? BPM"
        print(f"## #{rank} -> **{dest}** ({b_dest or '??'} BPM, `{delta_s}`) — {len(cites)} citations")
        print(f"**Move type:** _{MOVE_LABELS[mv]}_")
        print(f"**Why:** {why_for_move(mv, delta)}\n")
        print("**Evidence (specific DJs who made this move nearby):**")
        for c in cites[:3]:
            pct = int(100 * c["frac"])
            print(f"- **{c['dj_slug']}** @ {c['set_date']} "
                  f"(track #{c['position']} of {c['track_count']}, {pct}% through)  ")
            print(f"  `{c['prev_artist']} — {c['prev_title']}` -> "
                  f"`{c['next_artist']} — {c['next_title']}`")
        djs = sorted({c["dj_slug"] for c in cites})
        if len(djs) > 3:
            print(f"- Plus: {', '.join(djs[3:])}")
        print()

    if len(ranked) > top_n:
        tail = ", ".join(f"{k} ({len(v)})" for k, v in ranked[top_n:top_n+8])
        print(f"\n_Other observed moves (less frequent):_ {tail}")


def report_per_dj_panels(conn, start: str, target_frac: float, djs: List[str],
                         window: float = 0.25, per_dj_directions: int = 3):
    b_from = bpm_mid(start)
    pos_lbl = position_label(target_frac)
    print(f"# What these DJs do from `{start}` ({b_from or '??'} BPM)")
    print(f"**Position:** {int(100*target_frac)}% through a set ({pos_lbl})\n")

    for dj in djs:
        dj = dj.strip()
        trans = find_transitions(conn, start, target_frac, window,
                                 dj_filter=dj, venue_filter=None)
        if not trans:
            trans = find_transitions(conn, start, target_frac, window=0.5,
                                     dj_filter=dj, venue_filter=None)
        if not trans:
            print(f"## {dj}")
            print(f"_No observed transitions from `{start}` in their sets._\n")
            continue

        by_dest: Dict[str, List] = defaultdict(list)
        for t in trans:
            by_dest[t["next_sg"]].append(t)
        ranked = sorted(by_dest.items(), key=lambda kv: -len(kv[1]))[:per_dj_directions]

        print(f"## {dj}  _({len(trans)} observed moves from here)_\n")
        for dest, cites in ranked:
            mv = classify_move(start, dest)
            b_dest = bpm_mid(dest)
            delta = (b_dest - b_from) if (b_from and b_dest) else None
            delta_s = f"D{delta:+d}" if delta is not None else "D??"
            set_names = sorted({c["set_id"].replace(f"{dj}-", "", 1)[:40] for c in cites})[:3]
            set_str = " . ".join(set_names)
            print(f"- **-> {dest}** ({b_dest or '??'} BPM, `{delta_s}`) _{MOVE_LABELS[mv]}_")
            print(f"   cited: {set_str}")
        print()


# ================================================================ NEW MODE ==
# Template-aware recommender.

def _interp_curve(curve: List[List], t_sec: float) -> Optional[float]:
    """Linear interpolation over a bucketed curve of [[start_sec, value|None], ...].
    Nearest-non-null sample within one bucket width on either side is used when
    the hit bucket is null. Returns None if nothing nearby."""
    if not curve:
        return None
    # Clamp to curve range
    if t_sec <= curve[0][0]:
        idx = 0
    else:
        idx = len(curve) - 1
        for i, (s, _) in enumerate(curve):
            if s > t_sec:
                idx = max(0, i - 1)
                break
    v = curve[idx][1]
    if v is not None:
        return float(v)
    # Scan outward for nearest non-null bucket
    left = right = None
    for j in range(idx - 1, -1, -1):
        if curve[j][1] is not None:
            left = (curve[j][0], curve[j][1])
            break
    for j in range(idx + 1, len(curve)):
        if curve[j][1] is not None:
            right = (curve[j][0], curve[j][1])
            break
    if left and right:
        # Pick the closer one
        return float(left[1] if (t_sec - left[0]) <= (right[0] - t_sec) else right[1])
    if left:  return float(left[1])
    if right: return float(right[1])
    return None


def _ts_subgenre_at(tracks: List[Dict], t_sec: float) -> Optional[str]:
    """Return subgenre (fallback genre) of the track playing at t_sec within the set timeline."""
    current = None
    for t in tracks:
        ts = t.get("timestamp_sec")
        if ts is None:
            continue
        if ts <= t_sec:
            current = t
        else:
            break
    if not current:
        return None
    return current.get("subgenre") or current.get("genre")


def load_fingerprinted_tracks(conn, set_id: str) -> List[Dict]:
    rows = conn.execute(
        """SELECT t.set_id, t.position, t.timestamp_sec, t.spotify_id,
                  tr.tempo, c.genre, c.subgenre, c.texture,
                  t.raw_artist, t.raw_title
           FROM dj_set_tracks t
           LEFT JOIN tracks tr ON tr.spotify_id = t.spotify_id
           LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
           WHERE t.set_id = ? AND t.timestamp_sec IS NOT NULL
           ORDER BY t.timestamp_sec""",
        (set_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _bpm_at_track(track: Dict, fallback_curve: List[List]) -> Optional[float]:
    """Prefer the track's own tempo; fall back to the BPM curve; else subgenre midpoint."""
    if track.get("tempo"):
        return float(track["tempo"])
    sg = track.get("subgenre") or track.get("genre")
    mid = bpm_mid(sg) if sg else None
    if mid is not None:
        return float(mid)
    if track.get("timestamp_sec") is not None and fallback_curve:
        v = _interp_curve(fallback_curve, track["timestamp_sec"])
        if v is not None:
            return float(v)
    return None


def eligible_templates(conn, role: Optional[str],
                       like_template: Optional[str]) -> List[Dict]:
    """Return set_templates rows (parsed) matching the role, or the like-template
    plus neighbors from the same DJ."""
    where = []
    params: List = []
    if like_template:
        # Load anchor + all templates from same DJ (any role within that DJ's sets).
        anchor = load_template(like_template)
        if not anchor:
            return []
        where.append("dj_slug = ?")
        params.append(anchor["dj_slug"])
        # Restrict to same-role neighbors for tighter match (plus anchor's role).
        if role:
            where.append("role = ?")
            params.append(role)
        else:
            where.append("role = ?")
            params.append(anchor["role"])
    else:
        if role:
            where.append("role = ?")
            params.append(role)

    q = "SELECT * FROM set_templates"
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY dj_slug, source_set_id, role"
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    for r in rows:
        for field in ("bpm_curve", "energy_curve", "genre_density"):
            if r.get(field):
                try:
                    r[field] = json.loads(r[field])
                except Exception:
                    r[field] = None
    return rows


def scan_template_transitions(conn, templates: List[Dict], from_sg: str,
                              t_sec_target: Optional[float],
                              minute_window_sec: int,
                              bpm_current: Optional[float],
                              bpm_tol: int,
                              energy_current: Optional[float]
                              ) -> List[Dict]:
    """For each eligible template, walk the source set's track timeline looking
    for transitions from `from_sg` -> anything else, with the FROM track playing
    at absolute time within minute_window_sec of t_sec_target (or anywhere in
    the role window if t_sec_target is None).

    Returns a list of citation dicts with BPM / energy deltas from the curves.
    """
    citations: List[Dict] = []
    from_lc = (from_sg or "").lower()

    # Cache tracks per set_id so we don't re-query for every role of the same set.
    tracks_cache: Dict[str, List[Dict]] = {}
    # Cache set meta
    meta_cache: Dict[str, Dict] = {}

    def _meta(sid: str) -> Dict:
        if sid in meta_cache:
            return meta_cache[sid]
        row = conn.execute(
            "SELECT set_id, dj_slug, title, set_date, duration_sec FROM dj_sets WHERE set_id = ?",
            (sid,),
        ).fetchone()
        meta_cache[sid] = dict(row) if row else {}
        return meta_cache[sid]

    for tpl in templates:
        sid = tpl["source_set_id"]
        if sid not in tracks_cache:
            tracks_cache[sid] = load_fingerprinted_tracks(conn, sid)
        tracks = tracks_cache[sid]
        if len(tracks) < 2:
            continue
        start_sec = tpl["start_sec"]
        end_sec = tpl["end_sec"]
        bpm_curve = tpl.get("bpm_curve") or []
        energy_curve = tpl.get("energy_curve") or []

        for i in range(1, len(tracks)):
            prev = tracks[i - 1]
            nxt = tracks[i]
            prev_sg = (prev.get("subgenre") or prev.get("genre") or "")
            nxt_sg = (nxt.get("subgenre") or nxt.get("genre") or "")
            if not prev_sg or not nxt_sg or prev_sg == nxt_sg:
                continue
            if prev_sg.lower() != from_lc:
                continue
            transition_ts = nxt.get("timestamp_sec")
            if transition_ts is None:
                continue
            # Must fall within this template's role window.
            if not (start_sec <= transition_ts < end_sec):
                continue
            # Minute-position filter (if specified).
            if t_sec_target is not None:
                if abs(transition_ts - t_sec_target) > minute_window_sec:
                    continue

            prev_bpm = _bpm_at_track(prev, bpm_curve)
            nxt_bpm = _bpm_at_track(nxt, bpm_curve)

            # BPM compatibility filter against the user's current BPM.
            if bpm_current is not None and prev_bpm is not None:
                if abs(prev_bpm - bpm_current) > bpm_tol:
                    continue

            prev_energy = _interp_curve(energy_curve, prev.get("timestamp_sec") or transition_ts)
            nxt_energy = _interp_curve(energy_curve, transition_ts)

            m = _meta(sid)
            citations.append({
                "template_id": tpl["template_id"],
                "set_id": sid,
                "dj_slug": tpl["dj_slug"],
                "role": tpl["role"],
                "set_title": m.get("title") or sid,
                "set_date": m.get("set_date"),
                "set_duration": m.get("duration_sec") or end_sec,
                "venue_hint": tpl.get("venue_hint"),
                "prev_sg": prev_sg,
                "next_sg": nxt_sg,
                "prev_bpm": prev_bpm,
                "next_bpm": nxt_bpm,
                "prev_energy": prev_energy,
                "next_energy": nxt_energy,
                "transition_sec": transition_ts,
                "transition_min": round(transition_ts / 60.0, 1),
                "prev_artist": prev.get("raw_artist"),
                "prev_title": prev.get("raw_title"),
                "next_artist": nxt.get("raw_artist"),
                "next_title": nxt.get("raw_title"),
                "energy_user_delta": (
                    (nxt_energy - energy_current)
                    if (energy_current is not None and nxt_energy is not None)
                    else None
                ),
            })
    return citations


def score_destination(cites: List[Dict], bpm_current: Optional[float],
                      energy_current: Optional[float]) -> float:
    """Rank destinations by historical frequency + BPM compatibility + energy-trend continuation."""
    if not cites:
        return 0.0
    freq = len(cites)
    bpm_scores: List[float] = []
    energy_scores: List[float] = []
    for c in cites:
        if bpm_current is not None and c.get("prev_bpm") is not None:
            d = abs(c["prev_bpm"] - bpm_current)
            bpm_scores.append(max(0.0, 1.0 - d / 12.0))  # 0 at 12 BPM away
        if energy_current is not None and c.get("prev_energy") is not None and c.get("next_energy") is not None:
            local_trend = c["next_energy"] - c["prev_energy"]  # what this DJ's move did
            # Prefer moves that continue the user's direction: if user is rising
            # (energy > 0.5-ish), reward upward moves; if falling, reward downward.
            # Simple heuristic: reward same sign as (energy_current - 0.5) scaled.
            user_dir = energy_current - 0.5
            align = 1.0 if (user_dir * local_trend) >= 0 else 0.3
            energy_scores.append(align)
    bpm_avg = sum(bpm_scores) / len(bpm_scores) if bpm_scores else 0.5
    energy_avg = sum(energy_scores) / len(energy_scores) if energy_scores else 0.5
    # Log-scaled frequency so one DJ citing 40 times doesn't dominate 5 different DJs
    import math
    freq_score = math.log1p(freq)
    return freq_score * (0.6 + 0.25 * bpm_avg + 0.15 * energy_avg)


def recommend_template_aware(conn, from_sg: str, minute: Optional[float],
                             set_length_min: Optional[float],
                             bpm_current: Optional[float],
                             energy_current: Optional[float],
                             like_template: Optional[str],
                             top_n: int = 5,
                             minute_window: int = 10,
                             bpm_tol: int = 8):
    """Template-aware recommendation."""
    # Derive role + target second-in-set.
    role: Optional[str] = None
    t_sec_target: Optional[float] = None
    frac: Optional[float] = None
    if like_template:
        anchor = load_template(like_template)
        if not anchor:
            print(f"No template found with id `{like_template}`.")
            return
        role = anchor["role"]
        if minute is not None:
            t_sec_target = minute * 60.0
        # If no minute given but we have anchor, target the anchor's midpoint.
        if t_sec_target is None:
            t_sec_target = (anchor["start_sec"] + anchor["end_sec"]) / 2.0
        if set_length_min:
            frac = (minute * 60.0) / (set_length_min * 60.0) if minute is not None else None
    else:
        if minute is None or set_length_min is None:
            print("Template-aware mode requires --minute + --set-length, or --like-template.")
            return
        frac = minute / set_length_min
        frac = max(0.0, min(1.0, frac))
        role = role_for_fraction(frac)
        t_sec_target = minute * 60.0

    # Pull eligible templates.
    templates = eligible_templates(conn, role, like_template)
    if not templates:
        print(f"No templates found for role=`{role}`"
              + (f", like_template=`{like_template}`" if like_template else "")
              + ".")
        return

    # Walk transitions.
    # Minute window: scale by set length if available (longer sets -> wider absolute window).
    mw_sec = minute_window * 60
    citations = scan_template_transitions(
        conn, templates, from_sg, t_sec_target,
        minute_window_sec=mw_sec, bpm_current=bpm_current,
        bpm_tol=bpm_tol, energy_current=energy_current,
    )

    if not citations:
        # Widen minute window first, then BPM, then drop position constraint.
        print(f"_No template transitions from `{from_sg}` at minute {minute} "
              f"+/-{minute_window}m (BPM +/-{bpm_tol}). Widening search..._\n")
        citations = scan_template_transitions(
            conn, templates, from_sg, t_sec_target,
            minute_window_sec=mw_sec * 2, bpm_current=bpm_current,
            bpm_tol=bpm_tol * 2, energy_current=energy_current,
        )
    if not citations:
        citations = scan_template_transitions(
            conn, templates, from_sg, None,
            minute_window_sec=10**9, bpm_current=None,
            bpm_tol=10**6, energy_current=energy_current,
        )
    if not citations:
        print(f"No observed template transitions from `{from_sg}` in role `{role}`.")
        return

    # Group by destination subgenre, score.
    by_dest: Dict[str, List[Dict]] = defaultdict(list)
    for c in citations:
        by_dest[c["next_sg"]].append(c)
    ranked = sorted(
        by_dest.items(),
        key=lambda kv: -score_destination(kv[1], bpm_current, energy_current),
    )

    # Header.
    b_from = bpm_mid(from_sg)
    print(f"# Next-move advisor (template-aware)")
    pos_bits = []
    if minute is not None and set_length_min:
        pos_bits.append(f"minute {minute:.0f} of {set_length_min:.0f} ({int(100*frac)}% - role **{role}**)")
    elif minute is not None:
        pos_bits.append(f"minute {minute:.0f} (role **{role}**)")
    else:
        pos_bits.append(f"role **{role}**")
    if like_template:
        pos_bits.append(f"like-template=`{like_template}`")
    print(f"**You're at:** `{from_sg}` ({b_from or '??'} BPM) | " + " | ".join(pos_bits))
    if bpm_current is not None:
        print(f"**Current BPM:** {bpm_current:.1f} (matching within +/-{bpm_tol})")
    if energy_current is not None:
        print(f"**Current energy:** {energy_current:.2f}")
    print(f"\nScanned {len(templates)} templates, found {len(citations)} matching transitions. "
          f"Top {top_n} directions:\n")

    for rank, (dest, cites) in enumerate(ranked[:top_n], 1):
        mv = classify_move(from_sg, dest)
        b_dest = bpm_mid(dest)
        delta = (b_dest - b_from) if (b_from and b_dest) else None
        delta_s = f"D{delta:+d} BPM" if delta is not None else "D?? BPM"
        djs = sorted({c["dj_slug"] for c in cites})
        print(f"## #{rank} -> **{dest}** ({b_dest or '??'} BPM, `{delta_s}`) — "
              f"{len(cites)} transitions, {len(djs)} DJs")
        print(f"**Move type:** _{MOVE_LABELS[mv]}_")
        print(f"**Why:** {why_for_move(mv, delta)}\n")
        print("**Evidence:**")
        # Rank citations by proximity to target minute (then BPM proximity).
        def _cite_sort_key(c):
            d_min = abs(c["transition_sec"] - t_sec_target) if t_sec_target is not None else 0
            d_bpm = abs((c["prev_bpm"] or 0) - bpm_current) if bpm_current is not None and c["prev_bpm"] is not None else 0
            return (d_min, d_bpm)
        for c in sorted(cites, key=_cite_sort_key)[:3]:
            pb = f"{c['prev_bpm']:.0f}" if c.get("prev_bpm") is not None else "??"
            nb = f"{c['next_bpm']:.0f}" if c.get("next_bpm") is not None else "??"
            pe = f"{c['prev_energy']:.2f}" if c.get("prev_energy") is not None else "??"
            ne = f"{c['next_energy']:.2f}" if c.get("next_energy") is not None else "??"
            title_short = (c["set_title"] or c["set_id"])[:60]
            print(f"- **{c['dj_slug']}** at minute {c['transition_min']:.1f} "
                  f"of '{title_short}' went `{c['prev_sg']}` -> `{c['next_sg']}`, "
                  f"BPM {pb} -> {nb}, energy {pe} -> {ne}")
            print(f"  `{c['prev_artist']} — {c['prev_title']}` -> "
                  f"`{c['next_artist']} — {c['next_title']}`  "
                  f"(template=`{c['template_id']}`)")
        if len(djs) > 3:
            print(f"- Plus DJs: {', '.join(djs[3:])}")
        print()

    if len(ranked) > top_n:
        tail = ", ".join(f"{k} ({len(v)})" for k, v in ranked[top_n:top_n+8])
        print(f"\n_Other observed destinations:_ {tail}")


# ------------------------------------------------------------------------ cli

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True,
                    help="Current subgenre (e.g., 'soulful house')")
    # Legacy positioning
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--position", type=float,
                       help="Current track number (int) OR fractional position 0-1 (legacy mode)")
    group.add_argument("--pct", type=float, help="Percent through the set (0-100) (legacy mode)")
    ap.add_argument("--hours", type=float, default=3.0,
                    help="Planned set length in hours (used with --position)")
    ap.add_argument("--window", type=float, default=15,
                    help="Position match window in percent (legacy mode, default 15%%)")
    # Template-aware positioning
    ap.add_argument("--minute", type=float,
                    help="Current position in minutes (real time; triggers template-aware mode)")
    ap.add_argument("--set-length", dest="set_length", type=float,
                    help="Total set length in minutes (used with --minute)")
    ap.add_argument("--bpm", type=float, help="Current BPM (weights recommendations)")
    ap.add_argument("--energy", type=float, help="Current energy 0-1 (weights recommendations)")
    ap.add_argument("--like-template", dest="like_template",
                    help="Steer toward a set_templates.template_id shape")
    ap.add_argument("--minute-window", dest="minute_window", type=int, default=10,
                    help="Minute-proximity window for template mode (default 10)")
    ap.add_argument("--bpm-tol", dest="bpm_tol", type=int, default=8,
                    help="BPM compatibility tolerance for template mode (default 8)")
    # Filters / output
    ap.add_argument("--like", dest="like_dj", help="Filter by single DJ slug (legacy mode)")
    ap.add_argument("--per-dj", dest="per_dj",
                    help="Comma-separated DJ slugs (legacy mode) — per-DJ panels")
    ap.add_argument("--directions", type=int, default=3,
                    help="How many top directions per DJ (legacy, used with --per-dj)")
    ap.add_argument("--venue", help="Filter by set_id substring (legacy mode)")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    template_mode = (args.minute is not None) or (args.like_template is not None)

    with connect() as conn:
        if template_mode:
            recommend_template_aware(
                conn,
                from_sg=args.start,
                minute=args.minute,
                set_length_min=args.set_length,
                bpm_current=args.bpm,
                energy_current=args.energy,
                like_template=args.like_template,
                top_n=args.top,
                minute_window=args.minute_window,
                bpm_tol=args.bpm_tol,
            )
            return

        # Legacy mode
        if args.pct is not None:
            target_frac = args.pct / 100
        elif args.position is not None:
            # If position is a fraction (0..1), use directly. Else treat as track index.
            if 0.0 < args.position <= 1.0:
                target_frac = args.position
            else:
                total_tracks = int(args.hours * 60 / 3.3)
                target_frac = args.position / max(1, total_tracks)
        else:
            target_frac = 0.25

        if args.per_dj:
            djs = [d.strip() for d in args.per_dj.split(",") if d.strip()]
            report_per_dj_panels(conn, args.start, target_frac, djs,
                                 window=args.window/100,
                                 per_dj_directions=args.directions)
        else:
            recommend(conn, args.start, target_frac, window=args.window/100,
                      dj_filter=args.like_dj, venue_filter=args.venue,
                      top_n=args.top)


if __name__ == "__main__":
    main()
