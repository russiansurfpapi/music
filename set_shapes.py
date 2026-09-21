"""Set shape analysis — macro patterns and transition pace for DJ sets.

Every set gets a compact text fingerprint showing:
  - Timeline strip (one char per track, color-coded by subgenre family)
  - Transition density (how often the subgenre changes)
  - Longest plateau (longest run in one subgenre)
  - Shape archetype (PLATEAU / RAMP / WAVE / ECLECTIC)
  - Curveballs (tracks far from the DJ's usual palette)

Usage:
  python3 set_shapes.py --set <set_id>               # single set breakdown
  python3 set_shapes.py --dj <dj_slug>               # all sets for a DJ
  python3 set_shapes.py --venue space-miami          # all sets matching a venue substring
  python3 set_shapes.py --all-venues                 # summary across key venues
"""

import argparse
import statistics
from collections import Counter, defaultdict
from typing import Dict, List

from db import connect


# Subgenre → 1-char code for the timeline strip. Grouped by family so related
# subgenres share a nearby code (easy to scan visually).
#
# Family codes: lowercase = the subgenre's FAMILY label, so a reader can scan for
# structure like "dh dh dh th mt mt" without memorizing every mapping.
FAM: Dict[str, str] = {
    # House family (lowercase)
    "deep house": "d", "tech house": "t", "minimal techno": "m", "microhouse": "m",
    "acid house": "a", "chicago house": "c", "progressive house": "p",
    "afro house": "f", "tropical house": "f", "french house": "r",
    "garage house": "g", "soulful house": "s", "funky house": "u",
    "lo-fi house": "l", "disco house": "d", "jackin house": "j",
    "ghetto house": "g", "hip house": "h", "house": "H", "hard house": "H",
    "tribal house": "f", "ambient house": "b", "outsider house": "o",
    "bass house": "b", "future house": "F",
    # Techno
    "detroit techno": "M", "industrial techno": "I", "hard techno": "I",
    "dub techno": "B", "acid techno": "A", "melodic techno": "M",
    "ambient techno": "B", "peak time techno": "M", "techno": "M",
    # Electro / disco-dance
    "electro": "e", "italo disco": "i", "nu-disco": "n", "cosmic disco": "n",
    "balearic": "b", "eurodance": "E",
    # Trance
    "progressive trance": "T", "psytrance": "T", "goa trance": "T", "trance": "T",
    # Garage / dubstep / dnb
    "uk garage": "G", "speed garage": "G", "two-step": "G",
    "dubstep (original)": "S", "post-dubstep": "S", "brostep": "S",
    "grime": "Q", "future bass": "Q",
    "jungle/dnb": "J", "liquid dnb": "J", "neurofunk": "J", "jump up": "J",
    # Hip-hop family
    "trap": "#", "boom bap": "#", "lo-fi": "#", "jazz rap": "#",
    "instrumental hip hop": "#", "conscious hip hop": "#", "gangsta rap": "#",
    "southern rap": "#", "west coast rap": "#", "uk rap": "#",
    "underground hip hop": "#", "cloud rap": "#", "emo rap": "#",
    "drill": "#", "g-funk": "#", "pop rap": "#", "trap rap": "#",
    # Latin / world
    "trap latino": "%", "neoperreo": "%", "dembow": "%", "amapiano": "%",
    "afrobeats": "%", "moombahton": "%", "funk carioca": "%",
    "baltimore club": "%", "jersey club": "%", "ghettotech": "%",
    "miami bass": "%", "gqom": "%", "ballroom": "%",
    # Other
    "footwork": "f", "breakcore": "X", "deconstructed club": "X",
    "dub": "~", "neo-soul": "^", "alternative rnb": "^",
    "dream pop": "o", "art pop": "o", "bedroom pop": "o", "hyperpop": "o",
    "chillwave": "o", "synth pop": "y", "electropop": "y", "indie dance": "y",
}

# Genre-level fallback (when subgenre is null but genre exists)
GEN: Dict[str, str] = {
    "house": "H", "techno": "M", "trance": "T", "disco-funk-soul": "$",
    "hip-hop": "#", "reggaetón": "%", "uk bass": "G", "dnb": "J",
    "dubstep": "S", "garage": "G", "reggae": "~", "dub": "~",
    "rock": "R", "pop": "y", "ambient": "z", "idm": "X", "downtempo": "w",
    "jazz": "q", "blues": "q", "folk": "q", "classical": "q",
    "world": "%", "breaks": "J", "electro": "e",
}

UNCLASSIFIED = "."

