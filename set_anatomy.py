"""Timestamp-aware anatomy report for a fingerprinted DJ set.

Uses the data that YouTube fingerprinting uniquely provides — actual
timestamps for each identified track — to compute:

  - Per-track playtime (estimated from next-timestamp gap)
  - Dominant tracks (ranked by playtime, not just presence)
  - Transition speed histogram (fast / medium / slow / lingering)
  - ID-gap % (fraction of set where fingerprinter found nothing)
  - BPM curve (exact from dj_set_chunks if present, else from tracks.tempo)
  - Genre / subgenre density (time-weighted minutes, not counts)
  - Timeline strip (1 char per 2-minute window, subgenre-coded)

For sets with a `dj_set_chunks` row (future identify runs with --record-chunks
or librosa pass), ID-gap % and BPM curve are exact. For older sets, they're
estimated from deduped track rows.

Usage:
    python3 set_anatomy.py --set <set_id>
    python3 set_anatomy.py --dj <slug>           # all fingerprinted sets by DJ
"""

import argparse
import os
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from db import connect
from set_shapes import code_for, FAM, GEN, FAMILY


# ----------------------------- data ------------------------------

def load_set(conn, set_id: str) -> Optional[Dict]:
    meta = conn.execute(
        """SELECT set_id, dj_slug, title, set_date, duration_sec,
                  track_count, youtube_url, source_file
           FROM dj_sets WHERE set_id = ?""", (set_id,)
    ).fetchone()
    if not meta:
        return None
    tracks = conn.execute(
        """SELECT t.position, t.timestamp_sec, t.raw_artist, t.raw_title,
                  t.source, t.confidence, t.spotify_id,
                  tr.tempo, c.genre, c.subgenre, c.rhythm
           FROM dj_set_tracks t
           LEFT JOIN tracks tr ON tr.spotify_id = t.spotify_id
           LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
           WHERE t.set_id = ? ORDER BY t.position""", (set_id,)
    ).fetchall()
    chunks = conn.execute(
        """SELECT chunk_idx, timestamp_sec, matched, bpm, rms, key
           FROM dj_set_chunks WHERE set_id = ? ORDER BY chunk_idx""",
        (set_id,)
    ).fetchall()
    return {
        "meta": dict(meta),
        "tracks": [dict(r) for r in tracks],
        "chunks": [dict(r) for r in chunks],
    }


# ----------------------------- compute ------------------------------

def compute_playtimes(tracks: List[Dict], duration: int) -> List[int]:
    """Estimate playtime per track = next.timestamp - this.timestamp (last = to end)."""
    if not tracks: return []
    out = []
    for i, t in enumerate(tracks):
        if i < len(tracks) - 1:
            out.append(tracks[i+1]["timestamp_sec"] - t["timestamp_sec"])
        else:
            out.append(duration - t["timestamp_sec"] if duration else 0)
    return [max(0, x) for x in out]


def transition_bucket(sec: int) -> str:
    if sec < 60:   return "fast (<1m)"
    if sec < 180:  return "medium (1-3m)"
    if sec < 360:  return "slow (3-6m)"
    return "lingering (>6m)"


