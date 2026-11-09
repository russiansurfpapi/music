"""One artist in, one Spotify playlist out: every DJ set of theirs on YouTube, fingerprinted,
matched on Spotify, merged into "<Artist> — All Sets".

WHAT:  the five-DJ run of 2026-11-09 (.runs/five-djs/) made into one resumable command.
DONE:  `python3 dj_sets_playlist.py "Benji B"` ends by printing the playlist URL, and a rerun
       after a Spotify cooldown continues where it stopped without re-fingerprinting anything.
REPLACES: the per-run scripts in .runs/five-djs/ (run_acr.py, after.py, merge_dj_playlists.py)
       for new artists; they stay as the record of that run.

Stages, each safe to rerun:
  1. find    discover_dj_sets.py -> .runs/<slug>/sets.json (re-uploads collapsed, live sets dropped)
  2. identify identify_youtube_set.py --backend acrcloud, 4 sets at a time; finished sets skipped
  3. match   resolve_dj_tracks.py cache / isrc / search, scoped to this artist's sets with --sets.
             Stops on a Spotify cooldown or a spent daily budget; rerun later to continue.
  4. playlist "<Artist> — All Sets", sets in list order, songs in set order, each song once.

Usage:
  python3 dj_sets_playlist.py "Benji B"                 # everything
  python3 dj_sets_playlist.py "Benji B" --plan          # find sets, print cost, stop
  python3 dj_sets_playlist.py "Benji B" --from match    # skip find + identify
  python3 dj_sets_playlist.py "Benji B" --only playlist # just rebuild the playlist

Cost: ACRCloud is one lookup per --step seconds of audio (default 60s, so ~90 per 90-min set);
the trial account is 3,000 lookups. Spotify searches are paced 5s apart (1.5s earned an
11-hour ban, 2026-11-09) and capped by spotify_budget's learned daily limit.
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

MUSIC = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(MUSIC, "library.db")
STAGES = ["find", "identify", "match", "playlist"]
REAL_ID = re.compile(r"^[A-Za-z0-9]{22}$")
NOT_A_TRACK = ("id", "i.d.", "unknown", "?")


def log(*a):
    print(time.strftime("[%H:%M]"), *a, flush=True)


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def db():
    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row
    return c


# A set counts as finished once this share of its chunks got an answer. ACRCloud refuses a
# few lookups on most sets (Saturday's 48 sets: 47 at >= 90%, lowest 83%), and a rerun
# re-fingerprints the WHOLE set, so demanding 100% would pay ~90 lookups to recover ~5.
# A set the account died on sits far below this.
FINISHED_SHARE = 0.8


def finished_sets():
    with db() as c:
        return {r[0] for r in c.execute(
            "select set_id from dj_sets where chunks_total>0 "
            "and chunks_done >= ? * chunks_total", (FINISHED_SHARE,))}


# ── 1. find ─────────────────────────────────────────────────────────────────

def stage_find(artist, slug, sets_path, args):
    if os.path.exists(sets_path) and not args.rediscover:
        log(f"find: using {sets_path} (pass --rediscover to search YouTube again)")
        return
    cmd = [sys.executable, "-u", "discover_dj_sets.py", artist, "--dj", slug,
           "--min-minutes", str(args.min_minutes), "--json", sets_path]
    log("find: searching YouTube for", artist)
    subprocess.run(cmd, cwd=MUSIC, check=True)


def load_sets(slug, sets_path):
    """This artist's sets: the found list in its order, then any older sets of the
    same DJ already in the library (discovery hides sets fingerprinted before)."""
    rows = json.load(open(sets_path)) if os.path.exists(sets_path) else []
    seen = {r["id"] for r in rows}
    with db() as c:
        for r in c.execute("select set_id, title, chunks_total from dj_sets where dj_slug=? "
                           "order by set_date", (slug,)):
            if r["set_id"] not in seen:
                seen.add(r["set_id"])
                rows.append({"id": r["set_id"], "title": r["title"] or r["set_id"],
                             "minutes": r["chunks_total"] or 0, "from_library": True})
    return rows


def print_plan(rows, step):
    done = finished_sets()
    todo = [r for r in rows if r["id"] not in done]
    lookups = sum(int(r.get("minutes") or 0) * 60 // step for r in todo)
    log(f"{len(rows)} sets ({len(rows) - len(todo)} already identified); "
        f"{len(todo)} to fingerprint, about {lookups} ACRCloud lookups")
    for r in rows:
        mark = "done" if r["id"] in done else "todo"
        print(f"   {mark}  {int(r.get('minutes') or 0):>4} min  {r['id']}  {r['title'][:70]}")
    return lookups


# ── 2. identify ─────────────────────────────────────────────────────────────

def stage_identify(slug, rows, run_dir, args):
    done = finished_sets()
    queue = [r for r in rows if r["id"] not in done]
    if not queue:
        log("identify: every set already fingerprinted")
        return
    os.makedirs(os.path.join(run_dir, "sets"), exist_ok=True)
    lock, stop = threading.Lock(), threading.Event()
    last_start, pause_until, failed = [0.0], [0.0], []

    def work(r):
        if stop.is_set():
            return
        with lock:                                  # stagger downloads; YouTube pushes back on bursts
            time.sleep(max(0, pause_until[0] - time.time()))
            time.sleep(max(0, 10 - (time.time() - last_start[0])))
            last_start[0] = time.time()
        path = os.path.join(run_dir, "sets", f"{r['id']}.log")
        with open(path, "w") as out:
            p = subprocess.run([sys.executable, "-u", "identify_youtube_set.py",
                                f"https://www.youtube.com/watch?v={r['id']}", "--dj", slug,
                                "--title", r["title"], "--backend", "acrcloud", "--step", str(args.step)],
                               cwd=MUSIC, text=True, stdout=out, stderr=subprocess.STDOUT)
        lines = open(path).read().splitlines()
        streak = worst = 0
        for l in lines:
            if "error: acr" in l:
                streak += 1
                worst = max(worst, streak)
            elif l.lstrip().startswith("["):
                streak = 0
        with lock:
            summary = [l.strip() for l in lines if "matched" in l or "wrote" in l][-1:]
            log(f"identify: {r['title'][:60]} -> exit {p.returncode} {' '.join(summary)}")
            if worst >= 8:                          # quota or auth gone: stop burning downloads
                log("identify: ACRCloud is refusing lookups — stopping. Log:", path)
                stop.set()
            if p.returncode != 0:
                failed.append(r)
                text = "\n".join(lines)
                if "confirm you" in text or "403" in text or "429" in text:
                    log("identify: YouTube pushing back — pausing downloads 5 min")
                    pause_until[0] = time.time() + 300

    # Lookups in flight = workers x ACR_THREADS. The trial plan refuses with 3015 ("qps limit
    # exceeded") above a few per second: 4 sets x the default 8 threads was refused on sight
    # (2026-11-09); Saturday's 4 x 2 ran clean. Keep the product near 8.
    os.environ.setdefault("ACR_THREADS", str(args.acr_threads))
    log(f"identify: {len(queue)} sets, {args.workers} at a time, "
        f"{os.environ['ACR_THREADS']} lookups each")
    for attempt in range(3):
        with ThreadPoolExecutor(args.workers) as ex:
            list(ex.map(work, queue))
        if not failed or stop.is_set():
            break
        queue, failed[:] = list(failed), []
        log(f"identify: retrying {len(queue)} failed sets in 3 min")
        time.sleep(180)
    if stop.is_set():
        raise SystemExit("ACRCloud stopped answering; fix the account, then rerun with --from identify")


# ── 3. match ────────────────────────────────────────────────────────────────

def spotify_stop_reason():
    sys.path.insert(0, MUSIC)
    import spotify_budget
    used, limit, cooldown = spotify_budget.status()
    if cooldown and cooldown > time.strftime("%Y-%m-%dT%H:%M:%S"):
        return f"Spotify cooldown until {cooldown}"
    if used >= limit:
        return f"today's Spotify budget is spent ({used}/{limit})"
    return None


def unmatched(set_ids):
    marks = ",".join("?" * len(set_ids))
    with db() as c:
        return c.execute(
            f"select count(distinct raw_artist || '|' || raw_title) from dj_set_tracks "
            f"where spotify_id is null and set_id in ({marks}) "
            f"and lower(coalesce(raw_artist,'')) not in {NOT_A_TRACK}", set_ids).fetchone()[0]


def stage_match(rows, run_dir):
    ids = [r["id"] for r in rows]
    scope = os.path.join(run_dir, "scope.json")
    json.dump([{"id": i} for i in ids], open(scope, "w"))
    resolver = [sys.executable, "-u", "resolve_dj_tracks.py"]
    subprocess.run(resolver + ["cache"], cwd=MUSIC)          # 0 Spotify requests
    for mode in ("isrc", "search"):
        reason = spotify_stop_reason()
        if reason:
            raise SystemExit(f"match: stopped before {mode} — {reason}. Rerun with --from match later.")
        before = unmatched(ids)
        log(f"match: {mode}, {before} songs not on Spotify yet")
        subprocess.run(resolver + [mode, "--sets", scope], cwd=MUSIC)
        log(f"match: {mode} done, {unmatched(ids)} left")
    reason = spotify_stop_reason()
    if reason and unmatched(ids):
        log(f"match: {reason}; {unmatched(ids)} songs still unchecked. Rerun with --from match later.")


# ── 4. playlist ─────────────────────────────────────────────────────────────

def stage_playlist(artist, rows):
    reason = spotify_stop_reason()
    if reason:
        raise SystemExit(f"playlist: {reason}. Rerun with --only playlist later.")
    sys.path.insert(0, MUSIC)
    os.chdir(MUSIC)
    from tracklist_scraper import _get_or_create_playlist, _add_tracks_to_playlist
    from auth import get_spotify
    tracks, seen, used_sets = [], set(), 0
    with db() as c:
        for r in rows:
            got = [x for (x,) in c.execute(
                "select spotify_id from dj_set_tracks where set_id=? order by position", (r["id"],))
                if x and REAL_ID.match(x)]
            used_sets += bool(got)
            for x in got:
                if x not in seen:
                    seen.add(x)
                    tracks.append(x)
    name = f"{artist} — All Sets"
    if not tracks:
        raise SystemExit(f"playlist: no songs matched on Spotify yet for {artist}")
    sp = get_spotify()
    pid, url = _get_or_create_playlist(sp, name)
    added = _add_tracks_to_playlist(sp, pid, tracks)
    log(f"playlist: {name} — {len(tracks)} songs from {used_sets} of {len(rows)} sets "
        f"({added} new this run)\n   {url}")


def report(rows):
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    with db() as c:
        found = c.execute(f"select count(distinct raw_artist||'|'||raw_title) from dj_set_tracks "
                          f"where set_id in ({marks}) and lower(coalesce(raw_artist,'')) not in {NOT_A_TRACK}",
                          ids).fetchone()[0]
        matched = c.execute(f"select count(distinct spotify_id) from dj_set_tracks "
                            f"where set_id in ({marks}) and spotify_id is not null", ids).fetchone()[0]
    log(f"summary: {len(rows)} sets, {found} songs identified, {matched} on Spotify")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("artist")
    ap.add_argument("--slug", help="DJ slug in library.db (default: from the name)")
    ap.add_argument("--plan", action="store_true", help="find the sets, print the cost, stop")
    ap.add_argument("--from", dest="start", choices=STAGES, default="find")
    ap.add_argument("--only", choices=STAGES)
    ap.add_argument("--rediscover", action="store_true", help="search YouTube again")
    ap.add_argument("--min-minutes", type=int, default=30)
    ap.add_argument("--step", type=int, default=60, help="seconds between fingerprint lookups")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--acr-threads", type=int, default=2, help="ACRCloud lookups in flight per set")
    ap.add_argument("--yes", action="store_true", help="don't ask before spending ACRCloud lookups")
    args = ap.parse_args()

    slug = args.slug or slugify(args.artist)
    run_dir = os.path.join(MUSIC, ".runs", slug)
    os.makedirs(run_dir, exist_ok=True)
    sets_path = os.path.join(run_dir, "sets.json")
    stages = [args.only] if args.only else STAGES[STAGES.index(args.start):]

    if "find" in stages:
        stage_find(args.artist, slug, sets_path, args)
    rows = load_sets(slug, sets_path)
    if not rows:
        raise SystemExit(f"no sets for {args.artist}; try --min-minutes 20 or --rediscover")
    lookups = print_plan(rows, args.step)
    if args.plan:
        return
    if "identify" in stages and lookups and not args.yes and sys.stdin.isatty():
        if input(f"Spend about {lookups} ACRCloud lookups? [y/N] ").strip().lower() != "y":
            raise SystemExit("stopped before fingerprinting")
    if "identify" in stages:
        stage_identify(slug, rows, run_dir, args)
    if "match" in stages:
        stage_match(rows, run_dir)
    if "playlist" in stages:
        stage_playlist(args.artist, rows)
    report(rows)


if __name__ == "__main__":
    main()
