"""Transition Atlas — what macro moves DJs actually make.

No track-level BPM data (Spotify audio features blocked). Use per-subgenre BPM
band heuristics and observed transition frequencies across all sets to build a
move catalog.

Usage:
  python3 transition_atlas.py --from "deep house"        # all moves from this subgenre
  python3 transition_atlas.py --from "deep house" --dj mochakk   # DJ-specific
  python3 transition_atlas.py --from "deep house" --venue space-miami
  python3 transition_atlas.py --map                      # full matrix to markdown
"""

import argparse
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from db import connect


# Subgenre → (bpm_low, bpm_high). Used only for BPM-delta annotation, not classification.
SUBGENRE_BPM: Dict[str, Tuple[int, int]] = {
    # Ambient / downtempo
    "ambient": (60, 90), "ambient house": (100, 115), "ambient techno": (100, 115),
    "downtempo": (85, 105), "dub": (75, 100), "lo-fi": (80, 95),
    # House family — 115-128 territory
    "deep house": (115, 122), "soulful house": (115, 122), "funky house": (120, 126),
    "garage house": (120, 126), "jazz rap": (85, 100),
    "chicago house": (120, 128), "french house": (120, 128),
    "disco house": (118, 124), "tropical house": (100, 115),
    "afro house": (118, 125), "lo-fi house": (110, 120),
    "tech house": (122, 128), "jackin house": (122, 128),
    "tribal house": (120, 128), "bass house": (125, 130),
    "future house": (124, 128), "hard house": (130, 145),
    "progressive house": (126, 132), "melodic house & techno": (122, 128),
    "hip house": (105, 115), "house": (120, 126),
    # Techno
    "minimal techno": (125, 130), "microhouse": (120, 126),
    "melodic techno": (124, 130), "dub techno": (118, 125),
    "detroit techno": (128, 134), "industrial techno": (130, 140),
    "acid techno": (135, 145), "hard techno": (135, 150),
    "peak time techno": (130, 140), "techno": (128, 135),
    # Acid / electro
    "acid house": (120, 128), "electro": (120, 130),
    "italo disco": (115, 125), "cosmic disco": (100, 115), "nu-disco": (110, 120),
    "balearic": (100, 120), "eurodance": (130, 145),
    # Garage / bass / dnb / dubstep
    "uk garage": (130, 140), "speed garage": (130, 140), "two-step": (130, 140),
    "dubstep (original)": (138, 142), "post-dubstep": (135, 142), "brostep": (138, 145),
    "grime": (138, 142), "future bass": (130, 160),
    "jungle/dnb": (165, 175), "liquid dnb": (170, 175), "neurofunk": (170, 175),
    "jump up": (170, 175),
    # Trance
    "trance": (135, 142), "progressive trance": (132, 140),
    "psytrance": (138, 145), "goa trance": (145, 155),
    # Hip-hop family
    "trap": (140, 150), "drill": (140, 145), "boom bap": (80, 100),
    "instrumental hip hop": (75, 95), "conscious hip hop": (80, 95),
    "gangsta rap": (85, 100), "southern rap": (75, 95), "west coast rap": (85, 100),
    "uk rap": (85, 100), "underground hip hop": (80, 95),
    "cloud rap": (70, 90), "emo rap": (80, 95), "g-funk": (90, 100),
    "pop rap": (90, 105), "trap rap": (140, 150),
    # Latin / world / club
    "trap latino": (90, 105), "neoperreo": (100, 110), "dembow": (95, 110),
    "amapiano": (112, 118), "afrobeats": (100, 110), "moombahton": (108, 115),
    "funk carioca": (130, 160), "baltimore club": (128, 135), "jersey club": (130, 135),
    "ghettotech": (145, 160), "miami bass": (120, 140), "gqom": (115, 125),
    "ballroom": (130, 140),
    # Disco / pop / rock / other
    "footwork": (155, 165), "breakcore": (170, 180), "deconstructed club": (120, 150),
    "dream pop": (80, 110), "art pop": (80, 115), "bedroom pop": (80, 110),
    "hyperpop": (130, 150), "chillwave": (85, 105), "synth pop": (110, 125),
    "electropop": (118, 128), "indie dance": (120, 128),
    "neo-soul": (75, 95), "alternative rnb": (80, 100),
    # Genre-level fallbacks
    "disco-funk-soul": (110, 120), "jazz": (70, 120), "blues": (60, 100),
    "classical": (60, 120), "folk": (70, 110), "rock": (100, 140), "pop": (90, 130),
    "reggae": (70, 90), "world": (95, 125),
    "hip-hop": (85, 105), "reggaetón": (95, 110),
}

FAMILY: Dict[str, str] = {}
for sg in ["deep house", "tech house", "chicago house", "progressive house",
           "acid house", "afro house", "tropical house", "french house",
           "garage house", "soulful house", "funky house", "lo-fi house",
           "disco house", "jackin house", "ghetto house", "hip house",
           "house", "hard house", "tribal house", "ambient house",
           "outsider house", "bass house", "future house", "microhouse",
           "melodic house & techno"]:
    FAMILY[sg] = "house"
