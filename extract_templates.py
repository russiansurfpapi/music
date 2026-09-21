"""Extract BPM/energy/genre shape templates from fingerprinted DJ sets.

Each fingerprinted set (dj_sets.youtube_url IS NOT NULL) with >=10 timestamped
tracks is broken into 5 role-segmented templates (full, opener, warmup, peak,
closer) and persisted to the set_templates table. A future set-generator can
load these templates via load_template(template_id) and match candidate track
sequences against the stored bpm/energy/genre curves.

Shape of each template row:
  - bpm_curve:     JSON [[sec, bpm_or_null], ...]   (20 buckets)
  - energy_curve:  JSON [[sec, energy_or_null], ...] (20 buckets, 0..1)
  - genre_density: JSON {subgenre: seconds_covered}
  - pace_sec_median, edit_density, venue_hint

Usage:
  python3 extract_templates.py --all
  python3 extract_templates.py --set <set_id>
  python3 extract_templates.py --list
"""

import argparse
import json
import re
import statistics
from typing import Dict, List, Optional, Tuple

from db import connect

try:
    from set_energy import TEXTURE_ENERGY
except Exception:
    # Fallback copy if the import surface changes.
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

try:
    from transition_atlas import SUBGENRE_BPM
except Exception:
    SUBGENRE_BPM = {}


ROLES: List[Tuple[str, float, float]] = [
    # (name, start_frac, end_frac)
    ("full",    0.0, 1.0),
    ("opener",  0.0, 0.2),
    ("warmup",  0.2, 0.5),
    ("peak",    0.5, 0.8),
    ("closer",  0.8, 1.0),
]

