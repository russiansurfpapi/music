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

# Backfill genre from subgenre when the subgenre picks up but the genre doesn't
# (e.g. tags were all meta like "electronic"/"dance" that we now drop from genre).
SUBGENRE_TO_GENRE = {
    # house family
    "acid house": "house", "deep house": "house", "tech house": "house",
    "afro house": "house", "chicago house": "house", "french house": "house",
    "garage house": "house", "disco house": "house", "funky house": "house",
    "soulful house": "house", "tribal house": "house", "outsider house": "house",
    "lo-fi house": "house", "tropical house": "house", "jackin house": "house",
    "bass house": "house", "future house": "house", "ghetto house": "house",
    "hip house": "house", "progressive house": "house", "hard house": "house",
    "microhouse": "house",
    # techno family
    "minimal techno": "techno", "detroit techno": "techno",
    "industrial techno": "techno", "melodic techno": "techno",
    "acid techno": "techno", "dub techno": "techno",
    "ambient techno": "techno", "ambient house": "house",
    "peak time techno": "techno", "hard techno": "techno",
    # trance
    "psytrance": "trance", "goa trance": "trance", "progressive trance": "trance",
    # dnb / dubstep / garage
    "jungle/dnb": "dnb", "liquid dnb": "dnb", "neurofunk": "dnb", "jump up": "dnb",
    "dubstep (original)": "dubstep", "post-dubstep": "dubstep", "brostep": "dubstep",
    "uk garage": "garage", "speed garage": "garage", "two-step": "garage",
    "grime": "uk bass", "future bass": "uk bass",
    # hip-hop
    "trap": "hip-hop", "boom bap": "hip-hop", "lo-fi": "hip-hop",
    "jazz rap": "hip-hop", "instrumental hip hop": "hip-hop",
    "conscious hip hop": "hip-hop", "gangsta rap": "hip-hop",
    "southern rap": "hip-hop", "west coast rap": "hip-hop",
    "uk rap": "hip-hop", "underground hip hop": "hip-hop",
    "cloud rap": "hip-hop", "emo rap": "hip-hop", "drill": "hip-hop",
    "g-funk": "hip-hop", "pop rap": "hip-hop", "trap rap": "hip-hop",
    # latin / world / club
    "trap latino": "reggaetón", "neoperreo": "reggaetón",
    "dembow": "reggaetón",
    "afrobeats": "world", "amapiano": "world", "moombahton": "world",
    "funk carioca": "world",
    "baltimore club": "uk bass", "jersey club": "uk bass",
    "ghettotech": "uk bass", "miami bass": "hip-hop",
    "gqom": "uk bass", "ballroom": "uk bass",
    # disco / era dance
    "italo disco": "disco-funk-soul", "nu-disco": "disco-funk-soul",
    "cosmic disco": "disco-funk-soul", "balearic": "disco-funk-soul",
    "electro": "disco-funk-soul", "eurodance": "pop",
    # pop variants
    "dream pop": "pop", "art pop": "pop", "bedroom pop": "pop",
    "hyperpop": "pop", "chillwave": "pop", "synth pop": "pop",
    "electropop": "pop", "indie dance": "pop",
    # r&b variants
    "neo-soul": "disco-funk-soul", "alternative rnb": "disco-funk-soul",
    # idm / experimental
    "footwork": "breaks", "breakcore": "breaks",
    # reggae / dub
    "dub": "dub", "deconstructed club": "uk bass",
}

# After subgenre is determined, nudge production_dna scores. Additive weight ≈ a
# strong artist-level tag. Catches the canonical "this subgenre implies this gear"
# relationships that Last.fm tags don't carry (909/808/breakbeat lineage).
SUBGENRE_DNA = {
    "chicago house": ["909"],
    "detroit techno": ["909"],
    "acid house": ["909", "303"],
    "acid techno": ["909", "303"],
    "minimal techno": ["909"],
    "microhouse": ["909"],
    "dub techno": ["909", "analog"],
    "melodic techno": ["analog"],
    "french house": ["909", "sample-based"],
    "garage house": ["909"],
    "disco house": ["909", "sample-based"],
    "trap": ["808"],
    "drill": ["808"],
    "southern rap": ["808"],
    "miami bass": ["808"],
    "footwork": ["808", "breakbeat-based"],
    "jungle/dnb": ["breakbeat-based"],
    "boom bap": ["sample-based"],
    "instrumental hip hop": ["sample-based"],
    "jazz rap": ["sample-based"],
    "lo-fi": ["sample-based"],
    "grime": ["808"],
    "italo disco": ["analog", "synth-driven"],
    "nu-disco": ["analog", "synth-driven"],
    "cosmic disco": ["analog", "synth-driven"],
    "synth pop": ["synth-driven"],
    "electropop": ["synth-driven"],
    "hyperpop": ["vocal processing"],
}
SUBGENRE_DNA_BONUS = 15.0

