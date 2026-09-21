"""Emulation scorecard — how closely does a candidate set match a reference DJ's archetype?

Usage:
    python3 emulation_score.py --set <set_id> --like-dj <ref_slug>
    python3 emulation_score.py --csv tracks.csv --like-dj <ref_slug>
    python3 emulation_score.py --set <set_id> --like-template <template_id>
    python3 emulation_score.py --set <set_id> --like-dj <ref_slug> --md feedback.md

CSV format: position,timestamp_sec,artist,title,spotify_id  (header row required)
"""

import argparse
import csv
import json
import math
import os
import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from db import connect
from dj_profiles import gather, _entropy, _runs, _list
from dj_archetype import compute_axes, _fp_axes


# ---------------------------------------------------------------------------
# Candidate-set axis computation (mirrors dj_archetype.compute_axes but on
# an in-memory set-of-tracks rather than a DB-wide dj_slug lookup).
# ---------------------------------------------------------------------------

def _load_candidate_from_set(conn, set_id: str) -> Dict:
    """Pull tracks + timestamps for a single set_id; enrich with classifications."""
    meta = conn.execute(
        "SELECT set_id, dj_slug, title, duration_sec, track_count FROM dj_sets WHERE set_id = ?",
        (set_id,),
    ).fetchone()
    if not meta:
        raise SystemExit(f"No set '{set_id}' in dj_sets")

    rows = conn.execute(
        """
        SELECT t.position, t.timestamp_sec, t.raw_artist, t.raw_title, t.spotify_id,
               c.genre, c.subgenre, c.rhythm, c.production_dna, c.texture, c.lineage,
               tr.release_year
        FROM dj_set_tracks t
        LEFT JOIN classifications c ON c.spotify_id = t.spotify_id
        LEFT JOIN tracks tr ON tr.spotify_id = t.spotify_id
        WHERE t.set_id = ? ORDER BY t.position
        """,
        (set_id,),
    ).fetchall()

    raw_plays = []
    classified = []
    timestamps = []
    for r in rows:
        raw_plays.append({
            "set_id": set_id,
            "position": r["position"],
            "raw_artist": r["raw_artist"],
            "raw_title": r["raw_title"],
            "spotify_id": r["spotify_id"],
        })
        if r["timestamp_sec"] is not None:
            timestamps.append(r["timestamp_sec"])
        if r["genre"]:
            classified.append({
                "set_id": set_id,
                "pos": r["position"],
                "genre": r["genre"],
                "subgenre": r["subgenre"] or r["genre"],
                "rhythm": r["rhythm"],
                "dna": _list(r["production_dna"]),
                "texture": _list(r["texture"]),
                "lineage": _list(r["lineage"]),
                "year": r["release_year"],
            })

    return {
        "label": set_id,
        "dj_hint": meta["dj_slug"],
        "title": meta["title"],
        "duration_sec": meta["duration_sec"],
        "n_tracks_total": len(rows),
        "sets": [set_id],
        "tracks": classified,
        "raw_plays": raw_plays,
        "timestamps": timestamps,
    }


