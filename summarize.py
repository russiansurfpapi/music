"""Print a distribution summary of the classified library."""

import json
from collections import Counter

from db import connect


def _json_counter(conn, col: str) -> Counter:
    c: Counter = Counter()
    for row in conn.execute(f"SELECT {col} FROM classifications WHERE {col} IS NOT NULL"):
        val = row[0]
        try:
            items = json.loads(val) if val and val.startswith("[") else [val] if val else []
        except (json.JSONDecodeError, AttributeError):
            items = [val] if val else []
        for item in items:
            if item:
                c[item] += 1
    return c


def _single_counter(conn, col: str) -> Counter:
    c: Counter = Counter()
    for row in conn.execute(
        f"SELECT {col}, COUNT(*) FROM classifications "
        f"WHERE {col} IS NOT NULL AND {col} != '' GROUP BY {col}"
    ):
        c[row[0]] = row[1]
    return c


def _topn(counter: Counter, n: int = 10, total: int = 0) -> None:
    for label, count in counter.most_common(n):
        pct = f" ({100 * count / total:.1f}%)" if total else ""
        print(f"    {label:30s} {count:>6,}{pct}")


def main() -> None:
    with connect() as conn:
        total_tracks = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        classified = conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
        by_conf = dict(conn.execute(
            "SELECT confidence, COUNT(*) FROM classifications GROUP BY confidence"
        ).fetchall())
        by_lastfm = dict(conn.execute(
            "SELECT lastfm_status, COUNT(*) FROM tracks GROUP BY lastfm_status"
        ).fetchall())

        print("═" * 60)
        print(f"  YOUR LIBRARY — {total_tracks:,} tracks")
        print("═" * 60)
        print(f"\nClassified: {classified:,} / {total_tracks:,}")
        print(f"Confidence: high={by_conf.get('high', 0):,} "
              f"medium={by_conf.get('medium', 0):,} "
              f"low={by_conf.get('low', 0):,}")
        print(f"Last.fm:    track-level={by_lastfm.get('track', 0):,} "
              f"artist-level={by_lastfm.get('artist', 0):,} "
              f"missing={by_lastfm.get('missing', 0):,}")

        print("\n── Genre distribution ──")
        genres = _single_counter(conn, "genre")
        _topn(genres, 15, classified)

        print("\n── Subgenre distribution ──")
        subs = _single_counter(conn, "subgenre")
        _topn(subs, 20, classified)

        print("\n── Production DNA (top 10) ──")
        dna = _json_counter(conn, "production_dna")
        _topn(dna, 10, classified)

        print("\n── Rhythm ──")
        rhythms = _single_counter(conn, "rhythm")
        _topn(rhythms, 10, classified)

        print("\n── Texture/mood (top 10) ──")
        textures = _json_counter(conn, "texture")
        _topn(textures, 10, classified)

        print("\n── Lineage (top 15) ──")
        lineages = _json_counter(conn, "lineage")
        _topn(lineages, 15, classified)

        print("\n── Era (from release_year) ──")
        years = conn.execute(
            "SELECT release_year FROM tracks WHERE release_year IS NOT NULL"
        ).fetchall()
        eras: Counter = Counter()
        for (y,) in years:
            if y < 1970:   eras["pre-1970s"] += 1
            elif y < 1980: eras["1970s"] += 1
            elif y < 1990: eras["1980s"] += 1
            elif y < 2000: eras["1990s"] += 1
            elif y < 2010: eras["2000s"] += 1
            elif y < 2020: eras["2010s"] += 1
            else:          eras["2020s"] += 1
        for era in ["pre-1970s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"]:
            if eras[era]:
                pct = 100 * eras[era] / len(years)
                print(f"    {era:30s} {eras[era]:>6,} ({pct:.1f}%)")

        print("\n" + "═" * 60)

        # One-line vibe summary
        top_genre = genres.most_common(1)[0] if genres else None
        top_dna = dna.most_common(1)[0] if dna else None
        post_2010 = sum(eras[e] for e in ["2010s", "2020s"])
        post_2010_pct = 100 * post_2010 / len(years) if years else 0
        if top_genre and top_dna:
            g_pct = 100 * top_genre[1] / classified
            print(f"\n  Your library is {g_pct:.0f}% {top_genre[0]}.")
            print(f"  Most common production element: {top_dna[0]}.")
            print(f"  {post_2010_pct:.0f}% post-2010 — "
                  f"{'missing roots' if post_2010_pct > 80 else 'good era spread'}.")
        print()


if __name__ == "__main__":
    main()
