"""Cross-DJ track graph — universal vs. signature tracks, edit trading, taste profiles.

Surfaces tracks that cross the DJ scene (played by many) vs. tracks that define
individual selectors (played repeatedly by exactly one). Also flags titles
credited to multiple artists (a common signal for bootlegs, edits, and re-IDs).

Usage:
  python3 cross_track_graph.py                 # full report to stdout
  python3 cross_track_graph.py --dj <slug>     # single-DJ focus (sections 2+4)
  python3 cross_track_graph.py --md <path>     # write markdown to file
"""

import argparse
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from db import connect


# ---------------------------------------------------------------------------
# Normalization / filtering
# ---------------------------------------------------------------------------

SKIP_ARTISTS = {"id", "i.d.", "unknown", "?", ""}


def norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def is_valid(raw_artist: str, raw_title: str) -> bool:
    a = norm(raw_artist)
    t = norm(raw_title)
    if not t:
        return False
    if a in SKIP_ARTISTS:
        return False
    return True


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_plays(conn) -> List[Dict]:
    """Return all valid dj_set_tracks rows joined with dj_sets/djs."""
    rows = conn.execute("""
        SELECT
            t.set_id,
            t.raw_artist,
            t.raw_title,
            t.spotify_id,
            s.dj_slug,
            COALESCE(d.name, s.dj_slug) AS dj_name
        FROM dj_set_tracks t
        JOIN dj_sets s ON t.set_id = s.set_id
        LEFT JOIN djs d ON s.dj_slug = d.slug
    """).fetchall()
    plays = []
    for r in rows:
        if not is_valid(r["raw_artist"], r["raw_title"]):
            continue
        plays.append({
            "set_id": r["set_id"],
            "raw_artist": r["raw_artist"],
            "raw_title": r["raw_title"],
            "spotify_id": r["spotify_id"],
            "dj_slug": r["dj_slug"],
            "dj_name": r["dj_name"],
            "artist_key": norm(r["raw_artist"]),
            "title_key": norm(r["raw_title"]),
        })
    return plays


def dj_set_counts(conn) -> Dict[str, int]:
    rows = conn.execute("""
        SELECT dj_slug, COUNT(*) AS n FROM dj_sets GROUP BY dj_slug
    """).fetchall()
    return {r["dj_slug"]: r["n"] for r in rows}


# ---------------------------------------------------------------------------
# Section 1 — Universal tracks (>=3 distinct DJs)
# ---------------------------------------------------------------------------

def universal_tracks(plays: List[Dict], min_djs: int = 3, top_n: int = 40) -> List[Dict]:
    # Track key = (artist_key, title_key). Accumulate distinct DJs + total plays.
    by_track: Dict[Tuple[str, str], Dict] = defaultdict(lambda: {
        "djs": set(), "plays": 0, "display": None,
    })
    for p in plays:
        k = (p["artist_key"], p["title_key"])
        rec = by_track[k]
        rec["djs"].add(p["dj_name"])
        rec["plays"] += 1
        if rec["display"] is None:
            rec["display"] = f"{p['raw_artist'].strip()} — {p['raw_title'].strip()}"
    out = []
    for k, rec in by_track.items():
        if len(rec["djs"]) >= min_djs:
            out.append({
                "display": rec["display"],
                "n_djs": len(rec["djs"]),
                "djs": sorted(rec["djs"]),
                "plays": rec["plays"],
            })
    out.sort(key=lambda r: (-r["n_djs"], -r["plays"]))
    return out[:top_n]


# ---------------------------------------------------------------------------
# Section 2 — Signature tracks (exactly 1 DJ, >=3 of their sets)
# ---------------------------------------------------------------------------

