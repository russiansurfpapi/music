"""Discover DJ sets on YouTube via SerpAPI + LLM filtering.

Searches YouTube for a DJ's live sets, uses Claude to filter out non-sets
(interviews, music videos, fan compilations), then feeds results into
youtube_to_study.py for fingerprinting.

Usage:
    python3 discover_sets.py "Radio Slave" --count 10
    python3 discover_sets.py "Radio Slave" --count 10 --dry-run
    python3 discover_sets.py "Radio Slave" --count 10 --run
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent

try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env")
except ImportError:
    pass

SERP_API_KEY = os.getenv("SERP_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


def search_youtube_sets(dj_name: str, max_results: int = 20) -> list:
    """Search YouTube via SerpAPI (Google engine with site:youtube.com)."""
    if not SERP_API_KEY:
        print("ERROR: SERP_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    queries = [
        f"{dj_name} DJ set site:youtube.com",
        f"{dj_name} live set boiler room OR club OR festival site:youtube.com",
    ]

    all_results = []
    seen_ids = set()

    for query in queries:
        params = urllib.parse.urlencode({
            "q": query,
            "api_key": SERP_API_KEY,
            "engine": "google",
            "num": max_results,
        })
        url = f"https://serpapi.com/search.json?{params}"

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            print(f"  SERP error on query '{query}': {e}", file=sys.stderr)
            continue

        for r in data.get("organic_results", []):
            link = r.get("link", "")
            # Extract video ID from youtube.com URLs
            import re as _re
            m = _re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", link)
            if not m:
                continue
            vid_id = m.group(1)
            if vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)
            # Try to get duration from rich snippet
            rich = r.get("rich_snippet", {}).get("top", {})
            duration = ""
            for ext in rich.get("extensions", []):
                if ":" in ext and any(c.isdigit() for c in ext):
                    duration = ext
                    break
            all_results.append({
                "id": vid_id,
                "title": r.get("title", ""),
                "url": f"https://www.youtube.com/watch?v={vid_id}",
                "duration": duration,
                "channel": r.get("source", ""),
            })

    return all_results


def filter_with_llm(dj_name: str, results: list, target_count: int) -> list:
    """Use Claude to filter results to actual DJ sets by this artist."""
    if not ANTHROPIC_API_KEY:
        print("WARNING: ANTHROPIC_API_KEY not set — skipping LLM filter, using heuristics",
              file=sys.stderr)
        return _heuristic_filter(dj_name, results, target_count)

    results_text = "\n".join(
        f"{i+1}. [{r['duration']}] {r['title']} (by {r['channel']})"
        for i, r in enumerate(results)
    )

    prompt = f"""I'm looking for YouTube videos of DJ sets performed by "{dj_name}" (live performances, recorded sets, radio shows where they are DJing).

Here are YouTube search results. For each one, tell me YES or NO — is this a real DJ set/mix by {dj_name}?

Reject:
- Music videos or single tracks
- Sets by OTHER DJs (even if {dj_name} is mentioned)
- Interviews, documentaries, podcasts about them
- Fan-made compilations/playlists
- Videos under 20 minutes (too short to be a real set)
- Duplicate uploads of the same set

Accept:
- Live DJ sets at clubs/festivals
- Recorded mixes/radio shows where they are the DJ
- B2B sets where they are one of the DJs

Results:
{results_text}

Reply with ONLY a JSON array of the indices (1-based) that are real {dj_name} DJ sets. Example: [1, 3, 5, 8]"""

    request_body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=request_body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            response = json.loads(resp.read().decode())
        text = response["content"][0]["text"]
        # Extract JSON array from response
        import re
        m = re.search(r"\[[\d\s,]+\]", text)
        if m:
            indices = json.loads(m.group())
            filtered = [results[i - 1] for i in indices if 1 <= i <= len(results)]
            return filtered[:target_count]
    except Exception as e:
        print(f"  LLM filter error: {e} — falling back to heuristics", file=sys.stderr)

    return _heuristic_filter(dj_name, results, target_count)


def _heuristic_filter(dj_name: str, results: list, target_count: int) -> list:
    """Simple keyword-based filter as fallback."""
    dj_lower = dj_name.lower()
    keywords_good = ["set", "live", "boiler room", "festival", "club", "mix", "b2b"]
    keywords_bad = ["interview", "tutorial", "reaction", "review", "podcast", "documentary"]

    scored = []
    for r in results:
        title_lower = r["title"].lower()
        if dj_lower not in title_lower:
            continue
        if any(k in title_lower for k in keywords_bad):
            continue
        # Duration filter: parse "H:MM:SS" or "MM:SS"
        dur = r.get("duration", "")
        minutes = _parse_duration_minutes(dur)
        if minutes < 20:
            continue
        score = sum(1 for k in keywords_good if k in title_lower)
        score += min(minutes / 30, 3)  # longer = better, up to 3 bonus
        scored.append((score, r))

    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:target_count]]


def _parse_duration_minutes(dur: str) -> int:
    """Parse duration like '1:23:45' or '45:30' to minutes."""
    parts = dur.replace(" ", "").split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 2:
            return int(parts[0])
    except ValueError:
        pass
    return 0


def main():
    ap = argparse.ArgumentParser(description="Discover DJ sets on YouTube via SERP + LLM")
    ap.add_argument("dj", help="DJ name to search for")
    ap.add_argument("--count", type=int, default=10, help="Target number of sets")
    ap.add_argument("--dry-run", action="store_true", help="Show results without processing")
    ap.add_argument("--run", action="store_true", help="Immediately run youtube_to_study.py")
    ap.add_argument("--backend", default="shazam", choices=["shazam", "acrcloud", "merge"])
    args = ap.parse_args()

    print(f"Searching YouTube for '{args.dj}' sets...")
    results = search_youtube_sets(args.dj, max_results=20)
    print(f"  found {len(results)} raw results")

    if not results:
        print("No results found.")
        return

    print(f"Filtering with LLM (target: {args.count} sets)...")
    filtered = filter_with_llm(args.dj, results, args.count)
    print(f"  {len(filtered)} sets passed filter\n")

    if not filtered:
        print("No valid sets found after filtering.")
        return

    print(f"{'#':<3} {'Dur':<8} {'Title'}")
    print("-" * 70)
    for i, r in enumerate(filtered, 1):
        print(f"{i:<3} {r['duration']:<8} {r['title'][:60]}")
    print()

    urls = [r["url"] for r in filtered]

    if args.dry_run:
        print("--dry-run: would process these URLs:")
        for url in urls:
            print(f"  {url}")
        print(f"\nRun command:")
        print(f"  python3 youtube_to_study.py {' '.join(urls)} --auto-dj")
        return

    if args.run:
        print(f"Running youtube_to_study.py with {len(urls)} URLs...")
        cmd = [sys.executable, "youtube_to_study.py"] + urls + [
            "--auto-dj", "--backend", args.backend
        ]
        subprocess.run(cmd, cwd=str(HERE))
    else:
        print("To process, run:")
        print(f"  python3 youtube_to_study.py \\\n    " +
              " \\\n    ".join(urls) + " \\\n    --auto-dj")


if __name__ == "__main__":
    main()
