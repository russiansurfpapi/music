"""Generate a candidate track sequence that matches a template shape.

Loads a set_templates row (bpm_curve, genre_density, pace_sec_median) and
greedily fills each of its 20 time buckets with tracks from the user's
tagged library that match the bucket's target subgenre mix and BPM.

Usage:
  python3 set_generator.py --template ePJzsMgWOy8:peak
  python3 set_generator.py --like-dj peggy-gou --role warmup
  python3 set_generator.py --template <id> --duration 60
  python3 set_generator.py --template <id> --md out.md
  python3 set_generator.py --template <id> --pool "tech house,deep house"
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from typing import Dict, List, Optional, Tuple

from db import connect
from extract_templates import load_template

try:
    from transition_atlas import SUBGENRE_BPM, FAMILY
except Exception:
    SUBGENRE_BPM = {}
    FAMILY = {}


BPM_TOL = 6.0            # +/- BPM tolerance for pool pre-filter
AVG_TRACK_SEC = 300      # fallback track length when duration_ms is null
NUM_BUCKETS = 20         # matches extract_templates
SKIP_SUBGENRES = {"(unclassified)"}


# ---------------------------------------------------------------- template io

def resolve_template_id(template_id: Optional[str],
                        dj_slug: Optional[str],
                        role: Optional[str]) -> Optional[str]:
    """Resolve a template_id from --template or --like-dj/--role."""
    if template_id:
        return template_id
    if not dj_slug or not role:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT template_id FROM set_templates
               WHERE dj_slug = ? AND role = ?
               ORDER BY duration_sec DESC
               LIMIT 1""",
            (dj_slug, role),
        ).fetchone()
    return row["template_id"] if row else None


def interpolate_bpm(curve: List[List]) -> List[Tuple[int, float]]:
    """Fill None BPMs by linear interpolation between neighbors.

    Falls back to None-only list if the curve is entirely null.
    """
    pts = [(int(sec), (float(bpm) if bpm is not None else None))
           for sec, bpm in curve]
    # Find non-null indices.
    non_null = [i for i, (_, b) in enumerate(pts) if b is not None]
    if not non_null:
        return pts  # all null; caller will fall back to genre-median BPM
    # Fill leading None with first known.
    first = non_null[0]
    for i in range(first):
        pts[i] = (pts[i][0], pts[first][1])
    # Fill trailing None with last known.
    last = non_null[-1]
    for i in range(last + 1, len(pts)):
        pts[i] = (pts[i][0], pts[last][1])
    # Fill interior gaps.
    for a, b in zip(non_null, non_null[1:]):
        if b - a <= 1:
            continue
        lo_bpm = pts[a][1]
        hi_bpm = pts[b][1]
        span = b - a
        for k in range(1, span):
            frac = k / span
            pts[a + k] = (pts[a + k][0], lo_bpm + (hi_bpm - lo_bpm) * frac)
    return pts