def id_gap_pct(chunks: List[Dict], tracks: List[Dict], duration: int) -> Tuple[float, str]:
    """Exact from chunks table; approximate from dedupe gaps if no chunks."""
    if chunks:
        total = len(chunks)
        matched = sum(1 for c in chunks if c["matched"])
        return (1 - matched/total) * 100, "exact"
    # Approximate: if first matched timestamp > 0, count leading silence;
    # if a playtime is >5min it's likely a dead zone + a track.
    # For now: use the deduped-track-count vs expected-track-count heuristic.
    # If step=45s, expected_chunks = duration/45. If we saw N unique tracks
    # averaging ~3min playtime, covered = 3min * N; gap = 1 - covered/duration.
    if not tracks or not duration:
        return 0.0, "unknown"
    # Rough: unidentified fraction = 1 - (sum-of-playtimes capped) / duration
    # But playtimes already include unidentified pockets between matched rows.
    # So this underestimates the gap. Use chunks-table for accuracy.
    pt = compute_playtimes(tracks, duration)
    # Any stretch longer than 4x median playtime is "probably contains a gap"
    if not pt: return 0.0, "unknown"
    pt_sorted = sorted(pt)
    median = pt_sorted[len(pt_sorted)//2]
    long_tail = sum(p - median*2 for p in pt if p > median*4)
    return min(100, long_tail/duration*100), "estimated"


def genre_density(tracks: List[Dict], playtimes: List[int]) -> List[Tuple[str, int, float]]:
    """Return (subgenre_or_genre, seconds, pct) ordered by seconds desc."""
    bucket = Counter()
    total = 0
    for t, pt in zip(tracks, playtimes):
        key = t.get("subgenre") or t.get("genre") or "(unclassified)"
        bucket[key] += pt
        total += pt
    if total == 0: return []
    return sorted(
        [(k, v, v/total*100) for k, v in bucket.items()],
        key=lambda x: -x[1]
    )


def bpm_curve(chunks: List[Dict], tracks: List[Dict], duration: int,
              buckets: int = 20) -> List[Tuple[int, Optional[float]]]:
    """Return [(minute_start, bpm_or_None)] across the set."""
    bw = max(1, duration // buckets) if duration else 60
    out = []
    for i in range(buckets):
        t0 = i * bw
        t1 = t0 + bw
        bpms = []
        # Prefer per-chunk BPM from librosa pass
        for c in chunks:
            if t0 <= c["timestamp_sec"] < t1 and c.get("bpm"):
                bpms.append(c["bpm"])
        # Fallback to tracks.tempo weighted by presence in window
        if not bpms:
            for t in tracks:
                if t0 <= t["timestamp_sec"] < t1 and t.get("tempo"):
                    bpms.append(t["tempo"])
        out.append((t0, sum(bpms)/len(bpms) if bpms else None))
    return out


def timeline_strip(tracks: List[Dict], playtimes: List[int], duration: int,
                   width: int = 60) -> str:
    """Build 60-char strip where each char ≈ duration/60 seconds of set."""
    if not duration: return ""
    sec_per_char = duration / width
    chars = ["."] * width
    for t, pt in zip(tracks, playtimes):
        start = int(t["timestamp_sec"] / sec_per_char)
        end = min(width, int((t["timestamp_sec"] + pt) / sec_per_char) + 1)
        c = code_for(t.get("subgenre") or "", t.get("genre") or "")
        for i in range(start, end):
            if 0 <= i < width:
                chars[i] = c
    return "".join(chars)


# ----------------------------- render ------------------------------

def fmt_time(s: int) -> str:
    h, r = divmod(int(s), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render_set(data: Dict) -> str:
    m = data["meta"]
    tracks = data["tracks"]
    chunks = data["chunks"]
    duration = m.get("duration_sec") or 0
    pt = compute_playtimes(tracks, duration)

    lines = []
    lines.append("═" * 72)
    lines.append(f"  {m.get('title') or m['set_id']}")
    lines.append(f"  DJ: {m['dj_slug']}   Date: {m.get('set_date') or '—'}   "
                 f"Duration: {fmt_time(duration)}   Tracks: {len(tracks)}")
    if m.get("youtube_url"):
        lines.append(f"  YouTube: {m['youtube_url']}")
    lines.append("═" * 72)

    # 1) Top tracks by playtime
    lines.append("\n▣ DOMINANT TRACKS (by estimated playtime)")
    ranked = sorted(zip(tracks, pt), key=lambda x: -x[1])[:10]
    for t, p in ranked:
        sg = t.get("subgenre") or t.get("genre") or "—"
        lines.append(f"    {fmt_time(p):>6}  {t['raw_artist'][:30]:<30}  "
                     f"{(t['raw_title'] or '')[:40]:<40}  [{sg}]")

    # 2) Transition speed histogram
    lines.append("\n▣ TRANSITION PACE")
    buckets = Counter(transition_bucket(p) for p in pt)
    for b in ["fast (<1m)", "medium (1-3m)", "slow (3-6m)", "lingering (>6m)"]:
        n = buckets.get(b, 0)
        bar = "█" * n
        lines.append(f"    {b:<18} {n:>3}  {bar}")

    # 3) ID-gap
    gap_pct, how = id_gap_pct(chunks, tracks, duration)
    lines.append(f"\n▣ ID-GAP  (fingerprinter returned no match)")
    lines.append(f"    {gap_pct:.0f}% of set unidentified ({how})")
    if how == "estimated":
        lines.append("    (run `compute_audio_features.py --set "
                     f"{m['set_id']}` for exact gap)")

    # 4) Genre/subgenre density
    density = genre_density(tracks, pt)
    if density and density[0][0] != "(unclassified)" or len(density) > 1:
        lines.append("\n▣ SUBGENRE DENSITY (time-weighted)")
        for name, sec, pct in density[:8]:
            bar = "█" * int(pct / 3)
            lines.append(f"    {name[:22]:<22} {fmt_time(sec):>6}  "
                         f"{pct:>4.0f}%  {bar}")
    else:
        lines.append("\n▣ SUBGENRE DENSITY: (run bridge_fingerprinted.sh to classify)")

    # 5) BPM curve
    curve = bpm_curve(chunks, tracks, duration)
    if any(b for _, b in curve):
        lines.append("\n▣ BPM CURVE  (per-window avg)")
        for t0, bpm in curve:
            if bpm is None:
                lines.append(f"    {fmt_time(t0):>6}  —")
            else:
                bar = "▁▂▃▄▅▆▇█"[min(7, max(0, int((bpm - 90) / 5)))]
                lines.append(f"    {fmt_time(t0):>6}  {bpm:>5.1f}  {bar}")

    # 6) Timeline strip
    if duration:
        strip = timeline_strip(tracks, pt, duration)
        lines.append("\n▣ TIMELINE  (each char ≈ "
                     f"{duration//60:.0f}s × 1/60 of set ≈ "
                     f"{duration/60:.0f}m total)")
        lines.append(f"    0{'─'*28}{fmt_time(duration//2)}{'─'*28}{fmt_time(duration)}")
        lines.append(f"    {strip}")

    return "\n".join(lines)


def render_compare(datas: List[Dict]) -> str:
    """Side-by-side summary for N fingerprinted sets."""
    lines = []
    lines.append("═" * 88)
    lines.append(f"  COMPARE {len(datas)} sets")
    lines.append("═" * 88)

    headers = []
    rows_dur, rows_tracks, rows_dj, rows_date = [], [], [], []
    rows_dom, rows_dom2, rows_dom3 = [], [], []
    rows_pace_fast, rows_pace_med, rows_pace_slow, rows_pace_ling = [], [], [], []
    rows_idgap, rows_top_genre = [], []
    rows_strip = []

    for d in datas:
        m = d["meta"]
        tracks = d["tracks"]
        chunks = d["chunks"]
        duration = m.get("duration_sec") or 0
        pt = compute_playtimes(tracks, duration)

        label = (m.get("title") or m["set_id"])[:20]
        headers.append(label)
        rows_dj.append(m["dj_slug"][:20])
        rows_date.append((m.get("set_date") or "—")[:10])
        rows_dur.append(fmt_time(duration))
        rows_tracks.append(str(len(tracks)))

        ranked = sorted(zip(tracks, pt), key=lambda x: -x[1])[:3]
        doms = [f"{t['raw_artist'][:14]} / {fmt_time(p)}" for t, p in ranked]
        while len(doms) < 3: doms.append("—")
        rows_dom.append(doms[0]); rows_dom2.append(doms[1]); rows_dom3.append(doms[2])

        buckets = Counter(transition_bucket(p) for p in pt)
        n = max(len(pt), 1)
        rows_pace_fast.append(f"{buckets.get('fast (<1m)',0)*100//n}%")
        rows_pace_med.append(f"{buckets.get('medium (1-3m)',0)*100//n}%")
        rows_pace_slow.append(f"{buckets.get('slow (3-6m)',0)*100//n}%")
        rows_pace_ling.append(f"{buckets.get('lingering (>6m)',0)*100//n}%")

        gap, _ = id_gap_pct(chunks, tracks, duration)
        rows_idgap.append(f"{gap:.0f}%")

        density = genre_density(tracks, pt)
        top = density[0][0][:20] if density else "—"
        rows_top_genre.append(top)

        rows_strip.append(timeline_strip(tracks, pt, duration, width=28))

    def row(label, vals):
        return f"  {label:<22}  " + "  ".join(f"{v:<22}" for v in vals)

    lines.append("")
    lines.append(row("", headers))
    lines.append("  " + "─" * 86)
    lines.append(row("DJ", rows_dj))
    lines.append(row("Date", rows_date))
    lines.append(row("Duration", rows_dur))
    lines.append(row("Tracks", rows_tracks))
    lines.append("")
    lines.append(row("Dominant #1", rows_dom))
    lines.append(row("Dominant #2", rows_dom2))
    lines.append(row("Dominant #3", rows_dom3))
    lines.append("")
    lines.append(row("Pace: fast (<1m)", rows_pace_fast))
    lines.append(row("Pace: medium", rows_pace_med))
    lines.append(row("Pace: slow", rows_pace_slow))
    lines.append(row("Pace: lingering", rows_pace_ling))
    lines.append("")
    lines.append(row("ID-gap %", rows_idgap))
    lines.append(row("Top subgenre", rows_top_genre))
    lines.append("")
    lines.append("  Timeline strips:")
    for h, s in zip(headers, rows_strip):
        lines.append(f"    {h:<22} {s}")

    return "\n".join(lines)


# ----------------------------- cli ------------------------------

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--set", dest="set_id")
    g.add_argument("--dj", dest="dj_slug")
    g.add_argument("--compare", help="comma-separated set_ids to compare side-by-side")
    args = ap.parse_args()

    with connect() as conn:
        conn.row_factory = __import__("sqlite3").Row
        if args.compare:
            ids = [s.strip() for s in args.compare.split(",")]
            datas = [load_set(conn, sid) for sid in ids]
            missing = [sid for sid, d in zip(ids, datas) if not d]
            if missing:
                print(f"no sets found for: {missing}"); return
            print(render_compare(datas))
        elif args.set_id:
            data = load_set(conn, args.set_id)
            if not data:
                print(f"no set with id={args.set_id}"); return
            print(render_set(data))
        else:
            set_ids = [r["set_id"] for r in conn.execute(
                """SELECT set_id FROM dj_sets WHERE dj_slug=? AND youtube_url IS NOT NULL
                   ORDER BY set_date DESC""", (args.dj_slug,)
            ).fetchall()]
            if not set_ids:
                print(f"no fingerprinted sets for dj={args.dj_slug}"); return
            for sid in set_ids:
                print(render_set(load_set(conn, sid)))
                print()


if __name__ == "__main__":
    main()