def _load_candidate_from_csv(conn, path: str) -> Dict:
    """Load CSV (position,timestamp_sec,artist,title,spotify_id). Enrich from DB."""
    raw_plays, classified, timestamps = [], [], []
    set_id = "csv:" + os.path.basename(path)
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            pos_s = (r.get("position") or "").strip()
            ts_s = (r.get("timestamp_sec") or "").strip()
            artist = (r.get("artist") or "").strip()
            title = (r.get("title") or "").strip()
            sid = (r.get("spotify_id") or "").strip() or None
            try:
                pos = int(pos_s) if pos_s else len(raw_plays) + 1
            except ValueError:
                pos = len(raw_plays) + 1
            ts = None
            if ts_s:
                try:
                    ts = int(float(ts_s))
                except ValueError:
                    ts = None
            raw_plays.append({
                "set_id": set_id,
                "position": pos,
                "raw_artist": artist,
                "raw_title": title,
                "spotify_id": sid,
            })
            if ts is not None:
                timestamps.append(ts)
            if sid:
                cr = conn.execute(
                    """
                    SELECT c.genre, c.subgenre, c.rhythm, c.production_dna, c.texture,
                           c.lineage, tr.release_year
                    FROM classifications c
                    LEFT JOIN tracks tr ON tr.spotify_id = c.spotify_id
                    WHERE c.spotify_id = ?
                    """,
                    (sid,),
                ).fetchone()
                if cr and cr["genre"]:
                    classified.append({
                        "set_id": set_id,
                        "pos": pos,
                        "genre": cr["genre"],
                        "subgenre": cr["subgenre"] or cr["genre"],
                        "rhythm": cr["rhythm"],
                        "dna": _list(cr["production_dna"]),
                        "texture": _list(cr["texture"]),
                        "lineage": _list(cr["lineage"]),
                        "year": cr["release_year"],
                    })
    return {
        "label": set_id,
        "dj_hint": None,
        "title": os.path.basename(path),
        "duration_sec": max(timestamps) + 60 if timestamps else None,
        "n_tracks_total": len(raw_plays),
        "sets": [set_id],
        "tracks": classified,
        "raw_plays": raw_plays,
        "timestamps": timestamps,
    }


def _axes_from_candidate(cand: Dict, dj_slug_for_own: Optional[str]) -> Dict:
    """Compute axes on in-memory candidate data. Mirrors compute_axes() shape."""
    tracks = cand["tracks"]
    raw_plays = cand["raw_plays"]
    total_raw = len(raw_plays)

    # BREADTH
    sub_c = Counter(t["subgenre"] for t in tracks)
    subgenre_entropy = _entropy(sub_c)
    by_set = defaultdict(list)
    for t in tracks:
        by_set[t["set_id"]].append(t["subgenre"])
    distinct_per_set = [len(set(v)) for v in by_set.values()]
    median_distinct = int(statistics.median(distinct_per_set)) if distinct_per_set else 0

    # FLOW
    all_runs = []
    for sid, subs in by_set.items():
        all_runs.extend(_runs(subs))
    median_run = int(statistics.median(all_runs)) if all_runs else 0

    # ANCHOR LOYALTY
    play_counts: Counter = Counter()
    for p in raw_plays:
        key = (p["spotify_id"] or (p["raw_artist"] + "||" + p["raw_title"])).lower()
        play_counts[key] += 1
    anchor_plays = sum(c for c in play_counts.values() if c >= 2)
    anchor_ratio = anchor_plays / total_raw if total_raw else 0

    # OWN-TRACK RATIO
    if dj_slug_for_own:
        dj_tokens = [t for t in dj_slug_for_own.split("-") if len(t) > 2]
    else:
        dj_tokens = []
    own_plays = 0
    for p in raw_plays:
        artist = (p["raw_artist"] or "").lower()
        if dj_tokens and any(tok in artist for tok in dj_tokens):
            own_plays += 1
    own_ratio = own_plays / total_raw if total_raw else 0

    # ERA
    years = [t["year"] for t in tracks if t.get("year")]
    median_year = int(statistics.median(years)) if years else None
    year_range = (min(years), max(years)) if years else (None, None)
    year_spread = year_range[1] - year_range[0] if years else 0
    pre_2015 = sum(1 for y in years if y < 2015) / len(years) if years else 0
    post_2020 = sum(1 for y in years if y >= 2020) / len(years) if years else 0

    # RISK
    rhy_c = Counter(t["rhythm"] for t in tracks if t["rhythm"])
    total_rhy = sum(rhy_c.values())
    risk_labels = {"breakbeat", "halftime", "2-step/shuffle", "syncopated", "dembow", "polyrhythmic"}
    risk_plays = sum(v for k, v in rhy_c.items() if k in risk_labels)
    risk_ratio = risk_plays / total_rhy if total_rhy else 0

    # Timestamp axes (pace, dwell_cv) — computed directly from candidate timestamps
    ts = sorted(cand["timestamps"])
    pace_sec_median = None
    dwell_cv = None
    if len(ts) >= 2:
        gaps = [ts[i+1] - ts[i] for i in range(len(ts)-1)]
        pace_sec_median = int(statistics.median(gaps))
        dwell_all = list(gaps)
        if cand.get("duration_sec"):
            dwell_all.append(max(0, cand["duration_sec"] - ts[-1]))
        if len(dwell_all) > 2 and statistics.mean(dwell_all) > 0:
            dwell_cv = statistics.stdev(dwell_all) / statistics.mean(dwell_all)

    # Edit density / dynamic range require fingerprint chunks (dj_set_chunks)
    # which we don't compute here — surface as None.
    edit_density = None
    dynamic_range = None

    return {
        "n_tracks_classified": len(tracks),
        "n_plays_total": total_raw,
        "breadth_entropy": subgenre_entropy,
        "median_distinct_per_set": median_distinct,
        "median_run": median_run,
        "anchor_ratio": anchor_ratio,
        "own_ratio": own_ratio,
        "median_year": median_year,
        "year_range": year_range,
        "year_spread": year_spread,
        "pre_2015_ratio": pre_2015,
        "post_2020_ratio": post_2020,
        "risk_ratio": risk_ratio,
        "top_subgenres": sub_c.most_common(8),
        "top_rhythm": rhy_c.most_common(4),
        "subgenre_dist": sub_c,
        "fp": {
            "n_fp_sets": 1 if ts else 0,
            "pace_sec_median": pace_sec_median,
            "dwell_cv": dwell_cv,
            "edit_density": edit_density,
            "dynamic_range": dynamic_range,
        },
    }


