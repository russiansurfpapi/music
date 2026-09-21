"""DJ archetype fingerprint — 5 playstyle axes + emulation cheatsheet.

Usage:
    python3 dj_archetype.py --dj duke-dumont
    python3 dj_archetype.py --compare duke-dumont,dj-koze,mochakk
    python3 dj_archetype.py --all --md DJ_ARCHETYPES.md
"""

import argparse
import os
import re
import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Optional

import yaml

from db import connect
from dj_profiles import gather, _entropy, _runs, _list
from classify import load_tag_map

_HERE = os.path.dirname(os.path.abspath(__file__))
_INTERVIEWS_RAW = os.path.join(_HERE, "dj_interviews_raw.md")
_INTERVIEWS_VOCAB = os.path.join(_HERE, "dj_interviews_vocab.yaml")


# ---------------------------------------------------------------------------
# Axis computation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Qualitative layer: vibe tags, interview quotes, DJ vocabulary
# ---------------------------------------------------------------------------

_VIBE_META_EXCL = {
    "electronic", "dance", "edm", "electronica", "club", "electronic dance music",
    "alternative", "pop", "rock", "indie",  # too broad for vibe
}

def _is_vibe_tag(tag: str) -> bool:
    """Filter to readable, intent-bearing tags (exclude scraping junk + canonical meta)."""
    t = tag.lower().strip()
    if not t or len(t) > 30:
        return False
    if t in _VIBE_META_EXCL:
        return False
    # Scraping artifacts: underscore-heavy, colons, hashes
    if "_" in t or ":" in t or "#" in t:
        return False
    # Must contain at least one alphabetic char
    if not re.search(r"[a-z]", t):
        return False
    return True


def _vibe_tag_cloud(dj_slug: str, conn, top_n: int = 15) -> List:
    """Raw Last.fm tags from this DJ's tracks that aren't canonical-layer mapped.
    Surfaces 'ibiza', 'warehouse', 'afterhours', 'balearic', 'peak time'-style vibe terms."""
    tag_map, skip = load_tag_map()
    mapped = set()
    for layer in tag_map.values():
        mapped.update(layer.keys())
    mapped |= skip

    rows = conn.execute("""
        SELECT tt.tag, COUNT(DISTINCT tt.spotify_id) AS n
        FROM track_tags tt
        JOIN dj_set_tracks dt ON tt.spotify_id = dt.spotify_id
        JOIN dj_sets s ON dt.set_id = s.set_id
        WHERE s.dj_slug = ?
        GROUP BY tt.tag
        ORDER BY n DESC
    """, (dj_slug,)).fetchall()
    cloud = [
        (r["tag"], r["n"])
        for r in rows
        if r["tag"].lower() not in mapped and _is_vibe_tag(r["tag"])
    ]
    return cloud[:top_n]


def _normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _interview_quotes(dj_slug: str, max_lines: int = 6) -> List[str]:
    """Parse dj_interviews_raw.md by ## DJ Name headers. Return up to N quoted lines for this DJ."""
    if not os.path.exists(_INTERVIEWS_RAW):
        return []
    with open(_INTERVIEWS_RAW) as f:
        txt = f.read()
    sections = re.split(r"^## ", txt, flags=re.MULTILINE)
    target = _normalize_name(dj_slug)
    matched = []
    for s in sections[1:]:
        heading_end = s.find("\n")
        heading = s[:heading_end] if heading_end > 0 else s[:80]
        heading_norm = _normalize_name(heading)
        # Match if all tokens of the slug appear in the heading
        tokens = target.split()
        if all(tok in heading_norm for tok in tokens):
            body = s[heading_end:] if heading_end > 0 else ""
            # Pull quoted lines (bulleted strings starting with "- ")
            for line in body.splitlines():
                m = re.match(r'^- "(.+?)"\s*$', line.strip())
                if m:
                    matched.append(m.group(1))
                if len(matched) >= max_lines:
                    break
            if matched:
                break
    return matched


def _interview_vocab(dj_slug: str) -> List[Dict]:
    """Terms from dj_interviews_vocab.yaml that this DJ is cited as using."""
    if not os.path.exists(_INTERVIEWS_VOCAB):
        return []
    with open(_INTERVIEWS_VOCAB) as f:
        data = yaml.safe_load(f) or {}
    target_tokens = _normalize_name(dj_slug).split()
    out = []
    for section_name, entries in data.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            used_by = [_normalize_name(u) for u in (e.get("used_by") or [])]
            if any(all(t in u for t in target_tokens) for u in used_by):
                out.append({
                    "term": e.get("term"),
                    "means": e.get("means"),
                    "section": section_name,
                })
    return out