def subgenre_median_bpm(density: Dict[str, float]) -> Optional[float]:
    """BPM midpoint derived from the top-weighted subgenres in genre_density."""
    if not density:
        return None
    ranked = sorted(
        ((k, v) for k, v in density.items() if k not in SKIP_SUBGENRES),
        key=lambda x: -x[1],
    )
    mids: List[float] = []
    for sg, weight in ranked:
        band = SUBGENRE_BPM.get(sg)
        if band:
            mids.extend([(band[0] + band[1]) / 2.0] * max(1, int(weight // 60)))
    if not mids:
        return None
    return statistics.median(mids)


def dj_fallback_density(dj_slug: str) -> Dict[str, float]:
    """Aggregate subgenre seconds across all of a DJ's templates."""
    from json import loads
    density: Dict[str, float] = {}
    with connect() as conn:
        rows = conn.execute(
            """SELECT genre_density FROM set_templates
               WHERE dj_slug = ? AND role = 'full'""",
            (dj_slug,),
        ).fetchall()
    for r in rows:
        try:
            d = loads(r["genre_density"] or "{}")
        except Exception:
            continue
        for k, v in d.items():
            if k in SKIP_SUBGENRES:
                continue
            density[k] = density.get(k, 0.0) + float(v)
    return density


# ---------------------------------------------------------------- candidate pool

def load_candidate_pool(pool_filter: Optional[List[str]] = None) -> List[Dict]:
    """Pull all library tracks with classifications (preferred) then unclassified.

    Each record: spotify_id, artist, title, duration_sec, tempo, genre, subgenre.
    Tempo is synthesized from subgenre midpoint when tracks.tempo is null.
    """
    sql = """
      SELECT t.spotify_id, t.artist, t.title, t.duration_ms, t.tempo,
             c.genre, c.subgenre
      FROM tracks t
      LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
    """
    with connect() as conn:
        rows = conn.execute(sql).fetchall()
    out: List[Dict] = []
    for r in rows:
        subgenre = r["subgenre"]
        if pool_filter and (not subgenre or subgenre not in pool_filter):
            continue
        if r["tempo"] is not None:
            tempo = float(r["tempo"])
        else:
            band = (SUBGENRE_BPM.get(subgenre) if subgenre else None) \
                   or (SUBGENRE_BPM.get(r["genre"]) if r["genre"] else None)
            tempo = (band[0] + band[1]) / 2.0 if band else None
        dur_ms = r["duration_ms"]
        dur_sec = int(dur_ms / 1000) if dur_ms else AVG_TRACK_SEC
        out.append({
            "spotify_id": r["spotify_id"],
            "artist":     r["artist"],
            "title":      r["title"],
            "duration_sec": dur_sec,
            "tempo":      tempo,
            "genre":      r["genre"],
            "subgenre":   subgenre,
        })
    return out


# ---------------------------------------------------------------- bucket logic

def build_buckets(template: Dict, target_sec: int
                  ) -> List[Dict]:
    """Return 20 buckets describing a target-shaped set of target_sec seconds.

    Each bucket has: start, end, target_bpm, target_density (subgenre->weight).
    """
    bpm_pts = interpolate_bpm(template["bpm_curve"] or [])
    density = dict(template.get("genre_density") or {})
    # Remove unclassified bucket so it doesn't pollute the mix.
    for k in SKIP_SUBGENRES:
        density.pop(k, None)
    if not density:
        density = dj_fallback_density(template["dj_slug"])
    density_total = sum(density.values()) or 1.0
    # If we still have nothing, seed a generic house-tech mix.
    if density_total == 1.0 and not density:
        density = {"tech house": 1.0, "deep house": 1.0}
        density_total = 2.0
    fallback_bpm = subgenre_median_bpm(density) or 124.0

    # Spread density proportionally across all buckets (simple uniform shape —
    # we trust the BPM curve to supply the dynamic contour).
    per_bucket_density = {
        k: (v / density_total) for k, v in density.items()
    }
    buckets: List[Dict] = []
    width = target_sec // NUM_BUCKETS
    for i in range(NUM_BUCKETS):
        t0 = i * width
        t1 = (i + 1) * width if i < NUM_BUCKETS - 1 else target_sec
        tgt_bpm = bpm_pts[i][1] if i < len(bpm_pts) else None
        if tgt_bpm is None:
            tgt_bpm = fallback_bpm
        buckets.append({
            "index":        i,
            "start":        t0,
            "end":          t1,
            "duration":     t1 - t0,
            "target_bpm":   float(tgt_bpm),
            "target_density": per_bucket_density,
        })
    return buckets


# ---------------------------------------------------------------- fit scoring

def family_of(sg: Optional[str]) -> Optional[str]:
    return FAMILY.get(sg) if sg else None


def subgenre_fit(track_sg: Optional[str], target_density: Dict[str, float]
                 ) -> float:
    """0..1 score for how well a track's subgenre matches the target mix.

    1.0  exact subgenre present in target
    0.6  same family as a target subgenre
    0.2  classified but unrelated
    0.0  unclassified (penalize so classified wins)
    """
    if not track_sg:
        return 0.0
    if track_sg in target_density:
        return 1.0
    tgt_families = {family_of(k) for k in target_density.keys()}
    if family_of(track_sg) in tgt_families and family_of(track_sg):
        return 0.6
    return 0.2


def bpm_fit(track_bpm: Optional[float], target_bpm: float) -> float:
    """0..1 score, 1.0 at exact match, 0 at >= 12 BPM away."""
    if track_bpm is None:
        return 0.0
    delta = abs(track_bpm - target_bpm)
    if delta >= 12:
        return 0.0
    return 1.0 - (delta / 12.0)


def score_track(track: Dict, bucket: Dict,
                used_artists: Dict[str, int]) -> float:
    bpm = bpm_fit(track["tempo"], bucket["target_bpm"])
    sg  = subgenre_fit(track["subgenre"], bucket["target_density"])
    # Penalize artist repeats in the same set (each prior appearance = -0.15).
    rep_pen = 0.15 * used_artists.get(track["artist"] or "", 0)
    return (0.55 * bpm) + (0.45 * sg) - rep_pen


# ---------------------------------------------------------------- greedy fill

def fill_bucket(bucket: Dict, pool: List[Dict],
                used_ids: set, used_artists: Dict[str, int],
                pace_sec: Optional[int],
                warnings: List[str]) -> List[Dict]:
    """Greedily pick tracks until the bucket duration is filled."""
    need = bucket["duration"]
    picks: List[Dict] = []
    # Pre-filter to BPM +/- tolerance.
    candidates = [
        t for t in pool
        if t["spotify_id"] not in used_ids
        and t["tempo"] is not None
        and abs(t["tempo"] - bucket["target_bpm"]) <= BPM_TOL
    ]
    if not candidates:
        warnings.append(
            f"bucket {bucket['index']:02d} ({bucket['target_bpm']:.0f} BPM): "
            f"no tracks within +/-{BPM_TOL:.0f} BPM; widening")
        candidates = [t for t in pool
                      if t["spotify_id"] not in used_ids
                      and t["tempo"] is not None]
        candidates.sort(key=lambda t: abs(t["tempo"] - bucket["target_bpm"]))
        candidates = candidates[:200]

    # Sort by fit score; pick top matches greedily with small randomness
    # to avoid always grabbing the same record at the top.
    scored = [(score_track(t, bucket, used_artists), t) for t in candidates]
    scored.sort(key=lambda x: -x[0])

    # Use pace_sec if provided — this caps tracks to appropriate chunks.
    # Otherwise use the full track duration.
    remaining = need
    top_k = min(len(scored), 25)
    picked_any = False
    while remaining > 0 and scored:
        # Draw from top-K for variety.
        top = scored[:top_k]
        if not top:
            break
        chosen = random.choice(top) if len(top) > 1 else top[0]
        score, track = chosen
        scored.remove(chosen)
        # Respect pace_sec as the effective slot length if set.
        slot = int(pace_sec) if pace_sec and pace_sec > 30 else track["duration_sec"]
        slot = min(slot, track["duration_sec"])
        picks.append({**track, "slot_sec": slot, "score": score})
        used_ids.add(track["spotify_id"])
        if track["artist"]:
            used_artists[track["artist"]] = used_artists.get(track["artist"], 0) + 1
        remaining -= slot
        picked_any = True
        if len(picks) >= 30:  # hard safety cap per bucket
            break
        # Stop early if remaining is less than half a slot - avoids big overshoot.
        if pace_sec and pace_sec > 30 and remaining < pace_sec / 2:
            break

    if not picked_any:
        warnings.append(f"bucket {bucket['index']:02d}: empty (no matches at all)")
    return picks


# ---------------------------------------------------------------- formatting

def fmt_mmss(secs: int) -> str:
    m, s = divmod(max(0, int(secs)), 60)
    return f"{m:>3d}:{s:02d}"


def format_plain(template: Dict, picks: List[Dict],
                 target_sec: int, warnings: List[str]) -> str:
    lines: List[str] = []
    mins = target_sec // 60
    role = template.get("role", "full")
    dj = template.get("dj_slug", "?")
    lines.append(
        f"GENERATED SET - {mins} min {role} shaped like {template['template_id']}")
    lines.append("-" * 65)

    density = template.get("genre_density") or {}
    total = sum(v for k, v in density.items() if k not in SKIP_SUBGENRES) or 1
    top = sorted(
        ((k, v) for k, v in density.items() if k not in SKIP_SUBGENRES),
        key=lambda x: -x[1],
    )[:6]
    mix = " / ".join(f"{k} {round(100*v/total)}%" for k, v in top)
    lines.append(f"DJ: {dj}   pace: {template.get('pace_sec_median') or '-'}s median")
    lines.append(f"Target mix: {mix or '(none)'}")

    bpm_pts = interpolate_bpm(template.get("bpm_curve") or [])
    if bpm_pts:
        sample_ix = [0, len(bpm_pts) // 3, 2 * len(bpm_pts) // 3, len(bpm_pts) - 1]
        sample = [bpm_pts[i][1] for i in sample_ix if bpm_pts[i][1] is not None]
        if sample:
            lines.append("BPM curve: " + " -> ".join(f"{b:.0f}" for b in sample))
    lines.append("")

    # Track table.
    cursor = 0
    for p in picks:
        ts = fmt_mmss(cursor)
        artist = (p["artist"] or "")[:28]
        title = (p["title"] or "")[:40]
        sg = p["subgenre"] or p.get("genre") or "-"
        bpm = f"{p['tempo']:.0f}" if p.get("tempo") else "?"
        lines.append(f"  {ts}  {artist:<28}  {title:<40}  "
                     f"{sg:<18}  {bpm:>3} BPM")
        cursor += p["slot_sec"]

    lines.append("")
    lines.append("STATS")
    subs_hit = {p["subgenre"] for p in picks if p["subgenre"]}
    tgt_subs = {k for k in density.keys() if k not in SKIP_SUBGENRES}
    lines.append(f"  subgenres hit: {len(subs_hit & tgt_subs)} / {len(tgt_subs)} "
                 f"from template")
    bpms = [p["tempo"] for p in picks if p.get("tempo")]
    if bpms and bpm_pts:
        # Mean deviation vs target curve at each track's position.
        devs = []
        cur = 0
        for p in picks:
            pos = min(NUM_BUCKETS - 1, int(cur * NUM_BUCKETS / max(1, target_sec)))
            tgt = bpm_pts[pos][1] if bpm_pts[pos][1] is not None else None
            if tgt is not None and p.get("tempo") is not None:
                devs.append(abs(tgt - p["tempo"]))
            cur += p["slot_sec"]
        if devs:
            lines.append(f"  BPM mean deviation: {sum(devs)/len(devs):.1f} BPM")
    lines.append(f"  tracks used: {len(picks)}")
    total_sec = sum(p["slot_sec"] for p in picks)
    lines.append(f"  total time: {fmt_mmss(total_sec)}  "
                 f"(target {fmt_mmss(target_sec)})")
    if warnings:
        lines.append("")
        lines.append("WARNINGS")
        for w in warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines)


def format_markdown(template: Dict, picks: List[Dict],
                    target_sec: int, warnings: List[str]) -> str:
    mins = target_sec // 60
    role = template.get("role", "full")
    out = [f"# Generated {mins} min {role} set",
           f"_shaped like `{template['template_id']}` ({template['dj_slug']})_",
           "",
           "| # | Time | Artist | Title | Subgenre | BPM |",
           "|---|------|--------|-------|----------|-----|"]
    cursor = 0
    for i, p in enumerate(picks, 1):
        ts = fmt_mmss(cursor)
        bpm = f"{p['tempo']:.0f}" if p.get("tempo") else "?"
        out.append(f"| {i} | {ts} | {p['artist']} | {p['title']} | "
                   f"{p['subgenre'] or '-'} | {bpm} |")
        cursor += p["slot_sec"]
    if warnings:
        out += ["", "## Warnings", *[f"- {w}" for w in warnings]]
    return "\n".join(out)


# ---------------------------------------------------------------- main

def generate(template_id: str, duration_min: Optional[int],
             pool_filter: Optional[List[str]], seed: Optional[int]
             ) -> Tuple[Dict, List[Dict], int, List[str]]:
    if seed is not None:
        random.seed(seed)

    template = load_template(template_id)
    if not template:
        raise SystemExit(f"template not found: {template_id}")

    target_sec = (duration_min * 60) if duration_min else template["duration_sec"]
    buckets = build_buckets(template, target_sec)
    pool = load_candidate_pool(pool_filter)
    if not pool:
        raise SystemExit("library pool is empty")

    picks: List[Dict] = []
    used_ids: set = set()
    used_artists: Dict[str, int] = {}
    warnings: List[str] = []
    pace_sec = template.get("pace_sec_median")

    for b in buckets:
        chunk = fill_bucket(b, pool, used_ids, used_artists, pace_sec, warnings)
        picks.extend(chunk)

    return template, picks, target_sec, warnings


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate a track sequence that matches a template shape.")
    ap.add_argument("--template", help="template_id (e.g. ePJzsMgWOy8:peak)")
    ap.add_argument("--like-dj", dest="like_dj",
                    help="DJ slug to pull a template from")
    ap.add_argument("--role", choices=["opener", "warmup", "peak",
                                        "closer", "full"],
                    help="role to pair with --like-dj")
    ap.add_argument("--duration", type=int,
                    help="override duration in minutes")
    ap.add_argument("--pool", help="restrict candidate pool to subgenres "
                                    "(comma-separated)")
    ap.add_argument("--md", dest="md_path",
                    help="write markdown output to this file")
    ap.add_argument("--seed", type=int, help="random seed for reproducibility")
    args = ap.parse_args()

    tid = resolve_template_id(args.template, args.like_dj, args.role)
    if not tid:
        print("error: supply --template OR --like-dj + --role", file=sys.stderr)
        sys.exit(2)

    pool_filter = None
    if args.pool:
        pool_filter = [p.strip() for p in args.pool.split(",") if p.strip()]

    template, picks, target_sec, warnings = generate(
        tid, args.duration, pool_filter, args.seed)

    plain = format_plain(template, picks, target_sec, warnings)
    print(plain)

    if args.md_path:
        md = format_markdown(template, picks, target_sec, warnings)
        with open(args.md_path, "w") as fh:
            fh.write(md + "\n")
        print(f"\nwrote markdown to {args.md_path}")


if __name__ == "__main__":
    main()
