"""Analyze a single DJ set: subgenre flow, rhythm shifts, production DNA, lineage.

Usage:
    python3 analyze_set.py --list                       # list ingested sets
    python3 analyze_set.py --dj shadow-child            # list sets for a DJ
    python3 analyze_set.py --set-id <set_id>            # full analysis
    python3 analyze_set.py --set-id <set_id> --md OUT.md  # write to file
"""

import argparse
import json
import os
import sys
from collections import Counter
from typing import Dict, List, Optional, Tuple

from db import connect


def _list(col):
    try:
        return json.loads(col) if col else []
    except (json.JSONDecodeError, TypeError):
        return []


def load_set(conn, set_id: str) -> Tuple[Optional[dict], List[dict]]:
    meta = conn.execute(
        "SELECT s.set_id, s.dj_slug, s.title, s.set_date, s.track_count, s.resolved_count, "
        "       d.name AS dj_name "
        "FROM dj_sets s JOIN djs d ON d.slug=s.dj_slug WHERE s.set_id=?",
        (set_id,),
    ).fetchone()
    if not meta:
        return None, []
    rows = conn.execute(
        "SELECT t.position, t.raw_artist, t.raw_title, t.spotify_id, "
        "       c.genre, c.subgenre, c.rhythm, c.production_dna, c.texture, c.lineage, c.confidence "
        "FROM dj_set_tracks t LEFT JOIN classifications c ON c.spotify_id=t.spotify_id "
        "WHERE t.set_id=? ORDER BY t.position",
        (set_id,),
    ).fetchall()
    tracks = []
    for r in rows:
        tracks.append({
            "pos": r["position"], "artist": r["raw_artist"], "title": r["raw_title"],
            "id": r["spotify_id"], "genre": r["genre"], "subgenre": r["subgenre"],
            "rhythm": r["rhythm"], "dna": _list(r["production_dna"]),
            "texture": _list(r["texture"]), "lineage": _list(r["lineage"]),
            "confidence": r["confidence"],
        })
    return dict(meta), tracks


def runs(seq: List[Tuple[int, str]]) -> List[Tuple[int, int, str]]:
    """[(pos, value)] → [(start, end, value)] runs of identical values."""
    out = []
    cur = None
    start = None
    for pos, val in seq:
        if val != cur:
            if cur is not None:
                out.append((start, last, cur))
            cur = val
            start = pos
        last = pos
    if cur is not None:
        out.append((start, last, cur))
    return out