def _fp_axes(dj_slug: str, conn) -> Dict:
    """Timestamp-aware axes from fingerprinted YouTube sets.

    Returns all-None dict if the DJ has no fingerprinted sets. Axes:
      pace_sec_median    — median seconds between consecutive track changes
      dwell_cv           — coefficient of variation of per-track playtimes
                           (0 = all tracks get equal time; high = uneven)
      edit_density       — fraction of set time unidentified (requires
                           dj_set_chunks; else estimated from long-gap heuristic)
      dynamic_range      — RMS variance across chunks (requires librosa pass)
      n_fp_sets          — # fingerprinted sets used
    """
    sets = conn.execute(
        "SELECT set_id, duration_sec FROM dj_sets "
        "WHERE dj_slug = ? AND youtube_url IS NOT NULL",
        (dj_slug,)
    ).fetchall()
    if not sets:
        return {"n_fp_sets": 0, "pace_sec_median": None, "dwell_cv": None,
                "edit_density": None, "dynamic_range": None}

    pace_samples: List[int] = []
    dwell_all: List[int] = []
    gap_pcts: List[float] = []
    rms_cvs: List[float] = []

    for s in sets:
        sid, dur = s["set_id"], s["duration_sec"] or 0
        tracks = conn.execute(
            "SELECT timestamp_sec FROM dj_set_tracks WHERE set_id = ? "
            "AND timestamp_sec IS NOT NULL ORDER BY position", (sid,)
        ).fetchall()
        ts = [t["timestamp_sec"] for t in tracks]
        if len(ts) < 2: continue
        gaps = [ts[i+1] - ts[i] for i in range(len(ts)-1)]
        pace_samples.extend(gaps)
        dwell_all.extend(gaps)
        if dur: dwell_all.append(dur - ts[-1])

        # Exact edit density if chunks exist
        chunks = conn.execute(
            "SELECT matched, rms FROM dj_set_chunks WHERE set_id = ?", (sid,)
        ).fetchall()
        if chunks:
            matched = sum(1 for c in chunks if c["matched"])
            gap_pcts.append(1 - matched/len(chunks))
            rms_vals = [c["rms"] for c in chunks if c["rms"] is not None]
            if len(rms_vals) > 2 and statistics.mean(rms_vals) > 0:
                rms_cvs.append(statistics.stdev(rms_vals) / statistics.mean(rms_vals))
        # No else: edit density requires exact chunk data. Approximation via
        # gap-heuristic is circular (avg_gap × n_tracks ≈ duration by construction).
        # Run compute_audio_features.py to populate dj_set_chunks for this axis.

    return {
        "n_fp_sets": len(sets),
        "pace_sec_median": int(statistics.median(pace_samples)) if pace_samples else None,
        "dwell_cv": (statistics.stdev(dwell_all) / statistics.mean(dwell_all))
                    if len(dwell_all) > 2 and statistics.mean(dwell_all) > 0 else None,
        "edit_density": statistics.mean(gap_pcts) if gap_pcts else None,
        "dynamic_range": statistics.mean(rms_cvs) if rms_cvs else None,
    }