# ---------------------------------------------------------------------------
# Reference loading
# ---------------------------------------------------------------------------

def _load_reference_dj(conn, slug: str) -> Dict:
    data = gather(conn, min_resolved=1)
    if slug not in data:
        raise SystemExit(f"No DJ '{slug}' (or no classified tracks)")
    a = compute_axes(slug, data[slug], conn)
    a["subgenre_dist"] = Counter(dict(a["top_subgenres"]))
    a["source"] = "dj"
    a["label"] = slug
    return a


def _load_reference_template(conn, template_id: str) -> Dict:
    row = conn.execute(
        "SELECT * FROM set_templates WHERE template_id = ?", (template_id,)
    ).fetchone()
    if not row:
        raise SystemExit(f"No template '{template_id}' in set_templates")

    try:
        genre_density = json.loads(row["genre_density"]) if row["genre_density"] else {}
    except (json.JSONDecodeError, TypeError):
        genre_density = {}
    # genre_density is {subgenre: seconds}. Convert to Counter weighted by seconds.
    sub_c = Counter()
    for k, v in genre_density.items():
        if k == "(unclassified)":
            continue
        try:
            sub_c[k] += float(v)
        except (TypeError, ValueError):
            pass

    return {
        "source": "template",
        "label": template_id,
        "dj": row["dj_slug"],
        "n_sets": 1,
        "n_plays_total": None,
        "n_tracks_classified": None,
        "subgenre_dist": sub_c,
        "top_subgenres": sub_c.most_common(8),
        "fp": {
            "n_fp_sets": 1,
            "pace_sec_median": row["pace_sec_median"],
            "dwell_cv": None,
            "edit_density": row["edit_density"],
            "dynamic_range": None,
        },
        # Axes not available from a template
        "breadth_entropy": _entropy(Counter({k: int(v) for k, v in sub_c.items()})),
        "median_run": None,
        "anchor_ratio": None,
        "own_ratio": None,
        "median_year": None,
        "year_range": (None, None),
        "year_spread": None,
        "pre_2015_ratio": None,
        "post_2020_ratio": None,
        "risk_ratio": None,
    }


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def _sim_continuous(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """1 - |a-b|/max(|a|,|b|,0.01), clamped to [0,1]. Returns None if either side missing."""
    if a is None or b is None:
        return None
    scale = max(abs(a), abs(b), 0.01)
    s = 1 - abs(a - b) / scale
    return max(0.0, min(1.0, s))


def _sim_year(a: Optional[int], b: Optional[int]) -> Optional[float]:
    """Year similarity: 1.0 at same year, 0.0 at 20+ years apart."""
    if a is None or b is None:
        return None
    gap = abs(a - b)
    return max(0.0, 1.0 - gap / 20.0)


def _cosine(ca: Counter, cb: Counter) -> Optional[float]:
    keys = set(ca) | set(cb)
    if not keys:
        return None
    dot = sum(ca.get(k, 0) * cb.get(k, 0) for k in keys)
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    if na == 0 or nb == 0:
        return None
    return dot / (na * nb)


# Axis weighting for overall similarity
_WEIGHTS = {
    "breadth_entropy": 1.0,
    "median_run": 1.0,
    "anchor_ratio": 1.2,
    "own_ratio": 0.6,
    "era": 0.8,
    "risk_ratio": 0.8,
    "pace_sec_median": 1.0,
    "edit_density": 1.2,
    "dwell_cv": 0.6,
    "genre_density": 1.4,
}


def score_axes(cand: Dict, ref: Dict) -> Dict:
    axes = {}
    axes["breadth_entropy"] = {
        "you": cand["breadth_entropy"], "ref": ref.get("breadth_entropy"),
        "sim": _sim_continuous(cand["breadth_entropy"], ref.get("breadth_entropy")),
        "label": "breadth entropy",
    }
    axes["median_run"] = {
        "you": cand["median_run"], "ref": ref.get("median_run"),
        "sim": _sim_continuous(cand["median_run"], ref.get("median_run")),
        "label": "flow median_run",
    }
    axes["anchor_ratio"] = {
        "you": cand["anchor_ratio"], "ref": ref.get("anchor_ratio"),
        "sim": _sim_continuous(cand["anchor_ratio"], ref.get("anchor_ratio")),
        "label": "anchor ratio",
    }
    axes["own_ratio"] = {
        "you": cand["own_ratio"], "ref": ref.get("own_ratio"),
        "sim": _sim_continuous(cand["own_ratio"], ref.get("own_ratio")),
        "label": "own tracks",
    }
    axes["era"] = {
        "you": cand["median_year"], "ref": ref.get("median_year"),
        "sim": _sim_year(cand["median_year"], ref.get("median_year")),
        "label": "era",
    }
    axes["risk_ratio"] = {
        "you": cand["risk_ratio"], "ref": ref.get("risk_ratio"),
        "sim": _sim_continuous(cand["risk_ratio"], ref.get("risk_ratio")),
        "label": "risk (non-4/4)",
    }

    # Fingerprint-derived
    cf = cand.get("fp") or {}
    rf = ref.get("fp") or {}
    axes["pace_sec_median"] = {
        "you": cf.get("pace_sec_median"), "ref": rf.get("pace_sec_median"),
        "sim": _sim_continuous(cf.get("pace_sec_median"), rf.get("pace_sec_median")),
        "label": "pace (sec/track)",
    }
    axes["dwell_cv"] = {
        "you": cf.get("dwell_cv"), "ref": rf.get("dwell_cv"),
        "sim": _sim_continuous(cf.get("dwell_cv"), rf.get("dwell_cv")),
        "label": "dwell variance",
    }
    axes["edit_density"] = {
        "you": cf.get("edit_density"), "ref": rf.get("edit_density"),
        "sim": _sim_continuous(cf.get("edit_density"), rf.get("edit_density")),
        "label": "edit density",
    }

    # Genre density — cosine between subgenre distributions
    axes["genre_density"] = {
        "you": cand.get("subgenre_dist") or Counter(),
        "ref": ref.get("subgenre_dist") or Counter(),
        "sim": _cosine(cand.get("subgenre_dist") or Counter(), ref.get("subgenre_dist") or Counter()),
        "label": "genre density",
    }

    # Overall weighted mean
    wsum, ssum = 0.0, 0.0
    for k, w in _WEIGHTS.items():
        if k in axes and axes[k]["sim"] is not None:
            ssum += w * axes[k]["sim"]
            wsum += w
    overall = ssum / wsum if wsum else 0.0
    return {"axes": axes, "overall": overall}


# ---------------------------------------------------------------------------
# Feedback synthesis
# ---------------------------------------------------------------------------

def _feedback_items(axes: Dict, cand: Dict, ref: Dict) -> List[str]:
    """Produce concrete, ranked feedback based on worst-scoring axes."""
    tips: List[Tuple[float, str]] = []

    a = axes.get("anchor_ratio", {})
    if a.get("sim") is not None and a["sim"] < 0.6 and a.get("ref") is not None:
        you = a["you"] or 0
        ref_v = a["ref"] or 0
        if ref_v > you + 0.05:
            tips.append((a["sim"], f"Play more signature tracks across your rotation — your anchor ratio is {you*100:.0f}% vs ref {ref_v*100:.0f}%."))
        elif you > ref_v + 0.05:
            tips.append((a["sim"], f"Rotate fresher material — you repeat tracks {you*100:.0f}% vs ref {ref_v*100:.0f}%."))

    e = axes.get("edit_density", {})
    if e.get("sim") is not None and e["sim"] < 0.6 and e.get("ref") is not None:
        you = e["you"] or 0
        ref_v = e["ref"] or 0
        if ref_v > you + 0.05:
            tips.append((e["sim"], f"Source more unreleased/custom edits — you're at {you*100:.0f}% edit density, ref is {ref_v*100:.0f}%."))
        else:
            tips.append((e["sim"], f"Your set is {you*100:.0f}% unidentified material — ref runs tighter at {ref_v*100:.0f}%."))

    mr = axes.get("median_run", {})
    if mr.get("sim") is not None and mr["sim"] < 0.6:
        you, ref_v = mr.get("you"), mr.get("ref")
        if you is not None and ref_v is not None:
            if you < ref_v:
                tips.append((mr["sim"], f"Hold subgenres longer — you swap every {you} tracks, ref sits in for {ref_v}."))
            elif you > ref_v:
                tips.append((mr["sim"], f"Hop more — you stay in a subgenre for {you} tracks, ref pivots every {ref_v}."))

    era = axes.get("era", {})
    if era.get("sim") is not None and era["sim"] < 0.6:
        you, ref_v = era.get("you"), era.get("ref")
        if you is not None and ref_v is not None:
            if you > ref_v + 2:
                lo, hi = ref_v - 2, ref_v + 2
                tips.append((era["sim"], f"Dig back to {lo}-{hi} — you skew {you}, ref centers {ref_v}."))
            elif you < ref_v - 2:
                tips.append((era["sim"], f"Source contemporary — you skew {you}, ref centers {ref_v}."))

    pace = axes.get("pace_sec_median", {})
    if pace.get("sim") is not None and pace["sim"] < 0.6:
        you, ref_v = pace.get("you"), pace.get("ref")
        if you is not None and ref_v is not None:
            if you < ref_v:
                tips.append((pace["sim"], f"Slow your mixing — you swap every {you}s, ref holds {ref_v}s per track."))
            else:
                tips.append((pace["sim"], f"Mix faster — you sit on tracks {you}s each, ref cycles every {ref_v}s."))

    breadth = axes.get("breadth_entropy", {})
    if breadth.get("sim") is not None and breadth["sim"] < 0.6:
        you, ref_v = breadth.get("you"), breadth.get("ref")
        if you is not None and ref_v is not None:
            if you > ref_v + 0.3:
                tips.append((breadth["sim"], f"Narrow your crate — entropy {you:.2f} vs ref {ref_v:.2f}. Pick fewer lanes."))
            elif you < ref_v - 0.3:
                tips.append((breadth["sim"], f"Widen your crate — entropy {you:.2f} vs ref {ref_v:.2f}. Tour more subgenres."))

    risk = axes.get("risk_ratio", {})
    if risk.get("sim") is not None and risk["sim"] < 0.6:
        you, ref_v = risk.get("you"), risk.get("ref")
        if you is not None and ref_v is not None:
            if ref_v > you + 0.05:
                tips.append((risk["sim"], f"Break the 4/4 more — only {you*100:.0f}% of your tracks are off-grid, ref runs {ref_v*100:.0f}%."))
            elif you > ref_v + 0.05:
                tips.append((risk["sim"], f"Dial back off-grid rhythms — you're {you*100:.0f}% non-4/4, ref holds {ref_v*100:.0f}%."))

    gd = axes.get("genre_density", {})
    if gd.get("sim") is not None and gd["sim"] < 0.6:
        cand_top = [k for k, _ in Counter(gd.get("you") or Counter()).most_common(3)]
        ref_top = [k for k, _ in Counter(gd.get("ref") or Counter()).most_common(3)]
        missing = [r for r in ref_top if r not in cand_top]
        if missing:
            tips.append((gd["sim"], f"Dig into {', '.join(missing)} — those subgenres anchor the ref's identity."))

    # Sort worst-first and dedupe by message prefix
    tips.sort(key=lambda t: t[0])
    seen = set()
    out = []
    for _, msg in tips:
        key = msg[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append(msg)
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt_val(v, kind: str) -> str:
    if v is None:
        return "N/A"
    if kind == "pct":
        return f"{v*100:.0f}%"
    if kind == "int":
        return f"{int(v)}"
    if kind == "sec":
        return f"{int(v)}s"
    if kind == "float2":
        return f"{v:.2f}"
    if kind == "year":
        return f"{int(v)}"
    return str(v)


_AXIS_FMT = {
    "breadth_entropy": "float2",
    "median_run": "int",
    "anchor_ratio": "pct",
    "own_ratio": "pct",
    "era": "year",
    "risk_ratio": "pct",
    "pace_sec_median": "sec",
    "dwell_cv": "float2",
    "edit_density": "pct",
}


def render_card(cand: Dict, ref: Dict, result: Dict, ref_kind: str) -> str:
    ts = cand.get("timestamps") or []
    minutes = None
    if cand.get("duration_sec"):
        minutes = int(cand["duration_sec"] / 60)
    elif ts:
        minutes = int(max(ts) / 60)

    lines = []
    lines.append("EMULATION SCORECARD")
    cand_label = cand.get("label")
    cand_title = cand.get("title") or ""
    n_cand = cand.get("n_tracks_total") or len(cand.get("raw_plays") or [])
    mins_str = f"{minutes} min" if minutes else "duration N/A"
    lines.append(f"Candidate: {cand_label}  ({n_cand} tracks, {mins_str})")
    if cand_title:
        lines.append(f"  {cand_title}")
    if ref_kind == "dj":
        lines.append(f"Reference: {ref['dj']}  ({ref.get('n_sets', 0)} sets, {ref.get('n_plays_total', 0)} plays)")
    else:
        lines.append(f"Reference: template {ref['label']}  (dj_slug={ref.get('dj')})")

    lines.append("")
    lines.append(f"Overall similarity: {result['overall']:.2f} / 1.00")
    lines.append("-" * 62)

    axes = result["axes"]
    order = ["breadth_entropy", "median_run", "anchor_ratio", "own_ratio",
             "era", "risk_ratio", "pace_sec_median", "dwell_cv",
             "edit_density", "genre_density"]
    for k in order:
        ax = axes.get(k)
        if not ax:
            continue
        label = ax["label"]
        sim = ax["sim"]
        sim_s = f"{sim:.2f}" if sim is not None else "N/A "
        if k == "genre_density":
            lines.append(f"  {label:<20} {sim_s}  (cosine; top subgenres below)")
            continue
        fmt = _AXIS_FMT.get(k, "float2")
        you_s = _fmt_val(ax["you"], fmt)
        ref_s = _fmt_val(ax["ref"], fmt)
        note = ""
        if sim is not None and sim < 0.6 and ax["you"] is not None and ax["ref"] is not None:
            if k in ("anchor_ratio", "edit_density", "risk_ratio", "own_ratio"):
                if ax["ref"] > ax["you"]:
                    note = " -> below ref"
                else:
                    note = " -> above ref"
            elif k == "median_run":
                note = " -> hold longer" if ax["you"] < ax["ref"] else " -> hop more"
            elif k == "era":
                note = " -> too contemporary" if ax["you"] > ax["ref"] else " -> too retro"
            elif k == "pace_sec_median":
                note = " -> mixing too fast" if ax["you"] < ax["ref"] else " -> mixing too slow"
            elif k == "breadth_entropy":
                note = " -> crate too wide" if ax["you"] > ax["ref"] else " -> crate too narrow"
        lines.append(f"  {label:<20} {sim_s}  you {you_s:<8} vs  ref {ref_s:<8}{note}")
        if ax["sim"] is None and (ax["you"] is None or ax["ref"] is None):
            if ax["you"] is None and ax["ref"] is None:
                pass
            else:
                side = "candidate" if ax["you"] is None else "reference"
                lines.append(f"    N/A — no data on {side} for this axis")

    # Genre density detail
    gd = axes.get("genre_density", {})
    cand_dist = gd.get("you") or Counter()
    ref_dist = gd.get("ref") or Counter()
    if cand_dist or ref_dist:
        lines.append("")
        lines.append("  TOP SUBGENRES")
        cand_top = Counter(cand_dist).most_common(5)
        ref_top = Counter(ref_dist).most_common(5)
        lines.append(f"    you: " + ", ".join(f"{k} ({int(v)})" for k, v in cand_top) if cand_top else "    you: —")
        lines.append(f"    ref: " + ", ".join(f"{k} ({int(v)})" for k, v in ref_top) if ref_top else "    ref: —")

    # Feedback
    tips = _feedback_items(axes, cand, ref)
    lines.append("")
    lines.append("TOP FEEDBACK")
    if not tips:
        lines.append("  You're already close to the reference on every measurable axis.")
    else:
        for i, t in enumerate(tips[:5], 1):
            lines.append(f"  {i}. {t}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Score a candidate DJ set against a reference DJ/template.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--set", dest="set_id", help="Candidate: existing set_id in dj_set_tracks")
    src.add_argument("--csv", help="Candidate: CSV with position,timestamp_sec,artist,title,spotify_id")

    ref = ap.add_mutually_exclusive_group(required=True)
    ref.add_argument("--like-dj", dest="like_dj", help="Reference: DJ slug")
    ref.add_argument("--like-template", dest="like_template", help="Reference: template_id from set_templates")

    ap.add_argument("--md", help="Write scorecard to file instead of stdout")
    args = ap.parse_args()

    with connect() as conn:
        if args.set_id:
            cand = _load_candidate_from_set(conn, args.set_id)
        else:
            cand = _load_candidate_from_csv(conn, args.csv)

        if args.like_dj:
            ref_data = _load_reference_dj(conn, args.like_dj)
            ref_kind = "dj"
            dj_own_hint = args.like_dj
        else:
            ref_data = _load_reference_template(conn, args.like_template)
            ref_kind = "template"
            dj_own_hint = ref_data.get("dj")

        cand_axes = _axes_from_candidate(cand, dj_slug_for_own=dj_own_hint)
        # Attach some candidate metadata for rendering
        cand_axes["label"] = cand["label"]
        cand_axes["title"] = cand["title"]
        cand_axes["n_tracks_total"] = cand["n_tracks_total"]
        cand_axes["duration_sec"] = cand["duration_sec"]
        cand_axes["timestamps"] = cand["timestamps"]
        cand_axes["raw_plays"] = cand["raw_plays"]

        result = score_axes(cand_axes, ref_data)
        card = render_card(cand_axes, ref_data, result, ref_kind)

        if args.md:
            with open(args.md, "w") as f:
                f.write(card + "\n")
            print(f"Wrote {args.md}")
        else:
            print(card)


if __name__ == "__main__":
    main()
