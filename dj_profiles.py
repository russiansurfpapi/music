"""Cross-DJ analysis: per-DJ subgenre profile, hop pattern, era leaning, distinctiveness.

Usage:
    python3 dj_profiles.py            # full report to stdout
    python3 dj_profiles.py --md OUT.md
    python3 dj_profiles.py --dj sasha # one DJ deep dive
"""

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from typing import Dict, List

from db import connect


def _list(s):
    try:
        return json.loads(s) if s else []
    except (json.JSONDecodeError, TypeError):
        return []


def _entropy(counter: Counter) -> float:
    total = sum(counter.values())
    if total == 0:
        return 0.0
    return -sum((c / total) * math.log2(c / total) for c in counter.values() if c > 0)


def _runs(values: List[str]) -> List[int]:
    """Return run lengths of consecutive equal values."""
    runs = []
    cur = None
    n = 0
    for v in values:
        if v == cur:
            n += 1
        else:
            if cur is not None:
                runs.append(n)
            cur = v
            n = 1
    if cur is not None:
        runs.append(n)
    return runs


def gather(conn, min_resolved: int = 15):
    """Returns {dj_slug: {sets: [..set_ids..], tracks: [classified-track-dicts]}}."""
    by_dj: Dict[str, Dict] = defaultdict(lambda: {"sets": [], "tracks": []})
    set_rows = conn.execute(
        "SELECT s.set_id, s.dj_slug, s.set_date FROM dj_sets s WHERE s.resolved_count > 0"
    ).fetchall()
    for s in set_rows:
        rows = conn.execute("""
            SELECT t.position, t.spotify_id, c.genre, c.subgenre, c.rhythm,
                   c.production_dna, c.texture, c.lineage, tr.release_year
            FROM dj_set_tracks t
            LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
            LEFT JOIN tracks tr ON tr.spotify_id = t.spotify_id
            WHERE t.set_id = ? ORDER BY t.position
        """, (s["set_id"],)).fetchall()
        clean = []
        for r in rows:
            if not r["genre"]:
                continue
            clean.append({
                "set_id": s["set_id"],
                "pos": r["position"],
                "genre": r["genre"],
                "subgenre": r["subgenre"] or r["genre"],
                "rhythm": r["rhythm"],
                "dna": _list(r["production_dna"]),
                "texture": _list(r["texture"]),
                "lineage": _list(r["lineage"]),
                "year": r["release_year"],
            })
        if clean:
            by_dj[s["dj_slug"]]["sets"].append(s["set_id"])
            by_dj[s["dj_slug"]]["tracks"].extend(clean)

    return {dj: data for dj, data in by_dj.items() if len(data["tracks"]) >= min_resolved}