# Subgenre → family label (for pattern recognition)
FAMILY: Dict[str, str] = {}
for sg in ["deep house", "tech house", "chicago house", "progressive house",
           "acid house", "afro house", "tropical house", "french house",
           "garage house", "soulful house", "funky house", "lo-fi house",
           "disco house", "jackin house", "ghetto house", "hip house",
           "house", "hard house", "tribal house", "ambient house",
           "outsider house", "bass house", "future house", "microhouse"]:
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
for sg in ["trap", "boom bap", "lo-fi", "jazz rap", "instrumental hip hop",
           "conscious hip hop", "gangsta rap", "southern rap", "west coast rap",
           "uk rap", "underground hip hop", "cloud rap", "emo rap", "drill",
           "g-funk", "pop rap", "trap rap"]:
    FAMILY[sg] = "hiphop"


def code_for(subgenre: str, genre: str) -> str:
    if subgenre and subgenre in FAM:
        return FAM[subgenre]
    if genre and genre in GEN:
        return GEN[genre]
    return UNCLASSIFIED


def fetch_set(conn, set_id: str) -> Dict:
    meta = conn.execute("""
        SELECT set_id, dj_slug, set_date, track_count FROM dj_sets WHERE set_id = ?
    """, (set_id,)).fetchone()
    if not meta:
        return None
    rows = conn.execute("""
        SELECT t.position, t.raw_artist, t.raw_title, c.genre, c.subgenre, c.rhythm
        FROM dj_set_tracks t
        LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
        WHERE t.set_id = ? ORDER BY t.position
    """, (set_id,)).fetchall()
    return {"meta": dict(meta), "tracks": [dict(r) for r in rows]}