def signature_tracks(plays: List[Dict], min_sets: int = 3, top_djs: int = 10
                    ) -> List[Tuple[str, List[Dict]]]:
    # Count distinct DJs per track (to find uniques), and distinct sets per (DJ, track).
    track_djs: Dict[Tuple[str, str], set] = defaultdict(set)
    track_dj_sets: Dict[Tuple[str, str, str], set] = defaultdict(set)
    track_display: Dict[Tuple[str, str], str] = {}
    track_dj_name: Dict[Tuple[str, str, str], str] = {}

    for p in plays:
        k = (p["artist_key"], p["title_key"])
        track_djs[k].add(p["dj_slug"])
        track_dj_sets[(p["dj_slug"], *k)].add(p["set_id"])
        if k not in track_display:
            track_display[k] = f"{p['raw_artist'].strip()} — {p['raw_title'].strip()}"
        track_dj_name[(p["dj_slug"], *k)] = p["dj_name"]

    per_dj: Dict[str, List[Dict]] = defaultdict(list)
    per_dj_name: Dict[str, str] = {}
    for (dj_slug, a, t), set_ids in track_dj_sets.items():
        k = (a, t)
        if len(track_djs[k]) != 1:
            continue
        if len(set_ids) < min_sets:
            continue
        per_dj[dj_slug].append({
            "display": track_display[k],
            "plays": len(set_ids),
        })
        per_dj_name[dj_slug] = track_dj_name[(dj_slug, a, t)]

    # Filter to DJs with 2+ signatures, rank by count desc.
    ranked = []
    for slug, sigs in per_dj.items():
        if len(sigs) < 2:
            continue
        sigs.sort(key=lambda r: -r["plays"])
        ranked.append((per_dj_name[slug], sigs))
    ranked.sort(key=lambda x: (-len(x[1]), -sum(s["plays"] for s in x[1])))
    return ranked[:top_djs]


# ---------------------------------------------------------------------------
# Section 3 — Edit trading (same title, different artists, >1 DJ)
# ---------------------------------------------------------------------------

def edit_trading(plays: List[Dict], top_n: int = 20) -> List[Dict]:
    # For each title_key: collect distinct artist credits + distinct DJs.
    by_title: Dict[str, Dict] = defaultdict(lambda: {
        "artists": Counter(),   # display → plays
        "djs": set(),
        "display_title": None,
    })
    for p in plays:
        rec = by_title[p["title_key"]]
        rec["artists"][p["raw_artist"].strip()] += 1
        rec["djs"].add(p["dj_name"])
        if rec["display_title"] is None:
            rec["display_title"] = p["raw_title"].strip()

    out = []
    for title_key, rec in by_title.items():
        # Need multiple distinct artist credits (lowercased) AND multiple DJs.
        artist_keys = {norm(a) for a in rec["artists"]}
        if len(artist_keys) < 2:
            continue
        if len(rec["djs"]) < 2:
            continue
        variants = [a for a, _ in rec["artists"].most_common()]
        out.append({
            "title": rec["display_title"],
            "variants": variants,
            "n_variants": len(artist_keys),
            "n_djs": len(rec["djs"]),
            "total_plays": sum(rec["artists"].values()),
        })
    out.sort(key=lambda r: (-r["n_djs"], -r["n_variants"], -r["total_plays"]))
    return out[:top_n]


# ---------------------------------------------------------------------------
# Section 4 — DJ taste profile
# ---------------------------------------------------------------------------

