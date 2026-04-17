"""DJ-set genre/trend analysis — pure SQL on library.db.

Usage:
    python3 dj_report.py --list-djs
    python3 dj_report.py --dj shadow-child                # summary of all questions
    python3 dj_report.py --dj shadow-child --by genre     # genre/subgenre breakdown
    python3 dj_report.py --dj shadow-child --by subgenre
    python3 dj_report.py --dj shadow-child --evolution    # year-by-year
    python3 dj_report.py --dj shadow-child --repeats      # anchor tracks/artists
    python3 dj_report.py --dj shadow-child --anatomy      # set lengths, openers/closers
    python3 dj_report.py --dj shadow-child --remixes      # original vs remix rate
    python3 dj_report.py --set <set_id>                   # single-set position-by-position
    python3 dj_report.py --who-plays "acid house"         # rank DJs by subgenre
    python3 dj_report.py --compare shadow-child,kink      # side-by-side genre breakdown
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from typing import List, Optional

from db import connect

REMIX_RE = re.compile(r"\b(remix|rmx|edit|rework|bootleg|dub|mix|vip|version|flip)\b", re.I)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_dj(conn, slug_or_name: str) -> Optional[dict]:
    """Find a DJ by slug or fuzzy name."""
    row = conn.execute(
        "SELECT slug, name FROM djs WHERE slug = ? OR lower(name) = lower(?)",
        (slug_or_name, slug_or_name),
    ).fetchone()
    if row:
        return dict(row)
    # Fuzzy fallback
    row = conn.execute(
        "SELECT slug, name FROM djs WHERE lower(name) LIKE ? LIMIT 1",
        (f"%{slug_or_name.lower()}%",),
    ).fetchone()
    return dict(row) if row else None


def _pct(n: int, d: int) -> str:
    if not d:
        return "0%"
    return f"{100.0 * n / d:.1f}%"


def _bar(n: int, total: int, width: int = 30) -> str:
    if not total:
        return ""
    filled = int(round(width * n / total))
    return "█" * filled + "·" * (width - filled)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_djs(conn, only_with_sets: bool = True) -> None:
    rows = conn.execute("""
        SELECT d.slug, d.name, d.is_favorite,
               COUNT(DISTINCT s.set_id) AS sets,
               COALESCE(SUM(s.track_count), 0) AS tracks
        FROM djs d
        LEFT JOIN dj_sets s ON s.dj_slug = d.slug
        GROUP BY d.slug
        HAVING sets > 0 OR d.is_favorite = 1
        ORDER BY d.is_favorite DESC, tracks DESC
        LIMIT 30
    """).fetchall()
    print(f"{'Slug':<25} {'Name':<25} {'Fav':<4} {'Sets':>5} {'Tracks':>7}")
    print("-" * 70)
    for r in rows:
        fav = "★" if r["is_favorite"] else ""
        print(f"{r['slug']:<25} {r['name'][:24]:<25} {fav:<4} {r['sets']:>5} {r['tracks']:>7}")


def by_layer(conn, dj_slug: str, layer: str) -> None:
    """Top values for a classification layer."""
    if layer in ("production_dna", "texture", "lineage"):
        # JSON array — unpack in Python
        rows = conn.execute(f"""
            SELECT c.{layer} AS val
            FROM dj_set_tracks dst
            JOIN dj_sets ds ON ds.set_id = dst.set_id
            JOIN classifications c ON c.spotify_id = dst.spotify_id
            WHERE ds.dj_slug = ? AND c.{layer} IS NOT NULL
        """, (dj_slug,)).fetchall()
        counts: Counter = Counter()
        for r in rows:
            try:
                items = json.loads(r["val"]) if r["val"] else []
            except (json.JSONDecodeError, TypeError):
                items = []
            for it in items:
                if it:
                    counts[it] += 1
        items = counts.most_common(20)
    else:
        rows = conn.execute(f"""
            SELECT c.{layer} AS val, COUNT(*) AS n
            FROM dj_set_tracks dst
            JOIN dj_sets ds ON ds.set_id = dst.set_id
            JOIN classifications c ON c.spotify_id = dst.spotify_id
            WHERE ds.dj_slug = ? AND c.{layer} IS NOT NULL
            GROUP BY c.{layer}
            ORDER BY n DESC
            LIMIT 20
        """, (dj_slug,)).fetchall()
        items = [(r["val"], r["n"]) for r in rows]

    if not items:
        print(f"  No {layer} data classified.")
        return
    total = sum(n for _, n in items)
    print(f"\n=== {layer.upper()} (top {len(items)}) ===")
    for val, n in items:
        print(f"  {n:>4}  {_pct(n, total):>6}  {_bar(n, items[0][1])}  {val}")


def evolution(conn, dj_slug: str) -> None:
    """Year-by-year breakdown of dominant subgenres."""
    rows = conn.execute("""
        SELECT substr(ds.set_date, 1, 4) AS year,
               c.subgenre, c.genre,
               COUNT(*) AS n
        FROM dj_set_tracks dst
        JOIN dj_sets ds ON ds.set_id = dst.set_id
        JOIN classifications c ON c.spotify_id = dst.spotify_id
        WHERE ds.dj_slug = ? AND ds.set_date IS NOT NULL
        GROUP BY year, c.subgenre, c.genre
    """, (dj_slug,)).fetchall()
    by_year: defaultdict = defaultdict(Counter)
    for r in rows:
        label = r["subgenre"] or r["genre"] or "?"
        by_year[r["year"]][label] += r["n"]

    print(f"\n=== EVOLUTION (year-by-year top 3) ===")
    for year in sorted(by_year.keys()):
        top = by_year[year].most_common(3)
        total = sum(by_year[year].values())
        items = ", ".join(f"{label} ({_pct(n, total)})" for label, n in top)
        print(f"  {year} [{total:>3} tracks]  {items}")


def repeats(conn, dj_slug: str) -> None:
    """Artists/tracks that appear in 2+ sets."""
    print("\n=== ARTIST REPEATS (2+ sets) ===")
    rows = conn.execute("""
        SELECT raw_artist, COUNT(DISTINCT ds.set_id) AS n_sets, COUNT(*) AS n_tracks
        FROM dj_set_tracks dst
        JOIN dj_sets ds ON ds.set_id = dst.set_id
        WHERE ds.dj_slug = ?
        GROUP BY lower(raw_artist)
        HAVING COUNT(DISTINCT ds.set_id) >= 2
        ORDER BY n_sets DESC, n_tracks DESC
        LIMIT 20
    """, (dj_slug,)).fetchall()
    for r in rows:
        print(f"  {r['n_sets']:>3} sets  {r['n_tracks']:>3} plays  {r['raw_artist']}")
    if not rows:
        print("  (none)")

    print("\n=== ANCHOR TRACKS (played in 2+ sets) ===")
    rows = conn.execute("""
        SELECT raw_artist, raw_title, COUNT(DISTINCT ds.set_id) AS n_sets
        FROM dj_set_tracks dst
        JOIN dj_sets ds ON ds.set_id = dst.set_id
        WHERE ds.dj_slug = ?
        GROUP BY lower(raw_artist), lower(raw_title)
        HAVING COUNT(DISTINCT ds.set_id) >= 2
        ORDER BY n_sets DESC
        LIMIT 15
    """, (dj_slug,)).fetchall()
    for r in rows:
        print(f"  {r['n_sets']:>3} sets  {r['raw_artist']} — {r['raw_title']}")
    if not rows:
        print("  (none)")


def anatomy(conn, dj_slug: str) -> None:
    """Set length stats + opener/closer subgenre patterns."""
    rows = conn.execute("""
        SELECT set_id, set_date, track_count
        FROM dj_sets
        WHERE dj_slug = ? AND track_count > 0
        ORDER BY set_date
    """, (dj_slug,)).fetchall()

    print("\n=== SET ANATOMY ===")
    if not rows:
        print("  No sets with tracks.")
        return
    counts = [r["track_count"] for r in rows]
    print(f"  Sets:          {len(rows)}")
    print(f"  Tracks/set:    avg {sum(counts)/len(counts):.1f}, min {min(counts)}, max {max(counts)}")

    # Year mix
    yr_rows = conn.execute("""
        SELECT t.release_year, COUNT(*) AS n
        FROM dj_set_tracks dst
        JOIN dj_sets ds ON ds.set_id = dst.set_id
        JOIN tracks t ON t.spotify_id = dst.spotify_id
        WHERE ds.dj_slug = ? AND t.release_year IS NOT NULL
        GROUP BY t.release_year ORDER BY t.release_year
    """, (dj_slug,)).fetchall()
    if yr_rows:
        years = [(r["release_year"], r["n"]) for r in yr_rows]
        total = sum(n for _, n in years)
        weighted_avg = sum(y * n for y, n in years) / total
        oldest = years[0][0]
        newest = years[-1][0]
        print(f"  Track years:   range {oldest}-{newest}, avg {weighted_avg:.0f} (n={total})")

    # Opener / closer subgenre patterns
    print("\n=== OPENERS (track #1) ===")
    open_rows = conn.execute("""
        SELECT dst.raw_artist, dst.raw_title, c.subgenre, c.genre
        FROM dj_set_tracks dst
        JOIN dj_sets ds ON ds.set_id = dst.set_id
        LEFT JOIN classifications c ON c.spotify_id = dst.spotify_id
        WHERE ds.dj_slug = ? AND dst.position = 1
        ORDER BY ds.set_date
    """, (dj_slug,)).fetchall()
    for r in open_rows:
        sub = r["subgenre"] or r["genre"] or "?"
        print(f"  [{sub:<20}] {r['raw_artist']} — {r['raw_title'][:50]}")

    print("\n=== CLOSERS (last track) ===")
    close_rows = conn.execute("""
        SELECT dst.raw_artist, dst.raw_title, c.subgenre, c.genre
        FROM dj_set_tracks dst
        JOIN dj_sets ds ON ds.set_id = dst.set_id
        LEFT JOIN classifications c ON c.spotify_id = dst.spotify_id
        WHERE ds.dj_slug = ? AND dst.position = ds.track_count
        ORDER BY ds.set_date
    """, (dj_slug,)).fetchall()
    for r in close_rows:
        sub = r["subgenre"] or r["genre"] or "?"
        print(f"  [{sub:<20}] {r['raw_artist']} — {r['raw_title'][:50]}")


def remixes(conn, dj_slug: str) -> None:
    """Original vs remix/edit rate."""
    rows = conn.execute("""
        SELECT raw_title FROM dj_set_tracks dst
        JOIN dj_sets ds ON ds.set_id = dst.set_id
        WHERE ds.dj_slug = ?
    """, (dj_slug,)).fetchall()
    if not rows:
        return
    n_remix = sum(1 for r in rows if REMIX_RE.search(r["raw_title"]))
    n_total = len(rows)
    print(f"\n=== REMIX/EDIT RATE ===")
    print(f"  Total tracks:    {n_total}")
    print(f"  With remix tag:  {n_remix} ({_pct(n_remix, n_total)})")
    print(f"  Originals:       {n_total - n_remix} ({_pct(n_total - n_remix, n_total)})")


def show_set(conn, set_id: str) -> None:
    """Position-by-position view of one set."""
    ds = conn.execute(
        "SELECT * FROM dj_sets WHERE set_id = ?", (set_id,)
    ).fetchone()
    if not ds:
        print(f"No set: {set_id}")
        return
    print(f"\n=== {ds['set_id']} ({ds['set_date']}) — {ds['track_count']} tracks ===")
    rows = conn.execute("""
        SELECT dst.position, dst.raw_artist, dst.raw_title, c.subgenre, c.genre
        FROM dj_set_tracks dst
        LEFT JOIN classifications c ON c.spotify_id = dst.spotify_id
        WHERE dst.set_id = ?
        ORDER BY dst.position
    """, (set_id,)).fetchall()
    for r in rows:
        tag = r["subgenre"] or r["genre"] or "?"
        title = r["raw_title"][:55]
        print(f"  {r['position']:>3}.  [{tag[:18]:<18}]  {r['raw_artist'][:25]:<25}  {title}")


def by_artist(conn, artist: str) -> None:
    """Show all tracks by an artist across any DJ's sets, with classifications."""
    rows = conn.execute("""
        SELECT dst.raw_artist, dst.raw_title, ds.dj_slug, ds.set_date,
               c.subgenre, c.genre
        FROM dj_set_tracks dst
        JOIN dj_sets ds ON ds.set_id = dst.set_id
        LEFT JOIN classifications c ON c.spotify_id = dst.spotify_id
        WHERE lower(dst.raw_artist) LIKE ?
        ORDER BY ds.set_date, ds.dj_slug
    """, (f"%{artist.lower()}%",)).fetchall()
    if not rows:
        print(f"  No tracks found by '{artist}'.")
        return

    print(f"\n=== TRACKS BY '{artist}' across all sets ({len(rows)}) ===")
    for r in rows:
        sub = r["subgenre"] or r["genre"] or "?"
        print(f"  [{sub[:18]:<18}]  {r['set_date'] or '????-??-??'}  {r['dj_slug'][:18]:<18}  {r['raw_artist']} — {r['raw_title'][:55]}")

    # Subgenre summary
    counts: Counter = Counter()
    for r in rows:
        sub = r["subgenre"] or r["genre"]
        if sub:
            counts[sub] += 1
    if counts:
        print(f"\n=== Subgenre/genre summary ===")
        total = sum(counts.values())
        for label, n in counts.most_common():
            print(f"  {n:>3}  {_pct(n, total):>6}  {label}")