def compute_axes(dj_slug: str, data: Dict, conn) -> Dict:
    """Return the 5 axes + supporting stats for one DJ."""
    tracks = data["tracks"]
    sets = data["sets"]

    # --- Raw DJ-set data for anchor/own detection (not filtered by classification) ---
    raw_rows = conn.execute("""
        SELECT t.set_id, t.raw_artist, t.raw_title, t.spotify_id
        FROM dj_set_tracks t
        JOIN dj_sets s ON t.set_id = s.set_id
        WHERE s.dj_slug = ?
    """, (dj_slug,)).fetchall()
    raw_plays = [dict(r) for r in raw_rows]
    total_raw = len(raw_plays)

    # --- BREADTH: subgenre entropy + distinct subgenres per set ---
    sub_c = Counter(t["subgenre"] for t in tracks)
    subgenre_entropy = _entropy(sub_c)
    by_set = defaultdict(list)
    for t in tracks:
        by_set[t["set_id"]].append(t["subgenre"])
    distinct_per_set = [len(set(v)) for v in by_set.values()]
    median_distinct = int(statistics.median(distinct_per_set)) if distinct_per_set else 0

    # --- FLOW: run-length stats (how long do they stay in one subgenre) ---
    all_runs = []
    for sid, subs in by_set.items():
        all_runs.extend(_runs(subs))
    median_run = int(statistics.median(all_runs)) if all_runs else 0
    mean_run = statistics.mean(all_runs) if all_runs else 0
    max_run = max(all_runs) if all_runs else 0

    # --- ANCHOR LOYALTY: % of plays that are tracks this DJ plays in ≥2 of their sets ---
    play_counts: Counter = Counter()
    for p in raw_plays:
        key = (p["spotify_id"] or (p["raw_artist"] + "||" + p["raw_title"])).lower()
        play_counts[key] += 1
    anchor_plays = sum(c for c in play_counts.values() if c >= 2)
    anchor_ratio = anchor_plays / total_raw if total_raw else 0

    # --- OWN-TRACK RATIO: raw_artist mentions DJ name ---
    dj_tokens = [t for t in dj_slug.split("-") if len(t) > 2]
    own_plays = 0
    for p in raw_plays:
        artist = (p["raw_artist"] or "").lower()
        if any(tok in artist for tok in dj_tokens):
            own_plays += 1
    own_ratio = own_plays / total_raw if total_raw else 0

    # --- ERA POSTURE: median year + spread (range width) ---
    years = [t["year"] for t in tracks if t.get("year")]
    median_year = int(statistics.median(years)) if years else None
    year_range = (min(years), max(years)) if years else (None, None)
    year_spread = year_range[1] - year_range[0] if years else 0
    # Retro: >50% pre-2015. Contemporary: >50% post-2020.
    pre_2015 = sum(1 for y in years if y < 2015) / len(years) if years else 0
    post_2020 = sum(1 for y in years if y >= 2020) / len(years) if years else 0

    # --- RISK: non-4/4 rhythm share (breaks, halftime, 2-step, syncopated, dembow) ---
    rhy_c = Counter(t["rhythm"] for t in tracks if t["rhythm"])
    total_rhy = sum(rhy_c.values())
    risk_labels = {"breakbeat", "halftime", "2-step/shuffle", "syncopated", "dembow", "polyrhythmic"}
    risk_plays = sum(v for k, v in rhy_c.items() if k in risk_labels)
    risk_ratio = risk_plays / total_rhy if total_rhy else 0

    # --- Secondary/descriptive ---
    top_genres = Counter(t["genre"] for t in tracks if t["genre"])
    tex_c: Counter = Counter()
    dna_c: Counter = Counter()
    for t in tracks:
        for x in t["texture"]: tex_c[x] += 1
        for d in t["dna"]:     dna_c[d] += 1

    # Top-1 subgenre share — how concentrated is the selection?
    top1_share = (sub_c.most_common(1)[0][1] / sum(sub_c.values())) if sub_c else 0

    # --- Signature tracks (played in ≥ half of DJ's sets) ---
    track_to_sets: Dict[str, set] = defaultdict(set)
    for p in raw_plays:
        a = (p["raw_artist"] or "").strip().lower()
        if a in {"id", "i.d.", "unknown", "?"}:
            continue  # Unidentified — not a real signature
        key = p["raw_artist"] + " — " + p["raw_title"]
        track_to_sets[key].add(p["set_id"])
    n_sets = len(sets)
    threshold = max(2, n_sets // 2)
    signatures = sorted(
        [(k, len(v)) for k, v in track_to_sets.items() if len(v) >= threshold],
        key=lambda x: -x[1],
    )[:8]

    # --- Signature opener/closer (most common first/last track) ---
    opener_c: Counter = Counter()
    closer_c: Counter = Counter()
    for sid in sets:
        set_plays = [p for p in raw_plays if p["set_id"] == sid]
        if not set_plays:
            continue
        set_plays_sorted = sorted(set_plays, key=lambda x: x.get("position", 0))
        # Since raw_plays came in row order, it's already roughly positional
    # Reload with proper position ordering
    set_ordered = conn.execute("""
        SELECT t.set_id, t.position, t.raw_artist, t.raw_title
        FROM dj_set_tracks t
        JOIN dj_sets s ON t.set_id = s.set_id
        WHERE s.dj_slug = ?
        ORDER BY t.set_id, t.position
    """, (dj_slug,)).fetchall()
    sets_tracks: Dict[str, List] = defaultdict(list)
    for r in set_ordered:
        a = (r["raw_artist"] or "").strip().lower()
        if a in {"id", "i.d.", "unknown", "?"}:
            continue  # Skip unidentified — don't let "ID - ID" become a signature opener/closer
        sets_tracks[r["set_id"]].append(r["raw_artist"] + " — " + r["raw_title"])
    for sid, tlist in sets_tracks.items():
        if tlist:
            opener_c[tlist[0]] += 1
            closer_c[tlist[-1]] += 1

    # Qualitative layer
    vibe_tags = _vibe_tag_cloud(dj_slug, conn)
    quotes = _interview_quotes(dj_slug)
    vocab = _interview_vocab(dj_slug)

    # Fingerprint-derived timestamp axes (None if no fingerprinted sets)
    fp = _fp_axes(dj_slug, conn)

    return {
        "dj": dj_slug,
        "n_sets": len(sets),
        "fp": fp,
        "n_tracks_classified": len(tracks),
        "n_plays_total": total_raw,
        "vibe_tags": vibe_tags,
        "quotes": quotes,
        "vocab": vocab,
        # Axes
        "breadth_entropy": subgenre_entropy,
        "median_distinct_per_set": median_distinct,
        "median_run": median_run,
        "mean_run": mean_run,
        "max_run": max_run,
        "anchor_ratio": anchor_ratio,
        "own_ratio": own_ratio,
        "median_year": median_year,
        "year_range": year_range,
        "year_spread": year_spread,
        "pre_2015_ratio": pre_2015,
        "post_2020_ratio": post_2020,
        "risk_ratio": risk_ratio,
        # Descriptive
        "top1_share": top1_share,
        "top_subgenres": sub_c.most_common(6),
        "top_genres": top_genres.most_common(4),
        "top_rhythm": rhy_c.most_common(4),
        "top_dna": dna_c.most_common(5),
        "top_texture": tex_c.most_common(4),
        "signatures": signatures,
        "signature_opener": opener_c.most_common(1)[0] if opener_c else None,
        "signature_closer": closer_c.most_common(1)[0] if closer_c else None,
    }


# ---------------------------------------------------------------------------
# Archetype label
# ---------------------------------------------------------------------------

def archetype_label(a: Dict) -> str:
    """Compose a short archetype from the axes."""
    parts = []

    # Breadth
    e = a["breadth_entropy"]
    if e < 1.8:
        parts.append("tunnel-diver")
    elif e < 2.8:
        parts.append("disciplined")
    elif e < 3.5:
        parts.append("wide")
    else:
        parts.append("eclectic")

    # Flow
    if a["median_run"] >= 3:
        parts.append("long-blocks")
    elif a["median_run"] == 2:
        parts.append("short-blocks")
    else:
        parts.append("quick-hop")

    # Anchor loyalty
    if a["anchor_ratio"] > 0.35:
        parts.append("signature-heavy")
    elif a["anchor_ratio"] < 0.10:
        parts.append("cratedigger")

    # Own tracks
    if a["own_ratio"] > 0.20:
        parts.append("self-promoting")

    # Era posture
    if a["pre_2015_ratio"] > 0.5:
        parts.append("retro-leaning")
    elif a["post_2020_ratio"] > 0.6:
        parts.append("contemporary")
    if a["year_spread"] and a["year_spread"] >= 20 and 0.15 <= a["pre_2015_ratio"] <= 0.5:
        parts.append("historical-tour")

    # Risk
    if a["risk_ratio"] > 0.15:
        parts.append("rhythmically-adventurous")

    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def bar(value: float, scale_max: float, width: int = 10) -> str:
    filled = int(round((value / scale_max) * width)) if scale_max > 0 else 0
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def fmt_profile(a: Dict) -> str:
    dj = a["dj"]
    label = archetype_label(a)
    out = []
    out.append(f"## {dj}")
    out.append(f"**Archetype:** _{label}_")
    out.append(f"**Data:** {a['n_sets']} sets, {a['n_plays_total']} total plays, {a['n_tracks_classified']} classified\n")

    out.append("### Playstyle axes")
    out.append("```")
    out.append(f"BREADTH         {bar(a['breadth_entropy'], 5)}  entropy {a['breadth_entropy']:.2f}  ({a['median_distinct_per_set']} subgenres/set median)")
    out.append(f"FLOW            {bar(1/(a['median_run'] or 1), 1)}  median run {a['median_run']} (hop every N tracks)")
    out.append(f"ANCHOR LOYALTY  {bar(a['anchor_ratio'], 1)}  {a['anchor_ratio']*100:.0f}% of plays are repeat tracks")
    out.append(f"OWN TRACKS      {bar(a['own_ratio'], 1)}  {a['own_ratio']*100:.0f}% of plays are their own productions")
    if a["median_year"]:
        era_tag = "retro" if a['pre_2015_ratio'] > 0.5 else ("contemporary" if a['post_2020_ratio'] > 0.6 else "mixed")
        out.append(f"ERA POSTURE     median {a['median_year']}, range {a['year_range'][0]}-{a['year_range'][1]} ({era_tag})")
    out.append(f"RISK TASTE      {bar(a['risk_ratio'], 0.3)}  {a['risk_ratio']*100:.0f}% non-4/4 rhythm")
    fp = a.get("fp") or {}
    if fp.get("n_fp_sets"):
        out.append("")
        out.append(f"──── from {fp['n_fp_sets']} fingerprinted YouTube set(s) ────")
        if fp.get("pace_sec_median") is not None:
            p = fp["pace_sec_median"]
            pace_label = "rapid" if p < 60 else ("brisk" if p < 120 else ("patient" if p < 240 else "lingering"))
            # scale: 30s (fast) → 300s (very slow); lower is faster mixing
            speed_frac = min(1, max(0, (300 - p) / 270))
            out.append(f"PACE            {bar(speed_frac, 1)}  median {p//60}m{p%60:02d}s between tracks ({pace_label})")
        if fp.get("dwell_cv") is not None:
            out.append(f"DWELL VARIANCE  {bar(fp['dwell_cv'], 1.5)}  CV {fp['dwell_cv']:.2f} (how uneven playtimes are)")
        if fp.get("edit_density") is not None:
            out.append(f"EDIT DENSITY    {bar(fp['edit_density'], 0.5)}  {fp['edit_density']*100:.0f}% unidentified (edits/IDs/bootlegs)")
        if fp.get("dynamic_range") is not None:
            out.append(f"DYNAMIC RANGE   {bar(fp['dynamic_range'], 1.0)}  RMS CV {fp['dynamic_range']:.2f} (builds+drops vs plateau)")
    out.append("```\n")

    out.append("### Selection DNA")
    out.append(f"- **Top subgenres:** " + ", ".join(f"{k} ({v})" for k, v in a["top_subgenres"]))
    out.append(f"- **Top genres:** " + ", ".join(f"{k} ({v})" for k, v in a["top_genres"]))
    if a["top_rhythm"]:
        out.append(f"- **Rhythm:** " + ", ".join(f"{k} ({v})" for k, v in a["top_rhythm"]))
    if a["top_dna"]:
        out.append(f"- **Production DNA:** " + ", ".join(f"{k} ({v})" for k, v in a["top_dna"]))
    if a["top_texture"]:
        out.append(f"- **Texture:** " + ", ".join(f"{k} ({v})" for k, v in a["top_texture"]))

    if a["signatures"]:
        out.append("\n### Signature tracks (played in ≥half of their sets)")
        for k, n in a["signatures"]:
            out.append(f"- `{n} sets` · {k}")

    if a["signature_opener"] and a["signature_opener"][1] >= 2:
        out.append(f"\n**Signature opener:** {a['signature_opener'][0]} ({a['signature_opener'][1]}/{a['n_sets']} sets)")
    if a["signature_closer"] and a["signature_closer"][1] >= 2:
        out.append(f"**Signature closer:** {a['signature_closer'][0]} ({a['signature_closer'][1]}/{a['n_sets']} sets)")

    # ---- Qualitative layer ----
    if a["vibe_tags"]:
        out.append("\n### Vibe cloud (raw Last.fm tags, non-canonical)")
        out.append("_Context tags the classification taxonomy strips out — `ibiza`, `warehouse`, `peak time`, etc._\n")
        cloud_str = ", ".join(f"**{t}** ({n})" for t, n in a["vibe_tags"][:12])
        out.append(cloud_str)

    if a["quotes"]:
        out.append("\n### In their own words (from interviews)")
        for q in a["quotes"][:5]:
            out.append(f"> {q}")

    if a["vocab"]:
        out.append("\n### Vocabulary they've used in press")
        for v in a["vocab"][:6]:
            out.append(f"- **\"{v['term']}\"** — {v['means']} _(via {v['section']})_")

    out.append("\n### To play like " + dj)
    out.append(_emulation_tips(a))
    out.append("\n---\n")
    return "\n".join(out)


def _emulation_tips(a: Dict) -> str:
    tips = []
    e = a["breadth_entropy"]
    if e < 1.8:
        tips.append(f"1. **Stay inside one subgenre** — pick your lane (`{a['top_subgenres'][0][0]}`) and tunnel. Don't tour.")
    elif e < 2.8:
        tips.append(f"2. **Prep 3-4 subgenres** — anchor on `{a['top_subgenres'][0][0]}` and rotate in adjacent flavors.")
    else:
        subs = ", ".join(k for k, _ in a['top_subgenres'][:4])
        tips.append(f"1. **Build a crate across {a['median_distinct_per_set']}+ subgenres** — {subs} all get airtime.")

    if a["median_run"] >= 3:
        tips.append(f"2. **Long blocks** — settle into a subgenre for 3+ tracks before pivoting. Don't rush shifts.")
    elif a["median_run"] == 1:
        tips.append(f"2. **Hop every track** — no two adjacent tracks should share a subgenre. Keep the audience guessing.")
    else:
        tips.append(f"2. **Short paired blocks** — 2-track mini-statements, then transition.")

    if a["anchor_ratio"] > 0.3:
        tips.append(f"3. **Lock in anchor tracks** — replay your signature hits across sets. {a['anchor_ratio']*100:.0f}% repeat rate here.")
    elif a["anchor_ratio"] < 0.10:
        tips.append(f"3. **Fresh every set** — cratedigger mode. Don't repeat the same tracks.")

    if a["own_ratio"] > 0.2:
        tips.append(f"4. **Play your own productions aggressively** — {a['own_ratio']*100:.0f}% of plays here are self-cites.")

    if a["pre_2015_ratio"] > 0.5:
        tips.append(f"5. **Dig classic** — median track year is {a['median_year']}. Know the 90s-00s canon.")
    elif a["post_2020_ratio"] > 0.6:
        tips.append(f"5. **Stay in the current year** — {a['post_2020_ratio']*100:.0f}% of plays are 2020+. Fresh-press only.")
    elif a["year_spread"] >= 20:
        tips.append(f"5. **Span eras** — tracks range {a['year_range'][0]}-{a['year_range'][1]}. You're taking listeners on a time-travel.")

    if a["risk_ratio"] > 0.15:
        tips.append(f"6. **Break the 4/4** — {a['risk_ratio']*100:.0f}% of plays use breaks/halftime/2-step. Own it.")

    if a["signature_closer"] and a["signature_closer"][1] >= 3:
        tips.append(f"7. **Close with emotional payoff** — they use the same closer repeatedly. Find yours.")

    return "\n".join(tips)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dj", help="One DJ slug")
    ap.add_argument("--compare", help="Comma-separated slugs")
    ap.add_argument("--all", action="store_true", help="All DJs meeting min-set threshold")
    ap.add_argument("--min-sets", type=int, default=2, help="Minimum sets per DJ (default 2)")
    ap.add_argument("--min-tracks", type=int, default=15,
                    help="Minimum classified tracks per DJ (default 15). Lower for single-set/thin profiles.")
    ap.add_argument("--md", help="Write to file")
    args = ap.parse_args()

    with connect() as conn:
        data = gather(conn, min_resolved=args.min_tracks)
        targets: List[str] = []
        if args.dj:
            targets = [args.dj]
        elif args.compare:
            targets = [s.strip() for s in args.compare.split(",")]
        elif args.all:
            targets = sorted(
                [dj for dj, d in data.items() if len(d["sets"]) >= args.min_sets],
                key=lambda dj: -len(data[dj]["tracks"]),
            )
        else:
            ap.print_help()
            return

        out = ["# DJ Archetypes\n"]
        out.append("Playstyle fingerprints to emulate. Each DJ is a crate of decisions — breadth × flow × loyalty × era × risk.\n")
        out.append("---\n")

        for dj in targets:
            if dj not in data:
                print(f"# SKIP: no data for '{dj}' (needs ≥15 classified tracks)")
                continue
            axes = compute_axes(dj, data[dj], conn)
            out.append(fmt_profile(axes))

        md = "\n".join(out)
        if args.md:
            with open(args.md, "w") as f:
                f.write(md)
            print(f"Wrote {args.md}")
        else:
            print(md)


if __name__ == "__main__":
    main()