def taste_profile(plays: List[Dict], set_counts: Dict[str, int], min_sets: int = 3
                 ) -> List[Dict]:
    # Build universal-track key set.
    by_track: Dict[Tuple[str, str], set] = defaultdict(set)
    for p in plays:
        by_track[(p["artist_key"], p["title_key"])].add(p["dj_slug"])
    universal_keys = {k for k, djs in by_track.items() if len(djs) >= 3}
    unique_keys = {k for k, djs in by_track.items() if len(djs) == 1}

    # Per DJ: unique tracks they've played, how many of their sets per track.
    dj_tracks: Dict[str, set] = defaultdict(set)
    dj_track_sets: Dict[Tuple[str, Tuple[str, str]], set] = defaultdict(set)
    dj_name: Dict[str, str] = {}
    for p in plays:
        k = (p["artist_key"], p["title_key"])
        dj_tracks[p["dj_slug"]].add(k)
        dj_track_sets[(p["dj_slug"], k)].add(p["set_id"])
        dj_name[p["dj_slug"]] = p["dj_name"]

    out = []
    for slug, total_sets in set_counts.items():
        if total_sets < min_sets:
            continue
        tracks = dj_tracks.get(slug, set())
        if not tracks:
            continue
        n_total = len(tracks)
        n_universal = sum(1 for k in tracks if k in universal_keys)
        # Signatures: unique-to-this-DJ AND played in >=3 of their sets.
        n_signature = 0
        for k in tracks:
            if k not in unique_keys:
                continue
            if len(dj_track_sets[(slug, k)]) >= 3:
                n_signature += 1
        out.append({
            "slug": slug,
            "name": dj_name.get(slug, slug),
            "sets": total_sets,
            "unique_tracks": n_total,
            "universal": n_universal,
            "signatures": n_signature,
            "universal_ratio": n_universal / n_total if n_total else 0.0,
            "signature_ratio": n_signature / n_total if n_total else 0.0,
        })
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _truncate(s: str, n: int) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def render_universal(rows: List[Dict]) -> List[str]:
    out = []
    out.append("═══ UNIVERSAL TRACKS ═══")
    out.append("Tracks played by 3+ distinct DJs. Sorted by # DJs, then total plays.")
    out.append("")
    if not rows:
        out.append("  (none)")
        return out
    out.append(f"  {'#DJ':>3}  {'plays':>5}  {'track':<60}  DJs")
    out.append(f"  {'-'*3}  {'-'*5}  {'-'*60}  {'-'*40}")
    for r in rows:
        djs = ", ".join(r["djs"][:6])
        if len(r["djs"]) > 6:
            djs += f", +{len(r['djs']) - 6}"
        out.append(f"  {r['n_djs']:>3}  {r['plays']:>5}  "
                   f"{_truncate(r['display'], 60):<60}  {djs}")
    return out


def render_signatures(grouped: List[Tuple[str, List[Dict]]]) -> List[str]:
    out = []
    out.append("═══ SIGNATURE TRACKS ═══")
    out.append("Tracks played by exactly 1 DJ in 3+ of their sets.")
    out.append("Only DJs with 2+ signatures shown. Top 10 DJs.")
    out.append("")
    if not grouped:
        out.append("  (none)")
        return out
    for dj_name, sigs in grouped:
        out.append(f"  {dj_name}  ({len(sigs)} signatures)")
        for s in sigs:
            out.append(f"    {s['plays']:>2} sets  {_truncate(s['display'], 72)}")
        out.append("")
    return out


def render_edit_trading(rows: List[Dict]) -> List[str]:
    out = []
    out.append("═══ EDIT TRADING ═══")
    out.append("Same title credited to multiple artists across multiple DJs.")
    out.append("Often a bootleg/edit IDed differently, or a track re-credited.")
    out.append("")
    if not rows:
        out.append("  (none)")
        return out
    out.append(f"  {'#DJ':>3}  {'#var':>4}  {'title':<40}  credited-as variants")
    out.append(f"  {'-'*3}  {'-'*4}  {'-'*40}  {'-'*50}")
    for r in rows:
        variants = " / ".join(_truncate(v, 22) for v in r["variants"][:4])
        if len(r["variants"]) > 4:
            variants += f" (+{len(r['variants']) - 4})"
        out.append(f"  {r['n_djs']:>3}  {r['n_variants']:>4}  "
                   f"{_truncate(r['title'], 40):<40}  {variants}")
    return out