def who_plays(conn, subgenre: str) -> None:
    """Rank DJs by tracks tagged with this subgenre."""
    rows = conn.execute("""
        SELECT d.slug, d.name,
               COUNT(*) AS n_tracks,
               (SELECT COUNT(*) FROM dj_set_tracks dst2
                JOIN dj_sets ds2 ON ds2.set_id = dst2.set_id
                WHERE ds2.dj_slug = d.slug AND dst2.spotify_id IS NOT NULL) AS denom
        FROM djs d
        JOIN dj_sets ds ON ds.dj_slug = d.slug
        JOIN dj_set_tracks dst ON dst.set_id = ds.set_id
        JOIN classifications c ON c.spotify_id = dst.spotify_id
        WHERE lower(c.subgenre) = lower(?)
        GROUP BY d.slug
        ORDER BY n_tracks DESC
        LIMIT 15
    """, (subgenre,)).fetchall()
    print(f"\n=== WHO PLAYS '{subgenre}' ===")
    for r in rows:
        pct = _pct(r["n_tracks"], r["denom"])
        print(f"  {r['n_tracks']:>4}  {pct:>6}  {r['name']}")
    if not rows:
        print(f"  (no tracks tagged '{subgenre}')")


def compare(conn, slugs: List[str]) -> None:
    """Side-by-side subgenre breakdown."""
    cols = []
    for slug in slugs:
        rows = conn.execute("""
            SELECT c.subgenre, COUNT(*) AS n
            FROM dj_set_tracks dst
            JOIN dj_sets ds ON ds.set_id = dst.set_id
            JOIN classifications c ON c.spotify_id = dst.spotify_id
            WHERE ds.dj_slug = ? AND c.subgenre IS NOT NULL
            GROUP BY c.subgenre
        """, (slug,)).fetchall()
        c = Counter({r["subgenre"]: r["n"] for r in rows})
        cols.append((slug, c))

    all_subgenres = set()
    for _, c in cols:
        all_subgenres.update(c.keys())

    # Rank by combined frequency
    combined = Counter()
    for _, c in cols:
        combined.update(c)
    ordered = [s for s, _ in combined.most_common(20)]

    print(f"\n=== SUBGENRE COMPARISON ===")
    header = f"{'subgenre':<25} " + " ".join(f"{s[:12]:>12}" for s, _ in cols)
    print(header)
    print("-" * len(header))
    for sub in ordered:
        line = f"{sub[:24]:<25} "
        for slug, c in cols:
            n = c.get(sub, 0)
            total = sum(c.values())
            pct = (100.0 * n / total) if total else 0
            line += f"{n:>4} ({pct:>4.1f}%) "
        print(line)