for sg in ["minimal techno", "detroit techno", "industrial techno", "hard techno",
           "dub techno", "acid techno", "melodic techno", "ambient techno",
           "peak time techno", "techno"]:
    FAMILY[sg] = "techno"
for sg in ["progressive trance", "psytrance", "goa trance", "trance"]:
    FAMILY[sg] = "trance"
for sg in ["uk garage", "speed garage", "two-step"]:
    FAMILY[sg] = "garage"
for sg in ["jungle/dnb", "liquid dnb", "neurofunk", "jump up"]:
    FAMILY[sg] = "dnb"
for sg in ["dubstep (original)", "post-dubstep", "brostep"]:
    FAMILY[sg] = "dubstep"
for sg in ["trap", "drill", "boom bap", "jazz rap", "instrumental hip hop",
           "conscious hip hop", "gangsta rap", "southern rap", "west coast rap",
           "uk rap", "underground hip hop", "cloud rap", "emo rap", "g-funk",
           "pop rap", "trap rap", "lo-fi"]:
    FAMILY[sg] = "hiphop"
for sg in ["italo disco", "nu-disco", "cosmic disco", "disco-funk-soul",
           "neo-soul", "alternative rnb"]:
    FAMILY[sg] = "disco-funk"
for sg in ["electro", "electropop", "indie dance", "synth pop", "eurodance"]:
    FAMILY[sg] = "electro-dance"
for sg in ["trap latino", "neoperreo", "dembow", "amapiano", "afrobeats",
           "moombahton", "funk carioca", "baltimore club", "jersey club",
           "ghettotech", "miami bass", "gqom", "ballroom", "reggaetón"]:
    FAMILY[sg] = "global-club"
for sg in ["dream pop", "art pop", "bedroom pop", "hyperpop", "chillwave",
           "pop", "rock"]:
    FAMILY[sg] = "pop-rock"
for sg in ["ambient", "downtempo", "dub", "reggae"]:
    FAMILY[sg] = "ambient-dub"
for sg in ["dubstep", "garage", "uk bass"]:
    FAMILY[sg] = "bass-uk"


def bpm_mid(sg: str) -> int:
    band = SUBGENRE_BPM.get(sg)
    return int((band[0] + band[1]) / 2) if band else None


def classify_move(prev: str, nxt: str) -> str:
    """Return one of: PARALLEL, TEMPO_UP, TEMPO_DOWN, FAMILY_PIVOT, CURTAIN_DROP."""
    if prev == nxt:
        return "SAME"
    f_prev = FAMILY.get(prev, "?")
    f_nxt = FAMILY.get(nxt, "?")
    b_prev = bpm_mid(prev)
    b_nxt = bpm_mid(nxt)
    delta = (b_nxt - b_prev) if (b_prev and b_nxt) else 0

    # Curtain drop: jump into pop/rock/jazz-style while prev is a dance family
    pop_rock_families = {"pop-rock", "ambient-dub"}
    dance_families = {"house", "techno", "trance", "electro-dance",
                      "bass-uk", "garage", "dnb", "dubstep", "global-club"}
    if f_prev in dance_families and f_nxt in pop_rock_families:
        return "CURTAIN_DROP"
    if f_prev in pop_rock_families and f_nxt in dance_families:
        return "CURTAIN_DROP"  # return from pop/ambient

    # Same family — parallel (or small tempo shift)
    if f_prev == f_nxt and f_prev != "?":
        if abs(delta) <= 3:
            return "PARALLEL"
        elif delta > 3:
            return "TEMPO_UP"
        else:
            return "TEMPO_DOWN"

    # Cross-family with big tempo delta
    if abs(delta) >= 10:
        return "TEMPO_UP" if delta > 0 else "TEMPO_DOWN"

    return "FAMILY_PIVOT"


MOVE_LABELS = {
    "SAME": "Stay in the lane",
    "PARALLEL": "Parallel shift (same family, ≤3 BPM swing)",
    "TEMPO_UP": "Tempo up (add energy)",
    "TEMPO_DOWN": "Tempo down (ease out)",
    "FAMILY_PIVOT": "Family pivot (change territory, similar energy)",
    "CURTAIN_DROP": "Curtain drop (curveball into pop/rock/ambient, or return)",
}

MOVE_ORDER = ["PARALLEL", "TEMPO_UP", "TEMPO_DOWN", "FAMILY_PIVOT", "CURTAIN_DROP", "SAME"]


def collect_transitions(conn, dj_filter=None, venue_filter=None):
    """Return list of (prev_subgenre, next_subgenre, dj_slug, set_id)."""
    q = """
        SELECT s.dj_slug, s.set_id, t.position,
               COALESCE(c.subgenre, c.genre) AS sg
        FROM dj_set_tracks t
        JOIN dj_sets s ON t.set_id = s.set_id
        LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
        WHERE c.genre IS NOT NULL
    """
    params = []
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
            continue  # skip same-as-prev (not a transition)
        transitions.append((prev, nxt, rows[i]["dj_slug"], rows[i]["set_id"]))
    return transitions


