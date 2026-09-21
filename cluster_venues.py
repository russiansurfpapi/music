"""Venue archetype clustering for set_templates.

Groups full-set templates by shape (BPM curve + energy curve + pace + genre mix)
to produce readable venue/context archetypes such as "Boiler Room deep" or
"festival peak". Backs the labels into a venue_clusters table and stamps
each template with its cluster id.

Usage:
  python3 cluster_venues.py --fit            # fit clusters, update DB
  python3 cluster_venues.py --report         # print clusters + members
  python3 cluster_venues.py --md path.md     # write markdown report

Features per template (~50-dim vector):
  - 20-dim BPM curve (forward-fill, median-impute, z-score)
  - 20-dim energy curve (same treatment)
  - pace_sec_median / 300
  - edit_density (0..1)
  - duration_sec / 3600
  - top-5 subgenre share

Clustering:
  KMeans with K in {4, 6} (+ fallback to {3, 2} if empty clusters), pick
  by silhouette. Silhouette over 8 points is noisy; accepted as approximate.
"""

import argparse
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from db import connect


CURVE_BINS = 20
TOP_SUBGENRES_K = 5


# --------------------------------------------------------------------------- #
# Feature extraction
# --------------------------------------------------------------------------- #


def _parse_curve(raw: Optional[str]) -> List[Optional[float]]:
    """A curve JSON is a list of [time_sec, value] pairs (value may be null).
    Return just the value sequence, padded/truncated to CURVE_BINS."""
    if not raw:
        return [None] * CURVE_BINS
    try:
        pairs = json.loads(raw)
    except (ValueError, TypeError):
        return [None] * CURVE_BINS
    vals: List[Optional[float]] = []
    for item in pairs[:CURVE_BINS]:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            v = item[1]
            vals.append(float(v) if v is not None else None)
        else:
            vals.append(None)
    while len(vals) < CURVE_BINS:
        vals.append(None)
    return vals


def _fill_curve(vals: List[Optional[float]], global_median: float) -> List[float]:
    """Forward-fill nulls, then back-fill leading nulls, then median for residuals."""
    out = list(vals)
    # forward
    last = None
    for i, v in enumerate(out):
        if v is None:
            out[i] = last
        else:
            last = v
    # backward
    last = None
    for i in range(len(out) - 1, -1, -1):
        if out[i] is None:
            out[i] = last
        else:
            last = out[i]
    # median fallback
    for i, v in enumerate(out):
        if v is None:
            out[i] = global_median
    return [float(x) for x in out]


def _zscore(vec: List[float]) -> List[float]:
    arr = np.array(vec, dtype=float)
    mu = arr.mean()
    sd = arr.std()
    if sd < 1e-9:
        return [0.0] * len(vec)
    return ((arr - mu) / sd).tolist()


def _subgenre_share(
    density_raw: Optional[str], top_keys: List[str]
) -> List[float]:
    """Return share-of-seconds for each top subgenre; 0 if key missing."""
    if not density_raw:
        return [0.0] * len(top_keys)
    try:
        density: Dict[str, float] = json.loads(density_raw)
    except (ValueError, TypeError):
        return [0.0] * len(top_keys)
    total = sum(float(v) for v in density.values()) or 1.0
    return [float(density.get(k, 0.0)) / total for k in top_keys]


def _top_subgenres(rows: List[sqlite3.Row], k: int) -> List[str]:
    """Most common subgenres across dataset, excluding '(unclassified)'."""
    counts: Counter = Counter()
    for r in rows:
        raw = r["genre_density"]
        if not raw:
            continue
        try:
            density: Dict[str, float] = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for sg, secs in density.items():
            if sg == "(unclassified)":
                continue
            counts[sg] += float(secs)
    return [sg for sg, _ in counts.most_common(k)]


def _global_curve_median(rows: List[sqlite3.Row], field: str) -> float:
    """Median across all non-null values in a curve field."""
    vals: List[float] = []
    for r in rows:
        for v in _parse_curve(r[field]):
            if v is not None:
                vals.append(v)
    if not vals:
        return 120.0 if field == "bpm_curve" else 0.5
    return float(statistics.median(vals))


