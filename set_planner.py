"""Set Planner — given a starting subgenre, target length, and reference style,
suggest an arc shape based on observed DJ patterns in the library.

No track-level recommendations (that's a different problem). This plans the
*structure*: track count, quintile composition, where to pivot, what moves to
make at each boundary, where to drop a curveball.

Usage:
  python3 set_planner.py --start "deep house" --hours 3
  python3 set_planner.py --start "tech house" --hours 4 --like carl-cox
  python3 set_planner.py --start "deep house" --hours 2 --venue space-miami
"""

import argparse
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from db import connect
from transition_atlas import SUBGENRE_BPM, FAMILY, bpm_mid, classify_move, MOVE_LABELS


def tracks_for_hours(h: float) -> int:
    """~3-3.5 min/track averaged across dance sets."""
    return int(h * 60 / 3.3)


def fetch_reference_sets(conn, hours: float, like_dj: str = None, venue: str = None,
                         start_sg: str = None, tolerance: int = 20):
    """Find sets in DB matching target length (±tolerance tracks) + optional filters."""
    target_n = tracks_for_hours(hours)
    params: List = []
    q = """
        SELECT set_id, dj_slug, set_date, track_count
        FROM dj_sets
        WHERE track_count BETWEEN ? AND ?
    """
    params.extend([target_n - tolerance, target_n + tolerance])
    if like_dj:
        q += " AND dj_slug = ?"
        params.append(like_dj)
    if venue:
        q += " AND set_id LIKE ?"
        params.append(f"%{venue}%")
    q += " ORDER BY track_count DESC"
    sets = conn.execute(q, params).fetchall()

    if not start_sg:
        return sets

    # Filter to sets that OPEN near the starting subgenre
    filtered = []
    for s in sets:
        opening = conn.execute("""
            SELECT COALESCE(c.subgenre, c.genre) AS sg
            FROM dj_set_tracks t LEFT JOIN classifications c ON c.spotify_id=t.spotify_id
            WHERE t.set_id = ? ORDER BY t.position LIMIT 5
        """, (s["set_id"],)).fetchall()
        openers = [r["sg"] for r in opening if r["sg"]]
        if start_sg.lower() in [o.lower() for o in openers]:
            filtered.append(s)
    return filtered if filtered else sets


def quintile_composition(conn, set_ids: List[str]) -> List[Counter]:
    """For a set of sets, compute average per-quintile subgenre distribution."""
    q_counts = [Counter() for _ in range(5)]
    for sid in set_ids:
        rows = conn.execute("""
            SELECT t.position, COALESCE(c.subgenre, c.genre) AS sg
            FROM dj_set_tracks t LEFT JOIN classifications c ON c.spotify_id=t.spotify_id
            WHERE t.set_id = ? ORDER BY t.position
        """, (sid,)).fetchall()
        n = len(rows)
        if n == 0:
            continue
        for i, r in enumerate(rows):
            if not r["sg"]:
                continue
            q = min(4, int(5 * i / n))
            q_counts[q][r["sg"]] += 1
    return q_counts


