"""Populate dj_set_tracks.artist / .title for rows ingested before
normalisation existed at the scrape boundary.

`raw_artist` / `raw_title` are never modified — they stay as the audit trail
of exactly what the tracklist site published.

Idempotent: only touches rows where the normalised columns are still NULL,
unless --force is given.
"""

import argparse

from artist_normalize import primary_artist, clean_title
from db import connect


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="recompute rows that already have normalised values")
    args = ap.parse_args()

    where = "" if args.force else " WHERE artist IS NULL OR title IS NULL"
    with connect() as conn:
        rows = conn.execute(
            f"SELECT set_id, position, raw_artist, raw_title "
            f"FROM dj_set_tracks{where}").fetchall()
        print(f"normalising {len(rows)} rows")

        changed = 0
        for r in rows:
            a = primary_artist(r["raw_artist"] or "") or r["raw_artist"]
            t = clean_title(r["raw_title"] or "") or r["raw_title"]
            if a != r["raw_artist"] or t != r["raw_title"]:
                changed += 1
            conn.execute(
                "UPDATE dj_set_tracks SET artist=?, title=? "
                "WHERE set_id=? AND position=?", (a, t, r["set_id"], r["position"]))
        conn.commit()

    print(f"done: {len(rows)} rows written, {changed} actually differed from raw")


if __name__ == "__main__":
    main()