def summary(conn, dj_slug: str) -> None:
    """Run all reports for one DJ."""
    dj = _resolve_dj(conn, dj_slug)
    if not dj:
        print(f"No DJ: {dj_slug}")
        return
    print(f"\n{'=' * 60}\n  REPORT — {dj['name']}\n{'=' * 60}")
    by_layer(conn, dj["slug"], "subgenre")
    by_layer(conn, dj["slug"], "genre")
    by_layer(conn, dj["slug"], "rhythm")
    by_layer(conn, dj["slug"], "production_dna")
    by_layer(conn, dj["slug"], "texture")
    anatomy(conn, dj["slug"])
    evolution(conn, dj["slug"])
    repeats(conn, dj["slug"])
    remixes(conn, dj["slug"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--list-djs", action="store_true")
    p.add_argument("--dj", help="DJ slug or name")
    p.add_argument("--by", choices=["genre", "subgenre", "rhythm", "production_dna", "texture", "lineage"])
    p.add_argument("--evolution", action="store_true")
    p.add_argument("--repeats", action="store_true")
    p.add_argument("--anatomy", action="store_true")
    p.add_argument("--remixes", action="store_true")
    p.add_argument("--set", dest="set_id")
    p.add_argument("--who-plays", dest="who_plays")
    p.add_argument("--artist", help="show all tracks by this artist across any DJ's sets")
    p.add_argument("--compare", help="comma-separated slugs")
    args = p.parse_args()

    conn = connect()
    try:
        if args.list_djs:
            list_djs(conn)
            return
        if args.set_id:
            show_set(conn, args.set_id)
            return
        if args.who_plays:
            who_plays(conn, args.who_plays)
            return
        if args.artist:
            by_artist(conn, args.artist)
            return
        if args.compare:
            slugs = [s.strip() for s in args.compare.split(",")]
            compare(conn, slugs)
            return
        if not args.dj:
            p.print_help()
            return
        dj = _resolve_dj(conn, args.dj)
        if not dj:
            print(f"No DJ matching '{args.dj}'. Try --list-djs.")
            return

        if args.by:
            print(f"\n=== {dj['name']} — by {args.by} ===")
            by_layer(conn, dj["slug"], args.by)
        elif args.evolution:
            print(f"\n=== {dj['name']} ===")
            evolution(conn, dj["slug"])
        elif args.repeats:
            print(f"\n=== {dj['name']} ===")
            repeats(conn, dj["slug"])
        elif args.anatomy:
            print(f"\n=== {dj['name']} ===")
            anatomy(conn, dj["slug"])
        elif args.remixes:
            print(f"\n=== {dj['name']} ===")
            remixes(conn, dj["slug"])
        else:
            summary(conn, dj["slug"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