def suggest_curveball_slot(set_ids: List[str]) -> Dict:
    """Among the reference sets, where does a curveball (out-of-character drop)
    typically land? Returns stats on position fraction."""
    positions = []
    for sid in set_ids:
        rows = conn_global.execute("""
            SELECT t.position, s.dj_slug, c.genre FROM dj_set_tracks t
            JOIN dj_sets s ON t.set_id=s.set_id
            JOIN classifications c ON c.spotify_id=t.spotify_id
            WHERE t.set_id = ? AND c.genre IS NOT NULL
            ORDER BY t.position
        """, (sid,)).fetchall()
        if not rows:
            continue
        # DJ's overall genre distribution
        dj = rows[0]["dj_slug"]
        dj_genres = conn_global.execute("""
            SELECT c.genre, COUNT(*) AS n FROM dj_set_tracks tt
            JOIN dj_sets ss ON tt.set_id=ss.set_id
            JOIN classifications c ON c.spotify_id=tt.spotify_id
            WHERE ss.dj_slug = ? GROUP BY c.genre
        """, (dj,)).fetchall()
        total = sum(r["n"] for r in dj_genres) or 1
        rare = {r["genre"] for r in dj_genres if r["n"] / total < 0.05}
        n = len(rows)
        for i, r in enumerate(rows):
            if r["genre"] in rare:
                positions.append(i / n)
    return {
        "count": len(positions),
        "median_pos": sorted(positions)[len(positions)//2] if positions else None,
        "positions": positions,
    }


conn_global = None  # set in main()


def plan(start: str, hours: float, like_dj: str = None, venue: str = None):
    global conn_global
    with connect() as conn:
        conn_global = conn
        target_n = tracks_for_hours(hours)
        ref_sets = fetch_reference_sets(conn, hours, like_dj, venue, start_sg=start)
        ref_ids = [r["set_id"] for r in ref_sets]

        print(f"# Set Plan — {hours}h starting at `{start}`")
        filters = []
        if like_dj:   filters.append(f"like **{like_dj}**")
        if venue:     filters.append(f"at **{venue}**")
        if filters:
            print(f"_Style: {', '.join(filters)}_")
        print(f"\n**Target:** ~{target_n} tracks over {hours}h (≈3.3 min/track avg)\n")

        if ref_ids:
            print(f"**Reference sets** ({len(ref_ids)} in library within ±20 tracks"
                  + (" starting similarly" if start else "") + "):")
            for r in ref_sets[:6]:
                print(f"  - {r['dj_slug']} — {r['set_date']} ({r['track_count']} tracks)")
            print()

        if not ref_ids:
            # Fall back to broader filter if nothing matches with starting-sg constraint
            ref_sets = fetch_reference_sets(conn, hours, like_dj, venue)
            ref_ids = [r["set_id"] for r in ref_sets]
            if ref_ids:
                print(f"_No sets open at `{start}` with these filters — using all {len(ref_ids)} sets "
                      f"of similar length as reference._\n")
            else:
                print(f"_No reference sets match your filters. Using the full library._\n")
                ref_sets = [r for r in conn.execute(
                    "SELECT set_id, dj_slug, set_date, track_count FROM dj_sets "
                    "WHERE track_count BETWEEN ? AND ?",
                    (target_n - 30, target_n + 30)).fetchall()]
                ref_ids = [r["set_id"] for r in ref_sets]

        # ----- Quintile composition from reference sets -----
        q_counts = quintile_composition(conn, ref_ids)

        n_per_q = max(1, target_n // 5)
        q_labels = [
            ("OPEN", "0-20%", "1-"+str(n_per_q)),
            ("BUILD", "20-40%", str(n_per_q+1)+"-"+str(2*n_per_q)),
            ("PEAK", "40-60%", str(2*n_per_q+1)+"-"+str(3*n_per_q)),
            ("PLATEAU", "60-80%", str(3*n_per_q+1)+"-"+str(4*n_per_q)),
            ("CLOSE", "80-100%", str(4*n_per_q+1)+"-"+str(target_n)),
        ]

        print("## Suggested arc — composition per quintile\n")
        for i, (lbl, pct, rng) in enumerate(q_labels):
            total = sum(q_counts[i].values()) or 1
            top3 = q_counts[i].most_common(3)
            mix = " · ".join(f"{k} {100*v/total:.0f}%" for k, v in top3)
            avg_bpm = None
            bpms = [bpm_mid(k) for k, _ in q_counts[i].most_common() if bpm_mid(k)]
            if bpms:
                # Weight by frequency
                all_bpms = []
                for k, v in q_counts[i].most_common():
                    b = bpm_mid(k)
                    if b: all_bpms.extend([b] * v)
                avg_bpm = int(sum(all_bpms)/len(all_bpms)) if all_bpms else None
            bpm_hint = f"~{avg_bpm} BPM" if avg_bpm else ""
            print(f"### {lbl}  ({pct}, tracks {rng})   {bpm_hint}")
            print(f"**Mix:** {mix}\n")

        # ----- Suggested transitions at each quintile boundary -----
        print("## Suggested transitions (at quintile boundaries)\n")
        # Infer "typical move" at boundary i→i+1 from top subgenre in each
        for i in range(4):
            prev_top = q_counts[i].most_common(1)
            nxt_top = q_counts[i+1].most_common(1)
            if not prev_top or not nxt_top:
                continue
            p, n_p = prev_top[0]
            nx, n_n = nxt_top[0]
            if p == nx:
                print(f"**{q_labels[i][0]} → {q_labels[i+1][0]}:** stay in **{p}** "
                      f"(no change — keep the groove)")
            else:
                mv = classify_move(p, nx)
                bp, bn = bpm_mid(p), bpm_mid(nx)
                delta = (bn - bp) if (bp and bn) else None
                d_str = f"Δ{delta:+d} BPM" if delta is not None else ""
                print(f"**{q_labels[i][0]} → {q_labels[i+1][0]}:** {p} → **{nx}** "
                      f"`{mv}` {d_str}  _{MOVE_LABELS[mv]}_")
        print()

        # ----- Curveball placement -----
        cv = suggest_curveball_slot(ref_ids)
        if cv["count"] > 0:
            med = cv["median_pos"]
            track_pos = int(med * target_n) if med else None
            print("## Where to drop a curveball\n")
            print(f"In {len(ref_ids)} reference sets, **{cv['count']} out-of-character tracks** "
                  f"were observed.")
            if track_pos:
                quintile = "OPEN" if med < 0.2 else "BUILD" if med < 0.4 else "PEAK" if med < 0.6 else "PLATEAU" if med < 0.8 else "CLOSE"
                print(f"**Median drop position:** {int(100*med)}% through (≈ track #{track_pos}, in {quintile}).")
                print(f"\nSome DJs drop them early (Carl Cox: tracks 3-4). Most drop them mid-set "
                      f"as a breather between peak pushes. The Hayden James pattern is a pop/rock "
                      f"curveball at ~50-70% through the set.")

        # ----- Reference set strip (if any) -----
        if ref_ids:
            print("\n## Closest-match reference set to study\n")
            best = ref_sets[0]
            print(f"**{best['dj_slug']} — {best['set_date']}** ({best['track_count']} tracks)  ")
            print(f"  Run: `python3 set_shapes.py --set {best['set_id']}` for the full timeline strip  ")
            print(f"  Run: `python3 set_energy.py --set {best['set_id']}` for the BPM/energy arc")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="Starting subgenre (e.g., 'deep house')")
    ap.add_argument("--hours", type=float, default=3.0, help="Target set length in hours")
    ap.add_argument("--like", dest="like_dj", help="DJ slug to emulate")
    ap.add_argument("--venue", help="Venue substring (e.g., 'space-miami')")
    args = ap.parse_args()
    plan(args.start, args.hours, args.like_dj, args.venue)


if __name__ == "__main__":
    main()