# Disambiguation: single-word ambiguous tags that collide across subgenres.
# Resolved by looking at co-occurring tags in the same track.
_HOUSE_CUES = {
    "house", "deep house", "tech house", "disco house", "funky house",
    "soulful house", "chicago house", "french house", "garage house",
    "lo-fi house", "acid house", "afro house", "microhouse",
}
_TRANCE_CUES = {"trance", "psytrance", "goa", "goa trance", "uplifting"}
_INDUSTRIAL_EXCL = {
    "hip-hop", "hip hop", "rap", "rock", "metal", "industrial rock",
    "industrial metal", "post-industrial",
}
_ACID_EXCL = {
    "jazz", "acid jazz", "rock", "psychedelic rock", "psychedelic",
    "folk", "blues",
}


def _disambiguate(scores: Dict[str, Dict[str, float]], all_tags: set) -> None:
    """Mutate scores in place to resolve known ambiguous single-word tags."""
    # minimal: respect explicit "minimal techno" tag; only suppress bare "minimal" with house cues
    has_explicit_mt = any(t in all_tags for t in ("minimal techno", "minimal-techno"))
    if "minimal" in all_tags and (all_tags & _HOUSE_CUES) and not has_explicit_mt:
        if "minimal techno" in scores.get("subgenre", {}):
            scores["subgenre"]["minimal techno"] *= 0.3
        scores["subgenre"]["deep house"] = scores["subgenre"].get("deep house", 0) + 2.0
    if has_explicit_mt or ("minimal" in all_tags and "techno" in all_tags and not (all_tags & _HOUSE_CUES)):
        scores["subgenre"]["minimal techno"] = scores["subgenre"].get("minimal techno", 0) + 3.0

    # acid: suppress acid house when jazz/rock/psychedelic cues dominate
    if "acid" in all_tags and (all_tags & _ACID_EXCL):
        has_explicit_acid_house = any(t in all_tags for t in ("acid house", "acid techno"))
        if not has_explicit_acid_house:
            if "acid house" in scores.get("subgenre", {}):
                scores["subgenre"]["acid house"] *= 0.2

    # industrial: if hip-hop/rock cues are present, suppress industrial techno (stronger)
    if "industrial" in all_tags and (all_tags & _INDUSTRIAL_EXCL):
        if "industrial techno" in scores.get("subgenre", {}):
            has_explicit_it = "industrial techno" in all_tags
            if not has_explicit_it:
                scores["subgenre"]["industrial techno"] *= 0.1

    # progressive: if trance cues are present, boost progressive trance over progressive house
    if "progressive" in all_tags and (all_tags & _TRANCE_CUES):
        if "progressive house" in scores.get("subgenre", {}):
            scores["subgenre"]["progressive house"] *= 0.4
        scores["subgenre"]["progressive trance"] = (
            scores["subgenre"].get("progressive trance", 0) + 3.0
        )


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
    all_tags = set()
    for tag, w, _lvl in weighted:
        all_tags.add(tag)
        for layer in ALL_LAYERS:
            canonical = tag_map[layer].get(tag)
            if canonical:
                scores[layer][canonical] += w
                matched_tags.add(tag)

    _disambiguate(scores, all_tags)

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

    # Subgenre→genre backfill: with meta-tags removed from genre, tracks that only
    # carry "electronic"/"dance" get NULL genre even when subgenre is clear. Map it back.
    if result["subgenre"] and not result["genre"]:
        result["genre"] = SUBGENRE_TO_GENRE.get(result["subgenre"])

    # Subgenre→DNA inference: nudge production_dna scores for canonical lineages
    # (chicago house→909, trap→808, footwork→breakbeat-based, etc.)
    if result["subgenre"] and result["subgenre"] in SUBGENRE_DNA:
        for dna in SUBGENRE_DNA[result["subgenre"]]:
            scores["production_dna"][dna] += SUBGENRE_DNA_BONUS

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