VENUE_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("boiler room",  re.compile(r"boiler\s*room",      re.I)),
    ("space miami",  re.compile(r"space\s*miami",      re.I)),
    ("burning man",  re.compile(r"burning\s*man",      re.I)),
    ("dekmantel",    re.compile(r"dekmantel",          re.I)),
    ("bittersweet",  re.compile(r"bitter\s*sweet",     re.I)),
    ("csides",       re.compile(r"c\s*sides|cside|csidess?", re.I)),
    ("armory",       re.compile(r"armory",             re.I)),
    ("boat party",   re.compile(r"boat\s*party",       re.I)),
]


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS set_templates (
  template_id        TEXT PRIMARY KEY,
  source_set_id      TEXT NOT NULL,
  dj_slug            TEXT NOT NULL,
  role               TEXT NOT NULL,
  start_sec          INTEGER NOT NULL,
  end_sec            INTEGER NOT NULL,
  duration_sec       INTEGER NOT NULL,
  bpm_curve          TEXT,
  energy_curve       TEXT,
  genre_density      TEXT,
  pace_sec_median    INTEGER,
  edit_density       REAL,
  venue_hint         TEXT,
  created_at         TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (source_set_id) REFERENCES dj_sets(set_id)
);
"""


# --------------------------------------------------------------------- helpers

def ensure_table(conn) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()


def bpm_from_subgenre(sg: Optional[str], genre: Optional[str]) -> Optional[float]:
    for key in (sg, genre):
        if not key:
            continue
        band = SUBGENRE_BPM.get(key)
        if band:
            return (band[0] + band[1]) / 2.0
    return None


def texture_energy(texture_json: Optional[str]) -> Optional[float]:
    if not texture_json:
        return None
    try:
        tex = json.loads(texture_json)
    except Exception:
        return None
    vals = [TEXTURE_ENERGY[t] for t in tex if t in TEXTURE_ENERGY]
    if not vals:
        return None
    return sum(vals) / len(vals)


def parse_venue(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    for label, pat in VENUE_PATTERNS:
        if pat.search(title):
            return label
    return None


def load_set(conn, set_id: str) -> Optional[Dict]:
    meta = conn.execute(
        """SELECT set_id, dj_slug, title, set_date, duration_sec, youtube_url
           FROM dj_sets WHERE set_id = ?""",
        (set_id,),
    ).fetchone()
    if not meta:
        return None
    tracks = conn.execute(
        """SELECT t.position, t.timestamp_sec, t.spotify_id,
                  tr.tempo,
                  c.genre, c.subgenre, c.texture
           FROM dj_set_tracks t
           LEFT JOIN tracks tr ON tr.spotify_id = t.spotify_id
           LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
           WHERE t.set_id = ? AND t.timestamp_sec IS NOT NULL
           ORDER BY t.timestamp_sec""",
        (set_id,),
    ).fetchall()
    chunks = conn.execute(
        """SELECT chunk_idx, timestamp_sec, matched, bpm, rms
           FROM dj_set_chunks WHERE set_id = ? ORDER BY timestamp_sec""",
        (set_id,),
    ).fetchall()
    return {
        "meta": dict(meta),
        "tracks": [dict(r) for r in tracks],
        "chunks": [dict(c) for c in chunks],
    }


def normalized_rms(chunks: List[Dict]) -> Dict[int, float]:
    """Return a chunk_idx -> normalized RMS (0..1) map for the whole set."""
    vals = [(c["chunk_idx"], c["rms"]) for c in chunks if c.get("rms") is not None]
    if not vals:
        return {}
    rms_only = [v for _, v in vals]
    lo, hi = min(rms_only), max(rms_only)
    if hi == lo:
        return {idx: 0.5 for idx, _ in vals}
    return {idx: (v - lo) / (hi - lo) for idx, v in vals}


# -------------------------------------------------------------- curve builders

def build_bpm_curve(start: int, end: int, tracks: List[Dict],
                    chunks: List[Dict], buckets: int = 20) -> List[List]:
    width = max(1, (end - start) // buckets)
    out: List[List] = []
    for i in range(buckets):
        t0 = start + i * width
        t1 = start + (i + 1) * width if i < buckets - 1 else end
        bpms: List[float] = []
        # 1) librosa BPM from chunks if present
        for c in chunks:
            ts = c.get("timestamp_sec")
            if ts is None or c.get("bpm") is None:
                continue
            if t0 <= ts < t1:
                bpms.append(float(c["bpm"]))
        # 2) tracks.tempo at track timestamps in window
        if not bpms:
            for t in tracks:
                ts = t.get("timestamp_sec")
                if ts is None or t.get("tempo") is None:
                    continue
                if t0 <= ts < t1:
                    bpms.append(float(t["tempo"]))
        # 3) subgenre midpoint for tracks in window
        if not bpms:
            for t in tracks:
                ts = t.get("timestamp_sec")
                if ts is None:
                    continue
                if t0 <= ts < t1:
                    mid = bpm_from_subgenre(t.get("subgenre"), t.get("genre"))
                    if mid is not None:
                        bpms.append(mid)
        avg = sum(bpms) / len(bpms) if bpms else None
        out.append([t0, round(avg, 2) if avg is not None else None])
    return out


def build_energy_curve(start: int, end: int, tracks: List[Dict],
                       chunks: List[Dict], rms_norm: Dict[int, float],
                       buckets: int = 20) -> List[List]:
    width = max(1, (end - start) // buckets)
    out: List[List] = []
    for i in range(buckets):
        t0 = start + i * width
        t1 = start + (i + 1) * width if i < buckets - 1 else end
        vals: List[float] = []
        # Prefer normalized RMS from chunks
        for c in chunks:
            ts = c.get("timestamp_sec")
            if ts is None:
                continue
            if t0 <= ts < t1 and c["chunk_idx"] in rms_norm:
                vals.append(rms_norm[c["chunk_idx"]])
        # Fall back to texture-derived energy at track timestamps
        if not vals:
            for t in tracks:
                ts = t.get("timestamp_sec")
                if ts is None:
                    continue
                if t0 <= ts < t1:
                    e = texture_energy(t.get("texture"))
                    if e is not None:
                        vals.append(e)
        avg = sum(vals) / len(vals) if vals else None
        out.append([t0, round(avg, 3) if avg is not None else None])
    return out


def build_genre_density(start: int, end: int, tracks: List[Dict],
                        duration_sec: int) -> Dict[str, int]:
    """subgenre (fallback genre) -> seconds covered in [start, end)."""
    density: Dict[str, float] = {}
    # Compute playtime per track using next-track timestamp
    for i, t in enumerate(tracks):
        ts = t.get("timestamp_sec")
        if ts is None:
            continue
        nxt = (tracks[i + 1]["timestamp_sec"] if i + 1 < len(tracks)
               else duration_sec)
        if nxt is None:
            nxt = duration_sec
        play_start, play_end = ts, max(ts, nxt)
        overlap = max(0, min(end, play_end) - max(start, play_start))
        if overlap <= 0:
            continue
        key = t.get("subgenre") or t.get("genre") or "(unclassified)"
        density[key] = density.get(key, 0.0) + overlap
    return {k: int(round(v)) for k, v in density.items()}


def compute_pace_median(start: int, end: int, tracks: List[Dict]) -> Optional[int]:
    window_ts = [t["timestamp_sec"] for t in tracks
                 if t.get("timestamp_sec") is not None
                 and start <= t["timestamp_sec"] < end]
    if len(window_ts) < 2:
        return None
    gaps = [b - a for a, b in zip(window_ts, window_ts[1:])]
    return int(statistics.median(gaps))


def compute_edit_density(start: int, end: int, chunks: List[Dict]) -> Optional[float]:
    if not chunks:
        return None
    window = [c for c in chunks
              if c.get("timestamp_sec") is not None
              and start <= c["timestamp_sec"] < end]
    if not window:
        return None
    unmatched = sum(1 for c in window if not c.get("matched"))
    return unmatched / len(window)


# ----------------------------------------------------------- template writer

def build_templates_for_set(conn, set_id: str) -> List[Dict]:
    data = load_set(conn, set_id)
    if not data:
        return []
    meta = data["meta"]
    tracks = data["tracks"]
    chunks = data["chunks"]

    if len(tracks) < 10:
        return []

    duration = meta.get("duration_sec")
    if not duration:
        # Fall back to the last track's timestamp + 3 min pad
        if tracks:
            duration = tracks[-1]["timestamp_sec"] + 180
        else:
            return []

    venue_hint = parse_venue(meta.get("title"))
    rms_norm = normalized_rms(chunks)
    rows = []

    for role, s_frac, e_frac in ROLES:
        start = int(round(duration * s_frac))
        end = int(round(duration * e_frac))
        if end <= start:
            continue
        bpm_curve = build_bpm_curve(start, end, tracks, chunks)
        energy_curve = build_energy_curve(start, end, tracks, chunks, rms_norm)
        genre_density = build_genre_density(start, end, tracks, duration)
        pace_med = compute_pace_median(start, end, tracks)
        edit_den = compute_edit_density(start, end, chunks)
        rows.append({
            "template_id":      f"{set_id}:{role}",
            "source_set_id":    set_id,
            "dj_slug":          meta["dj_slug"],
            "role":             role,
            "start_sec":        start,
            "end_sec":          end,
            "duration_sec":     end - start,
            "bpm_curve":        json.dumps(bpm_curve),
            "energy_curve":     json.dumps(energy_curve),
            "genre_density":    json.dumps(genre_density),
            "pace_sec_median":  pace_med,
            "edit_density":     edit_den,
            "venue_hint":       venue_hint,
        })
    return rows


def upsert_templates(conn, rows: List[Dict]) -> int:
    if not rows:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO set_templates
           (template_id, source_set_id, dj_slug, role, start_sec, end_sec,
            duration_sec, bpm_curve, energy_curve, genre_density,
            pace_sec_median, edit_density, venue_hint)
           VALUES (:template_id, :source_set_id, :dj_slug, :role,
                   :start_sec, :end_sec, :duration_sec, :bpm_curve,
                   :energy_curve, :genre_density, :pace_sec_median,
                   :edit_density, :venue_hint)""",
        rows,
    )
    conn.commit()
    return len(rows)