def build_features(
    rows: List[sqlite3.Row],
) -> Tuple[np.ndarray, List[str], List[str]]:
    """Return (feature matrix, template_ids, top_subgenres)."""
    bpm_median = _global_curve_median(rows, "bpm_curve")
    nrg_median = _global_curve_median(rows, "energy_curve")
    top_subs = _top_subgenres(rows, TOP_SUBGENRES_K)

    feats: List[List[float]] = []
    ids: List[str] = []
    for r in rows:
        bpm = _fill_curve(_parse_curve(r["bpm_curve"]), bpm_median)
        nrg = _fill_curve(_parse_curve(r["energy_curve"]), nrg_median)
        bpm_z = _zscore(bpm)
        nrg_z = _zscore(nrg)
        pace = (r["pace_sec_median"] or 120) / 300.0
        edit = r["edit_density"] if r["edit_density"] is not None else 0.0
        dur_h = (r["duration_sec"] or 0) / 3600.0
        share = _subgenre_share(r["genre_density"], top_subs)

        vec = bpm_z + nrg_z + [pace, float(edit), dur_h] + share
        feats.append(vec)
        ids.append(r["template_id"])

    X = np.array(feats, dtype=float)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    return X_scaled, ids, top_subs


# --------------------------------------------------------------------------- #
# Clustering
# --------------------------------------------------------------------------- #


def _fit_k(X: np.ndarray, k: int) -> Tuple[np.ndarray, float]:
    k_eff = min(k, X.shape[0])
    km = KMeans(n_clusters=k_eff, random_state=42, n_init=10)
    labels = km.fit_predict(X)
    # silhouette needs >=2 clusters and n_samples > k
    if k_eff < 2 or X.shape[0] <= k_eff or len(set(labels)) < 2:
        score = -1.0
    else:
        try:
            score = float(silhouette_score(X, labels))
        except ValueError:
            score = -1.0
    return labels, score


def choose_clusters(X: np.ndarray) -> Tuple[np.ndarray, int, float]:
    """Try K=4 and K=6; pick better silhouette. Fall back to K=3/2 if any cluster
    is empty or we have too few templates."""
    candidates = [k for k in (4, 6) if k <= X.shape[0]]
    best_labels: Optional[np.ndarray] = None
    best_k = 0
    best_score = -2.0
    for k in candidates:
        labels, score = _fit_k(X, k)
        counts = Counter(labels.tolist())
        if len(counts) < k:
            # empty cluster — skip
            continue
        if score > best_score:
            best_score = score
            best_labels = labels
            best_k = k
    if best_labels is None:
        # fall back
        for k in (3, 2):
            if k > X.shape[0]:
                continue
            labels, score = _fit_k(X, k)
            counts = Counter(labels.tolist())
            if len(counts) < k:
                continue
            best_labels = labels
            best_k = k
            best_score = score
            break
    if best_labels is None:
        # degenerate — everything in one cluster
        best_labels = np.zeros(X.shape[0], dtype=int)
        best_k = 1
        best_score = -1.0
    return best_labels, best_k, best_score


# --------------------------------------------------------------------------- #
# Labeling
# --------------------------------------------------------------------------- #


def _median_bpm_range(rows: List[sqlite3.Row]) -> Tuple[int, int]:
    vals: List[float] = []
    for r in rows:
        for v in _parse_curve(r["bpm_curve"]):
            if v is not None:
                vals.append(v)
    if not vals:
        return (0, 0)
    vals.sort()
    lo = int(round(vals[max(0, int(len(vals) * 0.25))]))
    hi = int(round(vals[min(len(vals) - 1, int(len(vals) * 0.75))]))
    return (lo, hi)


def _pace_bucket(pace_sec: Optional[int]) -> str:
    if pace_sec is None:
        return "mixed pace"
    if pace_sec < 75:
        return "rapid mixing"
    if pace_sec < 150:
        return "steady mixing"
    return "long blends"


