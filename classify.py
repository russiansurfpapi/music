"""Classify tracks into 6 layers (genre / subgenre / production_dna / rhythm / texture / lineage).

Usage:
    python3 classify.py        # classify all tracks with lastfm_status != 'missing'

Pure API:
    classify_track(raw_tags, artist_genres, release_year) -> dict
"""

import json
import logging
import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import yaml

import db

_HERE = os.path.dirname(os.path.abspath(__file__))
TAG_MAP_PATH = os.path.join(_HERE, "tag_map.yaml")
UNMAPPED_LOG = os.path.join(_HERE, "unmapped_tags.log")

SINGULAR_LAYERS = ("genre", "subgenre", "rhythm")
MULTI_LAYERS = ("production_dna", "texture", "lineage")
ALL_LAYERS = SINGULAR_LAYERS + MULTI_LAYERS


def load_tag_map(path: str = TAG_MAP_PATH):
    """Returns (reverse_index, skip_tags_set)."""
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    reverse: Dict[str, Dict[str, str]] = {}
    for layer in ALL_LAYERS:
        reverse[layer] = {}
        for canonical, patterns in (data.get(layer) or {}).items():
            for p in patterns:
                reverse[layer][str(p).lower().strip()] = canonical
    skip = {str(t).lower().strip() for t in (data.get("skip_tags") or [])}
    return reverse, skip


_TAG_MAP: Optional[Dict[str, Dict[str, str]]] = None
_SKIP_TAGS: Optional[set] = None


def _get_map() -> Dict[str, Dict[str, str]]:
    global _TAG_MAP, _SKIP_TAGS
    if _TAG_MAP is None:
        _TAG_MAP, _SKIP_TAGS = load_tag_map()
    return _TAG_MAP


def _get_skip() -> set:
    global _TAG_MAP, _SKIP_TAGS
    if _SKIP_TAGS is None:
        _TAG_MAP, _SKIP_TAGS = load_tag_map()
    return _SKIP_TAGS


def _era_from_year(year: Optional[int]) -> Optional[str]:
    if not year:
        return None
    if 1970 <= year <= 1979:
        return "70s"
    if 1980 <= year <= 1989:
        return "80s"
    if 1990 <= year <= 1999:
        return "90s"
    if 2000 <= year <= 2009:
        return "00s"
    if 2010 <= year <= 2019:
        return "10s"
    if 2020 <= year <= 2029:
        return "20s"
    return None


def classify_track(
    raw_tags: List[Dict],
    artist_genres: List[str],
    release_year: Optional[int],
) -> Dict:
    """Pure classifier. raw_tags = [{tag, count, level}], artist_genres = list[str]."""
    tag_map = _get_map()
    skip = _get_skip()

    # Normalize inputs into weighted (tag, weight, level) stream.
    # Track tags: use count (1..100). Artist-level: scale down (weight * 0.5).
    # Spotify artist_genres: treat as weight 10, level='spotify'.
    weighted: List[Tuple[str, float, str]] = []
    has_track = False
    has_artist = False
    for rt in raw_tags or []:
        tag = (rt.get("tag") or "").lower().strip()
        if not tag or tag in skip:
            continue
        count = float(rt.get("count") or 0)
        level = rt.get("level") or "track"
        if level == "track":
            has_track = True
            w = max(count, 1.0)
        else:
            has_artist = True
            w = max(count, 1.0) * 0.5
        weighted.append((tag, w, level))

    has_spotify = False
    for g in artist_genres or []:
        tag = (g or "").lower().strip()
        if not tag or tag in skip:
            continue
        has_spotify = True
        weighted.append((tag, 10.0, "spotify"))

    # Accumulate scores per (layer, canonical)
    scores: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    matched_tags = set()
    for tag, w, _lvl in weighted:
        for layer in ALL_LAYERS:
            canonical = tag_map[layer].get(tag)
            if canonical:
                scores[layer][canonical] += w
                matched_tags.add(tag)

    result: Dict = {
        "genre": None,
        "subgenre": None,
        "production_dna": [],
        "rhythm": None,
        "texture": [],
        "lineage": [],
        "confidence": "low",
    }

    for layer in SINGULAR_LAYERS:
        if scores[layer]:
            best = max(scores[layer].items(), key=lambda kv: kv[1])
            result[layer] = best[0]

    for layer in MULTI_LAYERS:
        if scores[layer]:
            ranked = sorted(scores[layer].items(), key=lambda kv: kv[1], reverse=True)
            result[layer] = [c for c, _ in ranked]

    # Era fallback via release_year — only if no explicit era already in lineage.
    era_labels = {"70s", "80s", "90s", "00s", "10s", "20s"}
    if not any(l in era_labels for l in result["lineage"]):
        era = _era_from_year(release_year)
        if era:
            result["lineage"].append(era)

    # Confidence
    if has_track:
        result["confidence"] = "high"
    elif has_artist:
        result["confidence"] = "medium"
    elif has_spotify:
        result["confidence"] = "low"
    else:
        result["confidence"] = "low"

    # Expose unmapped tags for logging (non-API, stored under _meta)
    unmapped = [t for (t, _w, _l) in weighted if t not in matched_tags]
    result["_unmapped"] = unmapped

    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    conn = db.connect()
    try:
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT spotify_id, artist_id, release_year FROM tracks "
            "WHERE lastfm_status != 'missing' OR lastfm_status IS NULL"
        ).fetchall()

        unmapped_counts: Dict[str, int] = defaultdict(int)
        n_done = 0
        for row in rows:
            sid = row["spotify_id"]
            artist_id = row["artist_id"]
            year = row["release_year"]

            tags = cur.execute(
                "SELECT tag, count, level FROM track_tags WHERE spotify_id = ?",
                (sid,),
            ).fetchall()
            raw_tags = [{"tag": t["tag"], "count": t["count"], "level": t["level"]} for t in tags]

            artist_genres: List[str] = []
            if artist_id:
                gs = cur.execute(
                    "SELECT genre FROM artist_genres WHERE artist_id = ?",
                    (artist_id,),
                ).fetchall()
                artist_genres = [g["genre"] for g in gs]

            cls = classify_track(raw_tags, artist_genres, year)

            for t in cls.pop("_unmapped", []):
                unmapped_counts[t] += 1

            cur.execute(
                """INSERT INTO classifications
                   (spotify_id, genre, subgenre, production_dna, rhythm, texture, lineage, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(spotify_id) DO UPDATE SET
                     genre=excluded.genre,
                     subgenre=excluded.subgenre,
                     production_dna=excluded.production_dna,
                     rhythm=excluded.rhythm,
                     texture=excluded.texture,
                     lineage=excluded.lineage,
                     confidence=excluded.confidence,
                     classified_at=CURRENT_TIMESTAMP""",
                (
                    sid,
                    cls["genre"],
                    cls["subgenre"],
                    json.dumps(cls["production_dna"]),
                    cls["rhythm"],
                    json.dumps(cls["texture"]),
                    json.dumps(cls["lineage"]),
                    cls["confidence"],
                ),
            )
            n_done += 1
            if n_done % 200 == 0:
                conn.commit()
                logging.info("classified %d tracks", n_done)

        conn.commit()
        logging.info("classified %d tracks total", n_done)

        with open(UNMAPPED_LOG, "w") as f:
            for tag, cnt in sorted(unmapped_counts.items(), key=lambda kv: -kv[1]):
                f.write("{}\t{}\n".format(cnt, tag))
        logging.info("wrote %d unmapped tags to %s", len(unmapped_counts), UNMAPPED_LOG)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