def report_moves_from(conn, start: str, dj=None, venue=None, top_n=15):
    all_trans = collect_transitions(conn, dj_filter=dj, venue_filter=venue)
    from_trans = [t for t in all_trans if t[0].lower() == start.lower()]
    if not from_trans:
        print(f"No transitions recorded from '{start}'"
              f"{' (dj=' + dj + ')' if dj else ''}"
              f"{' (venue=' + venue + ')' if venue else ''}.")
        print("Available starting points (top 15 by frequency):")
        c = Counter(t[0] for t in all_trans)
        for sg, n in c.most_common(15):
            print(f"  {sg} ({n})")
        return

    # Count destinations grouped by move type
    by_move: Dict[str, Counter] = defaultdict(Counter)
    by_move_djs: Dict[str, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for prev, nxt, dj_slug, sid in from_trans:
        mv = classify_move(prev, nxt)
        by_move[mv][nxt] += 1
        by_move_djs[mv][nxt].add(dj_slug)

    b_prev = bpm_mid(start)
    bpm_str = f"{b_prev} BPM" if b_prev else "?? BPM"
    scope = " overall"
    if dj:
        scope = f" (filter: DJ = {dj})"
    elif venue:
        scope = f" (filter: venue = {venue})"

    print(f"\n# Moves from `{start}` ({bpm_str}){scope}")
    print(f"_{len(from_trans)} observed transitions_\n")

    for mv in MOVE_ORDER:
        if not by_move[mv]:
            continue
        print(f"## {MOVE_LABELS[mv]}\n")
        tops = by_move[mv].most_common(top_n)
        for nxt, n in tops:
            b_nxt = bpm_mid(nxt)
            delta = (b_nxt - b_prev) if (b_prev and b_nxt) else None
            delta_s = f"Δ{delta:+d}" if delta is not None else "Δ??"
            djs = sorted(by_move_djs[mv][nxt])
            dj_sample = ", ".join(djs[:4])
            if len(djs) > 4:
                dj_sample += f" (+{len(djs)-4} more)"
            print(f"  **→ {nxt}** ({n}×) `{delta_s}` — {dj_sample}")
        print()


def build_full_map(conn, out_path: str):
    all_trans = collect_transitions(conn)
    # Group by starting subgenre
    by_start: Dict[str, List] = defaultdict(list)
    for t in all_trans:
        by_start[t[0]].append(t)

    popular = sorted(by_start.keys(), key=lambda s: -len(by_start[s]))

    lines = ["# Transition Atlas\n"]
    lines.append(f"Observed macro moves across {len(all_trans)} classified transitions.\n")
    lines.append("Each starting subgenre lists where DJs actually went, grouped by move type.\n")
    lines.append("BPM Δ is based on subgenre BPM-band midpoints.\n")
    lines.append("---\n")

    for start in popular[:30]:
        trans = by_start[start]
        if len(trans) < 5:
            continue
        lines.append(f"## Starting from `{start}` ({bpm_mid(start) or '??'} BPM) — "
                     f"{len(trans)} observed moves\n")
        by_move: Dict[str, Counter] = defaultdict(Counter)
        by_move_djs: Dict[str, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
        for prev, nxt, dj_slug, sid in trans:
            mv = classify_move(prev, nxt)
            by_move[mv][nxt] += 1
            by_move_djs[mv][nxt].add(dj_slug)
        for mv in MOVE_ORDER:
            if not by_move[mv]:
                continue
            lines.append(f"**{MOVE_LABELS[mv]}:**")
            b_prev = bpm_mid(start)
            tops = by_move[mv].most_common(8)
            for nxt, n in tops:
                b_nxt = bpm_mid(nxt)
                delta = (b_nxt - b_prev) if (b_prev and b_nxt) else None
                delta_s = f"Δ{delta:+d}" if delta is not None else "Δ??"
                djs = sorted(by_move_djs[mv][nxt])[:4]
                lines.append(f"  - → **{nxt}** ({n}×) `{delta_s}` — {', '.join(djs)}")
            lines.append("")
        lines.append("---\n")

    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Wrote {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", help="Starting subgenre")
    ap.add_argument("--dj", help="Filter by DJ slug")
    ap.add_argument("--venue", help="Filter by set_id substring (e.g., space-miami)")
    ap.add_argument("--map", action="store_true", help="Write full TRANSITION_ATLAS.md")
    ap.add_argument("--top", type=int, default=10, help="Top N per category")
    args = ap.parse_args()

    with connect() as conn:
        if args.map:
            build_full_map(conn, "TRANSITION_ATLAS.md")
            return
        if args.start:
            report_moves_from(conn, args.start, dj=args.dj, venue=args.venue, top_n=args.top)
            return
        ap.print_help()


if __name__ == "__main__":
    main()