def _dominant_subgenre(rows: List[sqlite3.Row]) -> Optional[str]:
    counts: Counter = Counter()
    for r in rows:
        raw = r["genre_density"]
        if not raw:
            continue
        try:
            density: Dict[str, float] = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for sg, secs in density.items():
            if sg == "(unclassified)":
                continue
            counts[sg] += float(secs)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _venue_mode(rows: List[sqlite3.Row]) -> Optional[str]:
    hints = [r["venue_hint"] for r in rows if r["venue_hint"]]
    if not hints:
        return None
    counter = Counter(hints)
    top_hint, top_n = counter.most_common(1)[0]
    # dominant = over half of the cluster's templates (count all rows, not just
    # those with a hint), with a small-cluster escape hatch when every hinted
    # member agrees.
    if top_n > len(rows) / 2:
        return top_hint
    if len(counter) == 1 and top_n == len(hints):
        return top_hint
    return None


def _venue_pretty(venue: Optional[str]) -> str:
    if not venue:
        return "Mixed venue"
    special = {
        "boiler room": "Boiler Room",
        "burning man": "Burning Man",
        "armory": "Armory",
        "boat party": "Boat party",
        "bittersweet": "BitterSweet",
    }
    return special.get(venue.lower(), venue.title())


def derive_label(rows: List[sqlite3.Row]) -> Tuple[str, str]:
    """Return (label, description)."""
    venue = _venue_mode(rows)
    pretty_venue = _venue_pretty(venue)
    lo, hi = _median_bpm_range(rows)
    sub = _dominant_subgenre(rows) or "mixed"
    paces = [r["pace_sec_median"] for r in rows if r["pace_sec_median"]]
    pace_med = int(statistics.median(paces)) if paces else None
    pace_desc = _pace_bucket(pace_med)

    if lo and hi:
        label = f'{pretty_venue} — {sub} {lo}-{hi} BPM, {pace_desc}'
    else:
        label = f'{pretty_venue} — {sub}, {pace_desc}'

    desc_parts = [
        f"{sub} dominant",
        f"{lo}-{hi} BPM" if lo and hi else "BPM varied",
        pace_desc,
    ]
    description = "; ".join(desc_parts)
    return label, description


def typical_bpm_curve(rows: List[sqlite3.Row]) -> List[float]:
    stacks: List[List[Optional[float]]] = [_parse_curve(r["bpm_curve"]) for r in rows]
    out: List[float] = []
    for i in range(CURVE_BINS):
        vals = [s[i] for s in stacks if s[i] is not None]
        out.append(round(float(statistics.median(vals)), 2) if vals else 0.0)
    return out