def report_md(meta: dict, tracks: List[dict]) -> str:
    out = []
    out.append(f"# {meta['dj_name']} — {meta['title'] or '(untitled)'}")
    out.append("")
    out.append(f"- Date: {meta['set_date'] or '—'}")
    out.append(f"- Tracks: {meta['track_count']}")
    pct = 100 * meta['resolved_count'] / max(1, meta['track_count'])
    out.append(f"- Resolved: {meta['resolved_count']}/{meta['track_count']} ({pct:.0f}%)")
    out.append("")

    classified = [t for t in tracks if t["genre"]]
    if not classified:
        out.append("_No tracks classified. Set is mostly unresolved against the library._")
        out.append("")
        out.append("## Track-by-track (raw)")
        for t in tracks:
            out.append(f"{t['pos']:>3}. **{t['artist']}** — {t['title']}")
        return "\n".join(out)

    # SUBGENRE FLOW
    out.append("## Subgenre flow")
    sub_seq = [(t["pos"], t["subgenre"] or "—") for t in classified]
    for start, end, sub in runs(sub_seq):
        n = end - start + 1
        out.append(f"- {start:>3}–{end:<3} ({n:>2}): **{sub}**")
    out.append("")

    # RHYTHM
    out.append("## Rhythm distribution")
    rc = Counter(t["rhythm"] for t in classified if t["rhythm"])
    total = sum(rc.values()) or 1
    for r, n in rc.most_common():
        out.append(f"- {r}: {n} ({100*n/total:.0f}%)")
    out.append("")

    # PRODUCTION DNA
    out.append("## Production DNA")
    dna_c: Counter = Counter()
    for t in classified:
        for d in t["dna"]:
            dna_c[d] += 1
    if dna_c:
        for d, n in dna_c.most_common(10):
            out.append(f"- {d}: {n}")
    else:
        out.append("- (none detected)")
    out.append("")

    # TEXTURE
    out.append("## Texture / mood")
    tx_c: Counter = Counter()
    for t in classified:
        for tx in t["texture"]:
            tx_c[tx] += 1
    for tx, n in tx_c.most_common(8):
        out.append(f"- {tx}: {n}")
    out.append("")

    # LINEAGE
    out.append("## Lineage")
    lin_c: Counter = Counter()
    for t in classified:
        for l in t["lineage"]:
            lin_c[l] += 1
    for l, n in lin_c.most_common(10):
        out.append(f"- {l}: {n}")
    out.append("")

    # ARC NARRATIVE
    out.append("## Arc")
    sub_runs = runs(sub_seq)
    if sub_runs:
        opens = sub_runs[0][2]
        closes = sub_runs[-1][2]
        peak_idx = max(range(len(sub_runs)), key=lambda i: sub_runs[i][1] - sub_runs[i][0])
        peak = sub_runs[peak_idx][2]
        out.append(f"- Opens with **{opens}** ({sub_runs[0][1] - sub_runs[0][0] + 1} tracks)")
        out.append(f"- Longest section: **{peak}** ({sub_runs[peak_idx][1] - sub_runs[peak_idx][0] + 1} tracks)")
        out.append(f"- Closes with **{closes}** ({sub_runs[-1][1] - sub_runs[-1][0] + 1} tracks)")
        out.append(f"- Distinct subgenres traversed: {len(set(s for _, _, s in sub_runs if s != '—'))}")
    out.append("")

    # TRACK BY TRACK
    out.append("## Track-by-track")
    for t in tracks:
        if t["genre"]:
            tag_parts = []
            if t["subgenre"]:    tag_parts.append(t["subgenre"])
            elif t["genre"]:     tag_parts.append(t["genre"])
            if t["rhythm"]:      tag_parts.append(t["rhythm"])
            if t["dna"]:         tag_parts.append("/".join(t["dna"][:2]))
            tag = " • ".join(tag_parts)
        else:
            tag = "_unresolved_"
        out.append(f"{t['pos']:>3}. **{t['artist']}** — {t['title']}  ")
        out.append(f"     `{tag}`")
    return "\n".join(out)


def cmd_list(args) -> None:
    with connect() as conn:
        rows = conn.execute(
            "SELECT s.set_id, s.dj_slug, s.title, s.set_date, s.track_count, s.resolved_count "
            "FROM dj_sets s "
            + (f"WHERE s.dj_slug = ? " if args.dj else "")
            + "ORDER BY s.set_date DESC NULLS LAST, s.dj_slug",
            (args.dj,) if args.dj else (),
        ).fetchall()
        for r in rows:
            pct = 100 * r["resolved_count"] / max(1, r["track_count"])
            print(f"  {r['set_date'] or '????-??-??':10s} "
                  f"{r['dj_slug']:24s} "
                  f"{(r['title'] or '')[:50]:50s} "
                  f"{r['track_count']:>3}t / {pct:>3.0f}%  "
                  f"{r['set_id']}")
        print(f"\n  total: {len(rows)} sets")


def cmd_analyze(args) -> None:
    with connect() as conn:
        meta, tracks = load_set(conn, args.set_id)
        if not meta:
            print(f"Unknown set_id: {args.set_id}")
            sys.exit(1)
        md = report_md(meta, tracks)
        if args.md:
            with open(args.md, "w") as f:
                f.write(md)
            print(f"Wrote {args.md}")
        else:
            print(md)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    li = sub.add_parser("list"); li.add_argument("--dj")
    an = sub.add_parser("analyze"); an.add_argument("--set-id", required=True); an.add_argument("--md")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        sys.exit(0)
    if args.cmd == "list":
        cmd_list(args)
    else:
        cmd_analyze(args)


if __name__ == "__main__":
    main()