def profile(dj: str, data: Dict) -> Dict:
    tracks = data["tracks"]
    by_set: Dict[str, List[Dict]] = defaultdict(list)
    for t in tracks:
        by_set[t["set_id"]].append(t)
    # Sort tracks by position within each set.
    for sid in by_set:
        by_set[sid].sort(key=lambda x: x["pos"])

    sub_c = Counter(t["subgenre"] for t in tracks)
    gen_c = Counter(t["genre"] for t in tracks)
    rhy_c = Counter(t["rhythm"] for t in tracks if t["rhythm"])
    dna_c: Counter = Counter()
    tex_c: Counter = Counter()
    lin_c: Counter = Counter()
    for t in tracks:
        for d in t["dna"]:     dna_c[d] += 1
        for x in t["texture"]: tex_c[x] += 1
        for l in t["lineage"]: lin_c[l] += 1

    # Run lengths within each set.
    all_runs: List[int] = []
    for sid, st in by_set.items():
        all_runs.extend(_runs([t["subgenre"] for t in st]))
    median_run = sorted(all_runs)[len(all_runs) // 2] if all_runs else 0
    max_run = max(all_runs) if all_runs else 0

    # Era distribution from release_year.
    era_c: Counter = Counter()
    for t in tracks:
        y = t.get("year")
        if not y:                  era_c["?"] += 1
        elif y < 1980:             era_c["pre-80s"] += 1
        elif y < 1990:             era_c["80s"] += 1
        elif y < 2000:             era_c["90s"] += 1
        elif y < 2010:             era_c["00s"] += 1
        elif y < 2020:             era_c["10s"] += 1
        else:                       era_c["20s"] += 1

    return {
        "dj": dj,
        "n_sets": len(data["sets"]),
        "n_tracks": len(tracks),
        "subgenre": sub_c,
        "genre": gen_c,
        "rhythm": rhy_c,
        "dna": dna_c,
        "texture": tex_c,
        "lineage": lin_c,
        "era": era_c,
        "median_run": median_run,
        "max_run": max_run,
        "subgenre_entropy": _entropy(sub_c),
    }


def fmt_top(c: Counter, n: int = 5) -> str:
    total = sum(c.values()) or 1
    return ", ".join(f"{k} {100*v/total:.0f}%" for k, v in c.most_common(n))


def report(profiles: List[Dict]) -> str:
    out = []
    out.append("# DJ Set Profiles\n")
    out.append("Cross-set analysis of how each DJ moves between subgenres.\n")
    out.append("**Subgenre entropy** measures variety: higher = more diverse, lower = focused.")
    out.append("**Median run** = how many consecutive tracks they typically stay in one subgenre.\n")

    out.append("## Quick comparison\n")
    out.append("| DJ | Sets | Tracks | Top subgenre | Variety (entropy) | Median run | Era leaning |")
    out.append("|---|---|---|---|---|---|---|")
    for p in profiles:
        top = p["subgenre"].most_common(1)[0][0] if p["subgenre"] else "—"
        era_top = p["era"].most_common(1)[0][0] if p["era"] else "—"
        out.append(
            f"| {p['dj']} | {p['n_sets']} | {p['n_tracks']} | {top} | "
            f"{p['subgenre_entropy']:.2f} | {p['median_run']} | {era_top} |"
        )

    out.append("\n---\n")
    for p in profiles:
        out.append(f"## {p['dj']}  ·  {p['n_sets']} sets, {p['n_tracks']} resolved tracks\n")
        out.append(f"**Top subgenres:** {fmt_top(p['subgenre'], 6)}")
        out.append(f"**Top genres:** {fmt_top(p['genre'], 4)}")
        if p["rhythm"]:
            out.append(f"**Rhythm:** {fmt_top(p['rhythm'], 4)}")
        if p["dna"]:
            out.append(f"**Production DNA:** {fmt_top(p['dna'], 5)}")
        if p["texture"]:
            out.append(f"**Texture:** {fmt_top(p['texture'], 4)}")
        if p["lineage"]:
            out.append(f"**Lineage:** {fmt_top(p['lineage'], 5)}")
        out.append(f"**Era:** {fmt_top(p['era'], 5)}")
        out.append(f"**Hop pattern:** typical run = {p['median_run']} tracks, longest = {p['max_run']} tracks. "
                   f"Variety entropy = {p['subgenre_entropy']:.2f}.")
        # Interpretation
        if p["subgenre_entropy"] < 1.5:
            stance = "**Tight focus** — stays in a narrow subgenre band."
        elif p["subgenre_entropy"] < 2.5:
            stance = "**Disciplined breadth** — covers a few subgenres deliberately."
        else:
            stance = "**Wide hopper** — surveys many subgenres in a single set."
        out.append(stance)
        out.append("")
    return "\n".join(out)


def cmd_main(args) -> None:
    with connect() as conn:
        data = gather(conn, min_resolved=15)
        profiles = [profile(dj, d) for dj, d in data.items()]
        profiles.sort(key=lambda p: -p["n_tracks"])
        if args.dj:
            profiles = [p for p in profiles if p["dj"] == args.dj]
            if not profiles:
                print(f"No DJ '{args.dj}' (with ≥15 classified tracks)")
                return
        md = report(profiles)
        if args.md:
            with open(args.md, "w") as f:
                f.write(md)
            print(f"Wrote {args.md}")
        else:
            print(md)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dj", help="One DJ slug")
    ap.add_argument("--md", help="Write to file")
    cmd_main(ap.parse_args())


if __name__ == "__main__":
    main()