def typical_genre_density(rows: List[sqlite3.Row]) -> Dict[str, float]:
    """Aggregate subgenre seconds across members, normalized to shares."""
    agg: Dict[str, float] = defaultdict(float)
    for r in rows:
        raw = r["genre_density"]
        if not raw:
            continue
        try:
            density: Dict[str, float] = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for sg, secs in density.items():
            agg[sg] += float(secs)
    total = sum(agg.values()) or 1.0
    shares = {sg: round(secs / total, 4) for sg, secs in agg.items()}
    # keep top 8 by share
    top = dict(sorted(shares.items(), key=lambda kv: kv[1], reverse=True)[:8])
    return top


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def ensure_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(set_templates)")}
    if "venue_cluster" not in cols:
        conn.execute("ALTER TABLE set_templates ADD COLUMN venue_cluster INTEGER")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS venue_clusters (
            cluster_id             INTEGER PRIMARY KEY,
            label                  TEXT NOT NULL,
            description            TEXT,
            member_count           INTEGER NOT NULL,
            typical_bpm_curve      TEXT,
            typical_genre_density  TEXT,
            silhouette             REAL,
            k                      INTEGER,
            created_at             TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def persist_clusters(
    conn: sqlite3.Connection,
    rows: List[sqlite3.Row],
    labels: np.ndarray,
    k: int,
    silhouette: float,
) -> List[Dict]:
    conn.execute("DELETE FROM venue_clusters")
    conn.execute("UPDATE set_templates SET venue_cluster = NULL")
    cluster_rows: List[Dict] = []
    members_by_cluster: Dict[int, List[sqlite3.Row]] = defaultdict(list)
    for row, cid in zip(rows, labels.tolist()):
        members_by_cluster[int(cid)].append(row)
        conn.execute(
            "UPDATE set_templates SET venue_cluster = ? WHERE template_id = ?",
            (int(cid), row["template_id"]),
        )

    for cid in sorted(members_by_cluster.keys()):
        members = members_by_cluster[cid]
        label, desc = derive_label(members)
        bpm_curve = typical_bpm_curve(members)
        genre_density = typical_genre_density(members)
        conn.execute(
            """
            INSERT INTO venue_clusters
              (cluster_id, label, description, member_count,
               typical_bpm_curve, typical_genre_density, silhouette, k)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cid,
                label,
                desc,
                len(members),
                json.dumps(bpm_curve),
                json.dumps(genre_density),
                silhouette,
                k,
            ),
        )
        cluster_rows.append(
            {
                "cluster_id": cid,
                "label": label,
                "description": desc,
                "member_count": len(members),
                "members": members,
            }
        )
    conn.commit()
    return cluster_rows


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _load_name_map(conn: sqlite3.Connection) -> Dict[str, str]:
    return {row[0]: row[1] for row in conn.execute("SELECT slug, name FROM djs")}


def _load_set_titles(conn: sqlite3.Connection) -> Dict[str, str]:
    return {row[0]: row[1] or "" for row in conn.execute("SELECT set_id, title FROM dj_sets")}


def _render(conn: sqlite3.Connection, markdown: bool) -> str:
    meta = conn.execute(
        "SELECT k, silhouette FROM venue_clusters ORDER BY cluster_id LIMIT 1"
    ).fetchone()
    if not meta:
        return "No clusters yet — run --fit first."
    k, silhouette = meta[0], meta[1]
    name_map = _load_name_map(conn)
    titles = _load_set_titles(conn)

    cluster_rows = conn.execute(
        """
        SELECT cluster_id, label, description, member_count
        FROM venue_clusters ORDER BY cluster_id
        """
    ).fetchall()

    lines: List[str] = []
    header = f"VENUE ARCHETYPES (K={k}, silhouette={silhouette:.2f})"
    if markdown:
        lines.append(f"# {header}")
        lines.append("")
    else:
        lines.append(header)
        lines.append("=" * 74)

    for cluster_id, label, description, member_count in cluster_rows:
        members = conn.execute(
            """
            SELECT template_id, dj_slug, source_set_id, role
            FROM set_templates WHERE venue_cluster = ? ORDER BY dj_slug
            """,
            (cluster_id,),
        ).fetchall()

        if markdown:
            lines.append(f'## Cluster {cluster_id} — "{label}" ({member_count} templates)')
            lines.append(f"*{description}*")
            lines.append("")
            lines.append("Members:")
            for m in members:
                dj_name = name_map.get(m[1], m[1])
                title = titles.get(m[2], "")
                lines.append(f"- {dj_name} — {title} ({m[3]})")
            lines.append("")
        else:
            lines.append("")
            lines.append(
                f'CLUSTER {cluster_id} — "{label}" ({member_count} templates)'
            )
            lines.append(f"  Typical: {description}")
            lines.append("  Members:")
            for m in members:
                dj_name = name_map.get(m[1], m[1])
                title = titles.get(m[2], "")
                lines.append(f"    - {dj_name} — {title} ({m[3]})")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def fit(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT template_id, source_set_id, dj_slug, role, duration_sec,
               bpm_curve, energy_curve, genre_density,
               pace_sec_median, edit_density, venue_hint
        FROM set_templates WHERE role='full'
        """
    ).fetchall()
    if not rows:
        print("No full-role templates found.")
        return

    ensure_schema(conn)
    X, ids, top_subs = build_features(rows)
    labels, k, silhouette = choose_clusters(X)
    persist_clusters(conn, rows, labels, k, silhouette)
    print(
        f"Fit complete — {len(rows)} templates into K={k} clusters "
        f"(silhouette={silhouette:.3f}). Top subgenres used in features: "
        f"{', '.join(top_subs) if top_subs else '(none)'}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--fit", action="store_true", help="Fit clusters and persist")
    ap.add_argument("--report", action="store_true", help="Print current clusters")
    ap.add_argument("--md", type=str, default=None, help="Write markdown report to PATH")
    args = ap.parse_args()

    if not (args.fit or args.report or args.md):
        ap.print_help()
        return

    conn = connect()
    conn.row_factory = sqlite3.Row
    try:
        if args.fit:
            fit(conn)
        if args.report:
            print(_render(conn, markdown=False))
        if args.md:
            text = _render(conn, markdown=True)
            with open(args.md, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print(f"Wrote {args.md}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