# -------------------------------------------------------------------- loader

def load_template(template_id: str) -> Optional[Dict]:
    """Fetch a template by id and return a dict with JSON fields parsed.

    Future set_generator.py imports this directly.
    """
    with connect() as conn:
        ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM set_templates WHERE template_id = ?",
            (template_id,),
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    for field in ("bpm_curve", "energy_curve", "genre_density"):
        if out.get(field):
            try:
                out[field] = json.loads(out[field])
            except Exception:
                pass
    return out


# ------------------------------------------------------------------------ cli

def cmd_all(conn) -> None:
    ensure_table(conn)
    fingerprinted = conn.execute(
        """SELECT set_id FROM dj_sets
           WHERE youtube_url IS NOT NULL
           ORDER BY set_id"""
    ).fetchall()

    total_rows = 0
    sets_with_templates = 0
    djs: set = set()
    skipped: List[Tuple[str, str]] = []

    for r in fingerprinted:
        set_id = r["set_id"]
        rows = build_templates_for_set(conn, set_id)
        if not rows:
            skipped.append((set_id, "fewer than 10 timestamped tracks"))
            continue
        upsert_templates(conn, rows)
        total_rows += len(rows)
        sets_with_templates += 1
        djs.add(rows[0]["dj_slug"])

    print(f"{total_rows} templates across {sets_with_templates} sets "
          f"/ {len(djs)} DJs")
    if skipped:
        print("\nSkipped:")
        for sid, reason in skipped:
            print(f"  {sid}  ({reason})")


def cmd_set(conn, set_id: str) -> None:
    ensure_table(conn)
    rows = build_templates_for_set(conn, set_id)
    if not rows:
        print(f"No templates generated for {set_id} "
              "(missing set, <10 timestamps, or unknown duration).")
        return
    upsert_templates(conn, rows)
    print(f"Wrote {len(rows)} templates for {set_id}:")
    for r in rows:
        print(f"  {r['template_id']:<48}  {r['role']:<7}  "
              f"{r['duration_sec']}s  venue={r['venue_hint'] or '-'}")


def cmd_list(conn) -> None:
    ensure_table(conn)
    rows = conn.execute(
        """SELECT template_id, role, duration_sec, dj_slug, venue_hint
           FROM set_templates
           ORDER BY dj_slug, source_set_id, role"""
    ).fetchall()
    if not rows:
        print("No templates yet. Run with --all or --set <id>.")
        return
    print(f"{'template_id':<48}  {'role':<7}  {'dur':>5}  "
          f"{'dj':<24}  venue")
    print("-" * 100)
    for r in rows:
        print(f"{r['template_id']:<48}  {r['role']:<7}  "
              f"{r['duration_sec']:>5}  {r['dj_slug']:<24}  "
              f"{r['venue_hint'] or '-'}")
    print(f"\n{len(rows)} templates total")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Extract shape templates from fingerprinted DJ sets.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true",
                   help="Extract templates for every fingerprinted set.")
    g.add_argument("--set", dest="set_id",
                   help="Extract templates for a single set_id.")
    g.add_argument("--list", action="store_true",
                   help="List templates currently stored.")
    args = ap.parse_args()

    with connect() as conn:
        if args.all:
            cmd_all(conn)
        elif args.set_id:
            cmd_set(conn, args.set_id)
        elif args.list:
            cmd_list(conn)


if __name__ == "__main__":
    main()
