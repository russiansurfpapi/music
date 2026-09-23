"""Find an artist's DJ sets on YouTube, deduplicated by event, classified by an LLM.

Supersedes `discover_sets.py`, which asked Claude a yes/no question per search
result. That could not do the two things that actually matter, and it showed:
asked for 10 Nicolas Jaar sets it returned 4, while the same catalogue searched
this way yielded 18 distinct events.

What it fixes:

1. **Re-uploads collapse.** One event is uploaded many times. Jaar's 2012
   Essential Mix appeared 9 times in one sweep, Sonar 2012 five times,
   Departamento three. A yes/no filter says yes to all of them and you
   fingerprint the same 2 hours nine times. The LLM assigns an `event_key`
   (artist + venue/show + date) and only the longest upload of each survives.

2. **Live performance is separated from DJ sets.** Fingerprinting recognises
   released recordings, so an artist performing their own material returns
   almost nothing. Measured on this library: Jaar's DJ sets and radio mixes
   hit 63% and 71%, while Sonar 2012 returned 1 track from 67 chunks (3%) and
   Stockholm 3 from 87 (6%). Same artist, same pipeline, 20x difference. The
   caller needs to see that split before spending an hour.

Search is `yt-dlp ytsearch`, not SerpAPI: free, and it returns real durations.
SerpAPI's rich snippets left the duration blank on most rows, so the length
filter could not run at all.

Usage:
    python3 discover_dj_sets.py "Nicolas Jaar"
    python3 discover_dj_sets.py "Four Tet" --min-minutes 40 --kinds dj_set
    python3 discover_dj_sets.py "Peggy Gou" --run --dj peggy-gou
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

from db import connect

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"

# Angles that surface different corners of a catalogue. A single query returns
# the same famous upload over and over; the radio/podcast and alias angles are
# what turn up the mixes worth fingerprinting.
QUERY_TEMPLATES = [
    # generic
    "{a} dj set", "{a} live set", "{a} mix", "{a} b2b", "{a} full set festival",
    # the series and residencies that publish long mixes
    "{a} boiler room", "{a} essential mix", "{a} resident advisor podcast",
    "{a} nts radio", "{a} radio show", "{a} podcast",
    # venues and festivals. These matter more than they look: the generic
    # queries returned the same famous uploads over and over, and it was the
    # venue angle that surfaced Bar 25, 10 Days Off, Bozar and the artist's own
    # radio project — four events no generic query found.
    "{a} dekmantel", "{a} primavera", "{a} sonar", "{a} berghain",
    "{a} fabric london", "{a} awakenings", "{a} concert full",
    # recency: search ranking buries anything new behind the canonical uploads
    "{a} live 2024", "{a} live 2025", "{a} live 2026",
]


def search_youtube(artist, per_query=12, min_seconds=1800, templates=None):
    """Candidates from yt-dlp's own search. Free, and durations are real."""
    seen = {}
    for tmpl in (templates or QUERY_TEMPLATES):
        q = tmpl.format(a=artist)
        try:
            out = subprocess.check_output(
                ["yt-dlp", "--flat-playlist", "--no-warnings", "-J",
                 f"ytsearch{per_query}:{q}"],
                text=True, stderr=subprocess.DEVNULL, timeout=120)
            data = json.loads(out)
        except Exception as e:
            print(f"  search failed for {q!r}: {e}", file=sys.stderr)
            continue
        for e in data.get("entries") or []:
            dur = e.get("duration") or 0
            if dur < min_seconds or e["id"] in seen:
                continue
            seen[e["id"]] = {
                "id": e["id"],
                "title": (e.get("title") or "").strip(),
                "minutes": int(dur // 60),
                "channel": (e.get("channel") or e.get("uploader") or "").strip(),
            }
        print(f"  {q!r}: {len(seen)} unique so far")
    return list(seen.values())


def already_have(ids):
    """Video IDs already fingerprinted — dj_sets is keyed by YouTube video ID."""
    if not ids:
        return set()
    with connect() as conn:
        rows = conn.execute(
            "SELECT set_id FROM dj_sets WHERE set_id IN (%s)" % ",".join("?" * len(ids)),
            tuple(ids)).fetchall()
    return {r[0] for r in rows}


def owned_titles(dj_slug=None):
    """Titles of sets already fingerprinted, for the same-event check.

    Excluding by video ID alone is not enough: a set you already have gets
    re-uploaded under a different ID and comes back looking new. The first run
    of this tool returned the 2012 Essential Mix, RA.500, RA.211 and Boiler
    Room NYC as fresh finds when all four were already in the library.
    """
    with connect() as conn:
        if dj_slug:
            rows = conn.execute(
                "SELECT title FROM dj_sets WHERE dj_slug = ? AND title IS NOT NULL",
                (dj_slug,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT title FROM dj_sets WHERE title IS NOT NULL").fetchall()
    return [r[0] for r in rows]


PROMPT = """You are triaging YouTube search results for DJ sets by "{artist}".

For EACH numbered candidate return one object:
  "n"          the candidate number
  "artist_ok"  true only if {artist} (or a known alias/project of theirs) is the
               performer. False for another artist's set that merely mentions them.
  "kind"       one of:
                 "dj_set"  — they are selecting other people's records: club or
                             festival DJ set, radio mix, podcast mix
                 "live"    — they are performing their own material live
                 "release" — an album, EP, single, or an album upload dressed up
                             as a mix or "visualiser"
                 "other"   — interview, documentary, fan compilation, tutorial
  "event_key"  a short stable slug identifying the EVENT, so that separate
               uploads of the same recording share it. Use
               artist-venue_or_show-year, e.g. "jaar-essential_mix-2012",
               "jaar-sonar-2012". Different nights at the same venue get
               different keys. Part 1 / Part 2 of one recording share a key.
               NORMALISE HARD: strip upload noise, dates, "full set", "(HD)",
               channel names and part numbers first, then key on the event.
               "Sonar Lab, Barcelona FM - 15-06-2012" and
               "Best Set - Sonar 2012 + Tracklist" are ONE event and must get
               the SAME key. When unsure whether two rows are one event,
               prefer giving them the same key.
  "owned"      true if this candidate is the same EVENT as any entry in the
               ALREADY FINGERPRINTED list below, even though the upload
               differs. This is the common case for famous sets.
  "why"        under 10 words

ALREADY FINGERPRINTED (do not return these events as new):
{owned}

Candidates:
{listing}

Reply with ONLY a JSON array, one object per candidate, no prose."""


def classify(artist, candidates, owned):
    if not ANTHROPIC_API_KEY:
        sys.exit("ANTHROPIC_API_KEY not set — cannot classify. Set it in .env")

    listing = "\n".join(
        f"{i+1}. [{c['minutes']}m] {c['title']} (channel: {c['channel']})"
        for i, c in enumerate(candidates))
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 8000,
        "messages": [{"role": "user",
                      "content": PROMPT.format(
                          artist=artist, listing=listing,
                          owned="\n".join(f"- {t}" for t in owned) or "(none)")}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"Content-Type": "application/json",
                 "x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read().decode())
    text = payload["content"][0]["text"]
    usage = payload.get("usage", {})
    print(f"  LLM: {usage.get('input_tokens','?')} in / "
          f"{usage.get('output_tokens','?')} out")

    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        sys.exit(f"could not parse LLM reply:\n{text[:400]}")
    verdicts = json.loads(m.group())

    out = []
    for v in verdicts:
        i = int(v.get("n", 0)) - 1
        if 0 <= i < len(candidates):
            out.append({**candidates[i], **v})
    return out


def dedupe_by_event(rows):
    """One row per event_key — the longest upload wins.

    Longest, because partial and ad-trimmed re-uploads are common and a short
    one silently costs you the rest of the set.
    """
    best = {}
    for r in rows:
        key = r.get("event_key") or r["id"]
        if key not in best or r["minutes"] > best[key]["minutes"]:
            best[key] = r
    return sorted(best.values(), key=lambda r: -r["minutes"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("artist")
    ap.add_argument("--min-minutes", type=int, default=30)
    ap.add_argument("--per-query", type=int, default=12)
    ap.add_argument("--extra-queries", default=None,
                    help="comma-separated extra search phrases; {a} is the artist")
    ap.add_argument("--kinds", default="dj_set",
                    help="comma-separated kinds to keep (default dj_set; "
                         "'dj_set,live' to include live performances)")
    ap.add_argument("--dj", default=None, help="dj slug for --run")
    ap.add_argument("--run", action="store_true",
                    help="fingerprint the surviving sets via identify_youtube_set.py")
    ap.add_argument("--json", default=None, help="write the result list here")
    args = ap.parse_args()

    keep_kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}

    templates = list(QUERY_TEMPLATES)
    if args.extra_queries:
        templates += [q if "{a}" in q else "{a} " + q
                      for q in args.extra_queries.split(",") if q.strip()]
    print(f"Searching YouTube for '{args.artist}' ({len(templates)} angles)...")
    cands = search_youtube(args.artist, args.per_query, args.min_minutes * 60,
                           templates)
    if not cands:
        sys.exit("no candidates over the length filter")

    have = already_have([c["id"] for c in cands])
    cands = [c for c in cands if c["id"] not in have]
    print(f"\n{len(cands)} candidates ({len(have)} already fingerprinted)")

    owned = owned_titles(args.dj)
    print(f"Classifying ({len(owned)} sets already in the library)...")
    rows = classify(args.artist, cands, owned)

    wrong_artist = [r for r in rows if not r.get("artist_ok")]
    rows = [r for r in rows if r.get("artist_ok")]
    dupes = [r for r in rows if r.get("owned")]
    rows = [r for r in rows if not r.get("owned")]
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r.get("kind", "other"), []).append(r)

    deduped = dedupe_by_event([r for r in rows if r.get("kind") in keep_kinds])

    print(f"\n  rejected, not {args.artist}: {len(wrong_artist)}")
    print(f"  rejected, re-upload of a set already fingerprinted: {len(dupes)}")
    for k in ("dj_set", "live", "release", "other"):
        n = len(by_kind.get(k, []))
        mark = " (keeping)" if k in keep_kinds else ""
        if n:
            print(f"  {k:<8} {n}{mark}")
    print(f"\n{len(deduped)} distinct events after collapsing re-uploads:\n")
    print(f"  {'min':>4}  {'kind':<8} {'id':<12} title")
    for r in deduped:
        print(f"  {r['minutes']:>4}  {r.get('kind',''):<8} {r['id']:<12} {r['title'][:58]}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(deduped, f, indent=2)
        print(f"\nwrote {args.json}")

    if args.run:
        if not args.dj:
            sys.exit("--run needs --dj <slug>")
        import time
        for r in deduped:
            print(f"\n▶ {r['title']}")
            subprocess.run(
                ["python3", "-u", "identify_youtube_set.py",
                 f"https://www.youtube.com/watch?v={r['id']}",
                 "--dj", args.dj, "--title", r["title"], "--backend", "shazam"])
            # A run of back-to-back downloads is what trips YouTube's bot check.
            time.sleep(45)


if __name__ == "__main__":
    main()
