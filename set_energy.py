"""Set energy & BPM arc — visualize the tempo/intensity curve across a DJ set.

No track-level BPM (Spotify audio features blocked). Uses subgenre BPM bands from
transition_atlas.SUBGENRE_BPM plus texture-derived intensity as a proxy.

Usage:
  python3 set_energy.py --set <set_id>            # BPM + energy curve for one set
  python3 set_energy.py --dj <dj>                  # all sets by DJ, stacked
  python3 set_energy.py --venue space-miami        # all sets at venue
  python3 set_energy.py --compare <sid1>,<sid2>    # side-by-side
"""

import argparse
import json
from collections import Counter
from typing import Dict, List, Optional, Tuple

from db import connect
from transition_atlas import SUBGENRE_BPM, FAMILY


# Texture → energy score (0-1 relative intensity)
TEXTURE_ENERGY = {
    "aggressive": 0.95,
    "raw/gritty": 0.80,
    "euphoric": 0.75,
    "dark": 0.70,
    "groovy/funky": 0.60,
    "warm/soulful": 0.55,
    "stripped/minimal": 0.50,
    "atmospheric": 0.35,
    "melancholic": 0.30,
}

SPARK = "▁▂▃▄▅▆▇█"


def bpm_mid(sg: str, genre: str = None) -> Optional[int]:
    if sg:
        band = SUBGENRE_BPM.get(sg)
        if band:
            return int((band[0] + band[1]) / 2)
    if genre:
        band = SUBGENRE_BPM.get(genre)
        if band:
            return int((band[0] + band[1]) / 2)
    return None


def energy_score(textures: List[str]) -> Optional[float]:
    if not textures:
        return None
    vals = [TEXTURE_ENERGY.get(t) for t in textures if t in TEXTURE_ENERGY]
    if not vals:
        return None
    return sum(vals) / len(vals)


def sparkline(values: List[Optional[float]], lo: float, hi: float) -> str:
    """Render values into spark characters. None → space."""
    if hi == lo:
        hi = lo + 1
    out = []
    for v in values:
        if v is None:
            out.append(" ")
        else:
            frac = max(0, min(1, (v - lo) / (hi - lo)))
            idx = int(frac * (len(SPARK) - 1))
            out.append(SPARK[idx])
    return "".join(out)


def fetch_set_curves(conn, set_id: str):
    meta = conn.execute("""
        SELECT set_id, dj_slug, set_date, track_count FROM dj_sets WHERE set_id=?
    """, (set_id,)).fetchone()
    if not meta:
        return None

    rows = conn.execute("""
        SELECT t.position, t.raw_artist, t.raw_title,
               c.genre, c.subgenre, c.texture, c.rhythm
        FROM dj_set_tracks t
        LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
        WHERE t.set_id=? ORDER BY t.position
    """, (set_id,)).fetchall()

    bpms: List[Optional[int]] = []
    energies: List[Optional[float]] = []
    subs: List[Optional[str]] = []
    for r in rows:
        sg = r["subgenre"] or r["genre"]
        subs.append(sg)
        bpms.append(bpm_mid(r["subgenre"], r["genre"]))
        try:
            tex = json.loads(r["texture"]) if r["texture"] else []
        except Exception:
            tex = []
        energies.append(energy_score(tex))
    return dict(meta), rows, subs, bpms, energies