def analyze_set(sdata: Dict) -> Dict:
    tracks = sdata["tracks"]
    n = len(tracks)
    subs = [t["subgenre"] or t["genre"] or None for t in tracks]
    strip = "".join(code_for(t["subgenre"], t["genre"]) for t in tracks)

    # Transitions (subgenre changes track-to-track)
    transitions = sum(1 for i in range(1, n) if subs[i] != subs[i-1] and subs[i] and subs[i-1])
    transition_rate = transitions / (n - 1) if n > 1 else 0

    # Run lengths
    runs = []
    cur, ln = None, 0
    for s in subs:
        if s == cur:
            ln += 1
        else:
            if cur is not None:
                runs.append(ln)
            cur, ln = s, 1
    if cur is not None:
        runs.append(ln)
    max_run = max(runs) if runs else 0
    median_run = int(statistics.median(runs)) if runs else 0
    # Ignore None runs when computing the dominant-run metric
    valid_runs = [r for s, r in zip(subs, runs) if s]

    # Family transitions (harder to make — crossing family lines)
    fam_seq = [FAMILY.get(s or "", (s or "?")[:3]) if s else None for s in subs]
    family_transitions = sum(1 for i in range(1, n)
                              if fam_seq[i] != fam_seq[i-1] and fam_seq[i] and fam_seq[i-1])

    # Classify macro shape
    sub_c = Counter(s for s in subs if s)
    total = sum(sub_c.values()) or 1
    top_share = (sub_c.most_common(1)[0][1] / total) if sub_c else 0
    distinct = len(sub_c)

    if top_share >= 0.6:
        shape = "PLATEAU"          # Dominated by one subgenre
    elif max_run >= max(6, n // 10):
        shape = "BLOCKS"            # Long plateaus, distinct sections
    elif transition_rate >= 0.85 and distinct >= max(5, n // 8):
        shape = "ECLECTIC"          # Constant shift, many colors
    elif transition_rate >= 0.65:
        shape = "WAVE"              # Frequent shift, moderate palette
    else:
        shape = "MIXED"

    # Quintile DNA — which subgenre dominates each 20% slice
    quintiles = [[] for _ in range(5)]
    for i, s in enumerate(subs):
        q = min(4, int(5 * i / n))
        if s:
            quintiles[q].append(s)
    q_tops = []
    for q in quintiles:
        c = Counter(q)
        q_tops.append(c.most_common(1)[0][0] if c else "?")

    return {
        "n": n,
        "strip": strip,
        "subs": subs,
        "transitions": transitions,
        "transition_rate": transition_rate,
        "family_transitions": family_transitions,
        "max_run": max_run,
        "median_run": median_run,
        "shape": shape,
        "top_share": top_share,
        "distinct": distinct,
        "top_subgenres": sub_c.most_common(5),
        "quintile_tops": q_tops,
    }


def fmt_set(sdata: Dict, analysis: Dict) -> str:
    m = sdata["meta"]
    a = analysis
    out = []
    hrs = ("~1h" if a["n"] <= 25 else "~2h" if a["n"] <= 45
           else "~3h" if a["n"] <= 60 else "~4h" if a["n"] <= 80 else "5h+")
    out.append(f"### {m['dj_slug']} — {m['set_date']} ({a['n']} tracks, {hrs})")
    out.append(f"**Shape:** {a['shape']}   "
               f"**Transitions:** {a['transitions']}/{a['n']-1} = {a['transition_rate']*100:.0f}% "
               f"(family-crossings: {a['family_transitions']})   "
               f"**Longest plateau:** {a['max_run']} tracks   "
               f"**Distinct subgenres:** {a['distinct']}")
    out.append("")
    out.append("```")
    # Strip with position ruler
    ruler = "".join(str((i // 10) % 10) if i % 10 == 0 else " " for i in range(a["n"]))
    out.append(ruler)
    out.append(a["strip"])
    out.append("```")
    top = ", ".join(f"{k} {100*v/sum(dict(a['top_subgenres']).values()):.0f}%"
                    for k, v in a["top_subgenres"])
    out.append(f"**Top subgenres:** {top}")
    out.append(f"**Quintile tops:** OPEN `{a['quintile_tops'][0]}` → "
               f"`{a['quintile_tops'][1]}` → PEAK `{a['quintile_tops'][2]}` → "
               f"`{a['quintile_tops'][3]}` → CLOSE `{a['quintile_tops'][4]}`")
    return "\n".join(out)


def print_legend():
    print("```")
    print("LEGEND — timeline strip codes")
    print("  d = deep house      t = tech house      m = minimal techno   a = acid house")
    print("  c = chicago house   p = progressive house  e = electro       T = trance")
    print("  M = techno/detroit  I = industrial/hard techno  B = dub/ambient techno")
    print("  H = generic house   R = rock            y = synth/electropop")
    print("  G = uk garage       S = dubstep          Q = grime/future bass  J = dnb/jungle")
    print("  # = hip-hop         % = latin/afro/club  $ = disco-funk-soul   ~ = reggae/dub")
    print("  n = nu/cosmic disco i = italo disco      o = indie/dream pop   z = ambient")
    print("  X = idm/experimental  w = downtempo     q = jazz/classical/folk")
    print("  . = unclassified")
    print("```")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", help="One set_id")
    ap.add_argument("--dj", help="DJ slug — show all their sets")
    ap.add_argument("--venue", help="Substring match on set_id (e.g., 'space-miami', 'burning-man')")
    ap.add_argument("--md", help="Write to markdown file")
    ap.add_argument("--min-tracks", type=int, default=15)
    args = ap.parse_args()

    with connect() as conn:
        ids = []
        if args.set:
            ids = [args.set]
            title = f"Set: {args.set}"
        elif args.dj:
            rows = conn.execute("""
                SELECT set_id FROM dj_sets WHERE dj_slug = ? ORDER BY set_date
            """, (args.dj,)).fetchall()
            ids = [r["set_id"] for r in rows]
            title = f"All sets by {args.dj}"
        elif args.venue:
            rows = conn.execute("""
                SELECT set_id FROM dj_sets WHERE set_id LIKE ? ORDER BY track_count DESC
            """, (f"%{args.venue}%",)).fetchall()
            ids = [r["set_id"] for r in rows]
            title = f"All sets matching venue '{args.venue}'"
        else:
            ap.print_help()
            return

        lines = [f"# Set Shapes — {title}\n"]
        lines.append("Timeline strip: one character per track (see legend below).")
        lines.append("Shape codes: PLATEAU (dominated by 1 subgenre), BLOCKS (long plateaus, distinct sections),")
        lines.append("WAVE (frequent shifts, moderate palette), ECLECTIC (constant shift, wide palette), MIXED.\n")
        lines.append("---\n")

        kept = 0
        for sid in ids:
            sdata = fetch_set(conn, sid)
            if not sdata or len(sdata["tracks"]) < args.min_tracks:
                continue
            analysis = analyze_set(sdata)
            lines.append(fmt_set(sdata, analysis))
            lines.append("")
            kept += 1

        lines.append("\n---\n")
        lines.append("## Legend\n")
        lines.append("```")
        lines.append("d=deep house  t=tech house  m=minimal techno  a=acid house  c=chicago house")
        lines.append("p=progressive house  e=electro  T=trance  M=techno/detroit  I=industrial techno")
        lines.append("B=dub/ambient techno  H=generic house  R=rock  y=synth/electropop")
        lines.append("G=uk garage  S=dubstep  Q=grime/future bass  J=dnb/jungle  #=hip-hop")
        lines.append("%=latin/afro/club  $=disco-funk-soul  ~=reggae/dub  n=nu/cosmic disco")
        lines.append("i=italo disco  o=indie/dream pop  z=ambient  X=idm/experimental  w=downtempo")
        lines.append("q=jazz/classical/folk  .=unclassified")
        lines.append("```")

        md = "\n".join(lines)
        if args.md:
            with open(args.md, "w") as f:
                f.write(md)
            print(f"Wrote {args.md} ({kept} sets)")
        else:
            print(md)


if __name__ == "__main__":
    main()
