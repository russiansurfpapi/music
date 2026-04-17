"""Add the slot report's matches into existing session playlists.

By default, only Focus matches are added (subgenre-specific = clean fits).
Pass --kinds ab,thread,focus,evolution to include more.

Dedupes against playlist's current contents so re-running is safe.
"""

import argparse
import json
import os
import time
from typing import List, Set

from spotipy.exceptions import SpotifyException

from auth import get_spotify
from db import connect


def _current_track_ids(sp, pl_id: str) -> Set[str]:
    ids: Set[str] = set()
    offset = 0
    while True:
        try:
            resp = sp.playlist_items(pl_id, limit=100, offset=offset,
                                      additional_types=("track",))
        except SpotifyException as e:
            if getattr(e, "http_status", None) == 429:
                raise RuntimeError("429 on read")
            raise
        items = resp.get("items", [])
        for it in items:
            t = it.get("track") or it.get("item") or {}
            tid = t.get("id")
            if not tid and t.get("uri", "").startswith("spotify:track:"):
                tid = t["uri"].split(":")[-1]
            if tid:
                ids.add(tid)
        if resp.get("next") is None or len(items) < 100:
            break
        offset += 100
        time.sleep(0.6)
    return ids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True, help="Path to slot report JSON")
    ap.add_argument("--kinds", default="focus",
                    help="Comma-separated session kinds to add (focus,ab,thread,evolution)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}
    with open(args.report) as f:
        report = json.load(f)
    slots = report.get("slots", {})  # {session_id: [track_id, ...]}

    sp = get_spotify()
    with connect() as conn:
        # Filter to sessions matching the requested kinds.
        rows = conn.execute(
            "SELECT id, name, spotify_playlist_id FROM sessions "
            "WHERE spotify_playlist_id IS NOT NULL"
        ).fetchall()
        id_to_meta = {r["id"]: r for r in rows}

        total_added = 0
        planned = []
        for sid, track_ids in slots.items():
            meta = id_to_meta.get(sid)
            if not meta:
                continue
            kind = sid.split(":", 1)[0]
            if kind not in kinds:
                continue
            planned.append((sid, meta, track_ids))

        print(f"Planned: {len(planned)} sessions to update (kinds={kinds})")
        if args.dry_run:
            for sid, meta, tids in planned:
                print(f"  {meta['name']:60s} +{len(tids)} (before dedupe)")
            return

        for i, (sid, meta, tids) in enumerate(planned, 1):
            try:
                existing = _current_track_ids(sp, meta["spotify_playlist_id"])
            except RuntimeError:
                print(f"  ⛔ 429 reading {meta['name']} — bailing")
                break
            new_ids = [t for t in tids if t not in existing]
            if not new_ids:
                print(f"  [{i}/{len(planned)}] {meta['name']}: nothing new")
                continue
            try:
                for j in range(0, len(new_ids), 100):
                    sp.playlist_add_items(meta["spotify_playlist_id"], new_ids[j:j + 100])
                    time.sleep(0.8)
            except SpotifyException as e:
                if getattr(e, "http_status", None) == 429:
                    print(f"  ⛔ 429 adding to {meta['name']} — bailing")
                    break
                raise
            total_added += len(new_ids)
            print(f"  [{i}/{len(planned)}] {meta['name']}: +{len(new_ids)}")
            time.sleep(1.2)

        print(f"\nTotal added: {total_added}")


if __name__ == "__main__":
    main()