def render_set(conn, set_id: str) -> str:
    fetched = fetch_set_curves(conn, set_id)
    if not fetched:
        return f"No set '{set_id}'"
    meta, rows, subs, bpms, energies = fetched
    n = len(rows)

    # Compute min/max for normalization across this set only
    bpm_vals = [b for b in bpms if b is not None]
    e_vals = [e for e in energies if e is not None]
    bpm_lo = min(bpm_vals) if bpm_vals else 100
    bpm_hi = max(bpm_vals) if bpm_vals else 140
    e_lo = min(e_vals) if e_vals else 0
    e_hi = max(e_vals) if e_vals else 1

    hrs = ("~1h" if n <= 25 else "~2h" if n <= 45
           else "~3h" if n <= 60 else "~4h" if n <= 80 else "5h+")

    lines = []
    lines.append(f"### {meta['dj_slug']} — {meta['set_date']} ({n} tracks, {hrs})")
    lines.append("")

    # Quintile BPM averages (for summary row)
    q_bpms = [[], [], [], [], []]
    q_energy = [[], [], [], [], []]
    for i, (b, e) in enumerate(zip(bpms, energies)):
        q = min(4, int(5 * i / n))
        if b is not None:
            q_bpms[q].append(b)
        if e is not None:
            q_energy[q].append(e)
    q_bpm_avg = [int(sum(q) / len(q)) if q else None for q in q_bpms]
    q_e_avg = [sum(q) / len(q) if q else None for q in q_energy]

    lines.append("```")
    lines.append(f"BPM  ({bpm_lo}-{bpm_hi})  {sparkline([float(b) if b else None for b in bpms], bpm_lo, bpm_hi)}")
    lines.append(f"Energy         {sparkline(energies, e_lo, e_hi)}")
    # Position ruler (every 10)
    ruler = "".join(str((i // 10) % 10) if i % 10 == 0 else " " for i in range(n))
    lines.append(f"Pos            {ruler}")
    lines.append("```")
    lines.append("")

    # Quintile BPM summary
    q_labels = ["OPEN", "BUILD", "PEAK", "PLATEAU", "CLOSE"]
    bpm_line = "  ".join(
        f"{lbl}: {b if b else '?'}" for lbl, b in zip(q_labels, q_bpm_avg)
    )
    lines.append(f"**BPM arc:** {bpm_line}")

    if any(e is not None for e in q_e_avg):
        e_line = "  ".join(
            f"{lbl}: {int(100*e) if e else '?'}%" for lbl, e in zip(q_labels, q_e_avg)
        )
        lines.append(f"**Energy arc (0-100%):** {e_line}")

    # Peak BPM and peak position
    if bpm_vals:
        peak_bpm = max(bpm_vals)
        peak_pos = bpms.index(peak_bpm)
        lines.append(f"**Peak BPM:** {peak_bpm} at track #{peak_pos+1} "
                     f"({int(100*peak_pos/n)}% through the set)")

    # BPM range / spread
    if bpm_vals:
        spread = max(bpm_vals) - min(bpm_vals)
        lines.append(f"**BPM range:** {min(bpm_vals)}–{max(bpm_vals)} "
                     f"(spread: {spread})")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", help="One set_id")
    ap.add_argument("--dj", help="DJ slug — all sets")
    ap.add_argument("--venue", help="Substring match on set_id")
    ap.add_argument("--compare", help="Comma-separated set_ids for side-by-side")
    ap.add_argument("--md", help="Write to file")
    ap.add_argument("--min-tracks", type=int, default=15)
    args = ap.parse_args()

    with connect() as conn:
        if args.compare:
            ids = [s.strip() for s in args.compare.split(",")]
        elif args.set:
            ids = [args.set]
        elif args.dj:
            rows = conn.execute(
                "SELECT set_id FROM dj_sets WHERE dj_slug=? ORDER BY set_date",
                (args.dj,),
            ).fetchall()
            ids = [r["set_id"] for r in rows]
        elif args.venue:
            rows = conn.execute(
                "SELECT set_id FROM dj_sets WHERE set_id LIKE ? ORDER BY track_count DESC",
                (f"%{args.venue}%",),
            ).fetchall()
            ids = [r["set_id"] for r in rows]
        else:
            ap.print_help()
            return

        lines = ["# Set Energy & BPM Arcs\n"]
        lines.append("BPM is estimated from subgenre midpoints (no track-level BPM available).")
        lines.append("Energy is from texture layer (0-100%: atmospheric → aggressive).\n")
        lines.append("Spark height shows relative intensity within THIS set.\n")
        lines.append("---\n")
        kept = 0
        for sid in ids:
            fetched = fetch_set_curves(conn, sid)
            if not fetched or len(fetched[1]) < args.min_tracks:
                continue
            lines.append(render_set(conn, sid))
            lines.append("\n")
            kept += 1
        output = "\n".join(lines)
        if args.md:
            with open(args.md, "w") as f:
                f.write(output)
            print(f"Wrote {args.md} ({kept} sets)")
        else:
            print(output)


if __name__ == "__main__":
    main()