def render_taste_profile(rows: List[Dict], focus: Optional[str] = None) -> List[str]:
    out = []
    out.append("═══ DJ TASTE PROFILE ═══")
    out.append("For DJs with 3+ sets: # universal tracks played, # signatures, and")
    out.append("their ratios (low universal + high signature = crate-digger; the")
    out.append("inverse = floorfiller-heavy).")
    out.append("")
    if focus:
        rows = [r for r in rows if r["slug"] == focus]
    if not rows:
        out.append("  (none)")
        return out
    # Sort by universal_ratio desc by default, so floorfillers float up.
    rows = sorted(rows, key=lambda r: -r["universal_ratio"])
    out.append(f"  {'DJ':<26}  {'sets':>4}  {'uniq':>5}  "
               f"{'univ':>4}  {'sig':>4}  {'univ%':>5}  {'sig%':>5}")
    out.append(f"  {'-'*26}  {'-'*4}  {'-'*5}  {'-'*4}  {'-'*4}  {'-'*5}  {'-'*5}")
    for r in rows:
        out.append(
            f"  {_truncate(r['name'], 26):<26}  {r['sets']:>4}  "
            f"{r['unique_tracks']:>5}  {r['universal']:>4}  {r['signatures']:>4}  "
            f"{r['universal_ratio']*100:>4.1f}%  {r['signature_ratio']*100:>4.1f}%"
        )
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_report(conn, dj_focus: Optional[str] = None) -> str:
    plays = load_plays(conn)
    set_counts = dj_set_counts(conn)

    lines = []
    lines.append("═" * 72)
    if dj_focus:
        name = next((p["dj_name"] for p in plays if p["dj_slug"] == dj_focus), dj_focus)
        lines.append(f"  CROSS-TRACK GRAPH — {name} (focus)")
    else:
        lines.append("  CROSS-TRACK GRAPH")
    n_djs = len({p["dj_slug"] for p in plays})
    n_sets = len({p["set_id"] for p in plays})
    lines.append(f"  {len(plays)} valid plays  |  {n_sets} sets  |  {n_djs} DJs")
    lines.append("═" * 72)
    lines.append("")

    if not dj_focus:
        lines.extend(render_universal(universal_tracks(plays)))
        lines.append("")

    sigs = signature_tracks(plays)
    if dj_focus:
        sigs = [(name, tracks) for name, tracks in sigs
                if any(p["dj_slug"] == dj_focus and p["dj_name"] == name
                       for p in plays)]
        # Fallback: compute signatures for the focused DJ even if they have <2.
        if not sigs:
            all_sigs_raw = _signatures_for_dj(plays, dj_focus)
            if all_sigs_raw:
                focus_name = next(
                    (p["dj_name"] for p in plays if p["dj_slug"] == dj_focus),
                    dj_focus,
                )
                sigs = [(focus_name, all_sigs_raw)]
    lines.extend(render_signatures(sigs))
    lines.append("")

    if not dj_focus:
        lines.extend(render_edit_trading(edit_trading(plays)))
        lines.append("")

    lines.extend(render_taste_profile(taste_profile(plays, set_counts),
                                      focus=dj_focus))
    lines.append("")

    return "\n".join(lines)


def _signatures_for_dj(plays: List[Dict], slug: str, min_sets: int = 3
                      ) -> List[Dict]:
    """Fallback: return signature tracks for a specific DJ regardless of count threshold."""
    by_track: Dict[Tuple[str, str], set] = defaultdict(set)
    for p in plays:
        by_track[(p["artist_key"], p["title_key"])].add(p["dj_slug"])
    track_sets: Dict[Tuple[str, str], set] = defaultdict(set)
    track_display: Dict[Tuple[str, str], str] = {}
    for p in plays:
        if p["dj_slug"] != slug:
            continue
        k = (p["artist_key"], p["title_key"])
        track_sets[k].add(p["set_id"])
        if k not in track_display:
            track_display[k] = f"{p['raw_artist'].strip()} — {p['raw_title'].strip()}"
    out = []
    for k, set_ids in track_sets.items():
        if len(by_track[k]) != 1:
            continue
        if len(set_ids) < min_sets:
            continue
        out.append({"display": track_display[k], "plays": len(set_ids)})
    out.sort(key=lambda r: -r["plays"])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dj", help="DJ slug — show focused sections (2 + 4) only")
    ap.add_argument("--md", help="Write report to markdown file")
    args = ap.parse_args()

    with connect() as conn:
        report = build_report(conn, dj_focus=args.dj)

    if args.md:
        with open(args.md, "w") as f:
            f.write(report)
        print(f"Wrote {args.md}")
    else:
        print(report)


if __name__ == "__main__":
    main()
