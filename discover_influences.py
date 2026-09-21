"""Discover an artist's musical influences and build a Spotify playlist.

Thorough pipeline:
  1. SERP search (5 query angles) for articles about the artist's influences
  2. SERP search for YouTube interviews where they discuss influences
  3. Scrape web articles (urllib + Bright Data fallback for 403s)
  4. Extract YouTube transcripts (Deepgram Nova-2 or yt-dlp auto-subs)
  5. Feed all content to Claude → extract influence artists + specific tracks/albums
  6. For each influence artist, run a multi-query Spotify sampler
  7. Build a playlist "[Artist] — Influences"

Usage:
    python3 discover_influences.py "Jamie xx"
    python3 discover_influences.py "Jamie xx" --deepgram
    python3 discover_influences.py "Jamie xx" --tracks-per-artist 8 --deepgram
    python3 discover_influences.py "Jamie xx" --dry-run --save-research
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).parent

try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env")
except ImportError:
    pass

SERP_API_KEY = os.getenv("SERP_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEEPGRAM_KEY = os.getenv("DEEPGRAM_KEY", "")


# ---------------------------------------------------------------------------
# Fuzzy dedup helpers
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Lowercase, strip accents, drop punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) not in ("Mn",))  # strip accents
    s = re.sub(r"[^\w\s]", "", s)  # drop punctuation
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _fuzzy_eq(a: str, b: str, threshold: float = 0.85) -> bool:
    """True if normalized strings are similar above threshold."""
    na, nb = _normalize(a), _normalize(b)
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def dedup_influences(influences: list[dict]) -> list[dict]:
    """Remove duplicate influence artists via fuzzy name matching.
    Keeps the first occurrence; merges tracks/reasons from dupes."""
    deduped = []
    for inf in influences:
        merged = False
        for existing in deduped:
            if _fuzzy_eq(inf["name"], existing["name"]):
                # Merge tracks and pick longer reason
                ext = existing.get("tracks", [])
                new = inf.get("tracks", [])
                existing["tracks"] = list(dict.fromkeys(ext + new))  # preserve order, dedup
                if len(inf.get("reason", "")) > len(existing.get("reason", "")):
                    existing["reason"] = inf["reason"]
                merged = True
                break
        if not merged:
            deduped.append(inf)
    return deduped


def _track_key(artist: str, title: str) -> str:
    """Normalized key for fuzzy track dedup."""
    # Strip parenthesized/bracketed suffixes: (Remastered), [Deluxe], (Live at...), etc.
    title = re.sub(r"\s*[\(\[].*(remaster|deluxe|version|edition|remix|bonus|mono|stereo|live|radio|single|extended|original).*[\)\]]",
                   "", title, flags=re.IGNORECASE)
    # Strip dash suffixes: " - Remastered 2011", " - 2017 Remaster", " - Deluxe Edition", etc.
    title = re.sub(r"\s*-\s*(\d{4}\s+)?(remaster(ed)?|deluxe|version|edition|bonus|mono|stereo|live|radio|single|extended|original)(\s+\d{4})?\s*$",
                   "", title, flags=re.IGNORECASE)
    return _normalize(f"{artist} {title}")


# ---------------------------------------------------------------------------
# 1. SERP search — wide net
# ---------------------------------------------------------------------------

def serp_search(query: str, max_results: int = 10) -> list[dict]:
    if not SERP_API_KEY:
        print("ERROR: SERP_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

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
        return data.get("organic_results", [])
    except Exception as e:
        print(f"    SERP error on '{query[:40]}': {e}", file=sys.stderr)
        return []


def search_influence_articles(artist: str) -> list[dict]:
    """Cast a wide net for articles about the artist's influences."""
    queries = [
        f'"{artist}" musical influences inspired by',
        f'"{artist}" interview "influenced by" OR "inspired by" OR "grew up listening"',
        f'"{artist}" favorite records OR albums OR artists',
        f'"{artist}" "desert island discs" OR "record collection" OR "formative"',
        f'"{artist}" production style inspiration sound',
    ]
    results = []
    seen = set()
    for q in queries:
        for r in serp_search(q, max_results=8):
            link = r.get("link", "")
            if link in seen or "youtube.com" in link or "youtu.be" in link:
                continue
            seen.add(link)
            results.append({
                "url": link,
                "title": r.get("title", ""),
                "snippet": r.get("snippet", ""),
            })
    return results[:15]


def search_youtube_interviews(artist: str) -> list[dict]:
    """Search for YouTube interviews where the artist discusses influences."""
    queries = [
        f'"{artist}" interview influences site:youtube.com',
        f'"{artist}" "inspired by" OR "grew up listening" OR "favorite records" site:youtube.com',
        f'"{artist}" interview music taste OR collection site:youtube.com',
    ]
    results = []
    seen_ids = set()
    for q in queries:
        for r in serp_search(q, max_results=6):
            link = r.get("link", "")
            m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", link)
            if not m:
                continue
            vid_id = m.group(1)
            if vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)
            results.append({
                "id": vid_id,
                "url": f"https://www.youtube.com/watch?v={vid_id}",
                "title": r.get("title", ""),
            })
    return results[:8]


# ---------------------------------------------------------------------------
# 2. Scrape web articles (urllib + Bright Data fallback)
# ---------------------------------------------------------------------------

def fetch_article_text(url: str, timeout: int = 15, use_bright_data: bool = False) -> str:
    """Fetch a web page and extract its text content."""
    text = _fetch_urllib(url, timeout)
    if not text and use_bright_data:
        text = _fetch_bright_data(url)
    return text


def _fetch_urllib(url: str, timeout: int = 15) -> str:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            page_html = raw.decode(charset, errors="replace")

        page_html = re.sub(r"<script[^>]*>.*?</script>", "", page_html, flags=re.DOTALL | re.IGNORECASE)
        page_html = re.sub(r"<style[^>]*>.*?</style>", "", page_html, flags=re.DOTALL | re.IGNORECASE)
        page_html = re.sub(r"<nav[^>]*>.*?</nav>", "", page_html, flags=re.DOTALL | re.IGNORECASE)
        page_html = re.sub(r"<footer[^>]*>.*?</footer>", "", page_html, flags=re.DOTALL | re.IGNORECASE)
        page_html = re.sub(r"<header[^>]*>.*?</header>", "", page_html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", page_html)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:10000]
    except Exception as e:
        print(f"    urllib failed for {url}: {e}", file=sys.stderr)
        return ""


def _fetch_bright_data(url: str) -> str:
    """Fetch via Bright Data Web Unlocker API (bypasses bot detection)."""
    bd_key = os.getenv("BRIGHT_DATA_API_KEY", "")
    if not bd_key:
        return ""
    try:
        api_url = "https://api.brightdata.com/request"
        body = json.dumps({"zone": "web_unlocker1", "url": url, "format": "raw"}).encode()
        req = urllib.request.Request(api_url, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bd_key}",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            page_html = resp.read().decode("utf-8", errors="replace")
        page_html = re.sub(r"<script[^>]*>.*?</script>", "", page_html, flags=re.DOTALL | re.IGNORECASE)
        page_html = re.sub(r"<style[^>]*>.*?</style>", "", page_html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", page_html)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:10000]
    except Exception as e:
        print(f"    Bright Data failed for {url}: {e}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# 3. YouTube transcript extraction
# ---------------------------------------------------------------------------

def extract_youtube_transcript(video_id: str, use_deepgram: bool = False) -> str:
    if use_deepgram and DEEPGRAM_KEY:
        return _transcript_deepgram(video_id)
    return _transcript_vtt(video_id)


def _transcript_vtt(video_id: str) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = os.path.join(tmpdir, "%(id)s")
        url = f"https://www.youtube.com/watch?v={video_id}"
        cmd = [
            "yt-dlp", "--write-auto-subs", "--sub-lang", "en",
            "--skip-download", "--sub-format", "vtt",
            "-o", out_template, url,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        except Exception as e:
            print(f"    yt-dlp error for {video_id}: {e}", file=sys.stderr)
            return ""

        vtt_files = list(Path(tmpdir).glob("*.vtt"))
        if not vtt_files:
            return ""

        vtt_text = vtt_files[0].read_text(errors="replace")
        lines = []
        for line in vtt_text.splitlines():
            if "-->" in line or line.strip() == "" or line.startswith("WEBVTT"):
                continue
            if re.match(r"^\d{2}:\d{2}", line.strip()):
                continue
            if line.strip().startswith("Kind:") or line.strip().startswith("Language:"):
                continue
            cleaned = re.sub(r"<[^>]+>", "", line).strip()
            if cleaned and cleaned not in lines[-1:]:
                lines.append(cleaned)
        return " ".join(lines)[:8000]


def _transcript_deepgram(video_id: str) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, f"{video_id}.mp3")
        url = f"https://www.youtube.com/watch?v={video_id}"
        cmd = [
            "yt-dlp", "-x", "--audio-format", "mp3",
            "--postprocessor-args", "-ac 1 -ar 16000",
            "-o", audio_path, url,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120, check=False)
            if not os.path.exists(audio_path):
                print(f"    yt-dlp download failed for {video_id}", file=sys.stderr)
                return _transcript_vtt(video_id)
        except Exception as e:
            print(f"    yt-dlp error for {video_id}: {e}", file=sys.stderr)
            return _transcript_vtt(video_id)

        with open(audio_path, "rb") as f:
            audio_data = f.read()

        req = urllib.request.Request(
            "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true&language=en",
            data=audio_data,
            headers={
                "Authorization": f"Token {DEEPGRAM_KEY}",
                "Content-Type": "audio/mp3",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode())
            transcript = data.get("results", {}).get("channels", [{}])[0] \
                             .get("alternatives", [{}])[0].get("transcript", "")
            return transcript[:8000]
        except Exception as e:
            print(f"    Deepgram error for {video_id}: {e} — falling back to VTT",
                  file=sys.stderr)
            return _transcript_vtt(video_id)


# ---------------------------------------------------------------------------
# 4. LLM influence extraction — thorough prompt
# ---------------------------------------------------------------------------

def extract_influences_with_llm(artist: str, articles: list[dict],
                                 transcripts: list[dict]) -> list[dict]:
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    content_parts = []
    for i, art in enumerate(articles, 1):
        if art.get("text"):
            content_parts.append(
                f"--- Article {i}: {art['title']} ({art['url']}) ---\n"
                f"{art['text'][:5000]}"
            )

    for i, tr in enumerate(transcripts, 1):
        if tr.get("text"):
            content_parts.append(
                f"--- YouTube transcript {i}: {tr['title']} ---\n"
                f"{tr['text'][:5000]}"
            )

    if not content_parts:
        print("  no content to analyze — trying with snippets only")
        for art in articles:
            if art.get("snippet"):
                content_parts.append(f"Snippet: {art['snippet']}")
        if not content_parts:
            return []

    combined = "\n\n".join(content_parts)

    prompt = f"""I'm building a comprehensive "influences" playlist for "{artist}". Below are articles and interview transcripts.

Your job: extract EVERY artist, musician, band, producer, or composer that {artist} has cited as an influence, inspiration, formative listening, or important to their artistic development. Be thorough — even brief mentions count.

For each influence, provide:
- "name": the artist/band name exactly as it appears on Spotify
- "reason": WHY they're an influence — quote or paraphrase the source (1 sentence)
- "tracks": if ANY specific songs or albums by this influence are mentioned in the content, list them (up to 3). Otherwise empty array.

Rules:
- Include ALL mentioned influences — cast a wide net
- Include artists whose specific SONGS or ALBUMS are mentioned as important to {artist}
- Include artists from playlist/compilation contexts if {artist} curated or cited them
- Do NOT include {artist} themselves
- Do NOT include generic categories like "dubstep producers" — only named artists
- Aim for 15-35 influence artists
- "name" must be a real, specific artist name searchable on Spotify

Return ONLY a JSON array:
[{{"name": "Artist Name", "reason": "Why they influenced {artist}", "tracks": ["Song 1", "Album 2"]}}]

Content:
{combined[:20000]}"""

    api_key = ANTHROPIC_API_KEY.strip().strip('"')
    request_body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=request_body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            response = json.loads(resp.read().decode())
        text = response["content"][0]["text"]

        # Find the JSON array — try progressively shorter matches
        # to handle extra text after the closing bracket
        start = text.find("[")
        if start == -1:
            print("  LLM returned no JSON array", file=sys.stderr)
            return []

        # Walk forward from '[' to find the matching ']'
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        influences = json.loads(text[start:end])
        cleaned = [inf for inf in influences
                   if inf.get("name") and len(inf["name"]) < 60
                   and not any(w in inf["name"].lower()
                               for w in ["producers", "various", "unknown", "genre"])]
        return dedup_influences(cleaned)
    except Exception as e:
        print(f"  LLM error: {e}", file=sys.stderr)

    return []


# ---------------------------------------------------------------------------
# 5. Spotify sampler — multiple search angles per influence
# ---------------------------------------------------------------------------

def build_influence_playlist(artist: str, influences: list[dict],
                              tracks_per: int = 5, playlist_name: str = None,
                              dry_run: bool = False):
    """Multi-query Spotify sampler for each influence artist."""
    if dry_run:
        total_named_tracks = sum(len(inf.get("tracks", [])) for inf in influences)
        print(f"\n{'#':<3} {'Artist':<25} {'Named tracks':<15} {'Reason'}")
        print("-" * 90)
        for i, inf in enumerate(influences, 1):
            named = ", ".join(inf.get("tracks", [])[:2]) or "—"
            print(f"{i:<3} {inf['name']:<25} {named:<15} {inf.get('reason', '')[:40]}")
        est = len(influences) * tracks_per + total_named_tracks
        print(f"\n--dry-run: would create playlist with ~{est} tracks")
        return

    from auth import get_spotify
    from tracklist_scraper import (
        _get_or_create_playlist, _add_tracks_to_playlist,
    )

    sp = get_spotify()
    name = playlist_name or f"{artist} — Influences"
    playlist_id, playlist_url = _get_or_create_playlist(sp, name)
    print(f"\nPlaylist: {name}")
    print(f"  {playlist_url}\n")

    all_track_ids = []
    seen_track_ids = set()
    seen_track_keys = set()  # fuzzy dedup by normalized (artist, title)

    def _add_unique(items_raw):
        """Add tracks, deduping by ID and fuzzy (artist, title) match."""
        for t in items_raw:
            tid = t.get("id") if isinstance(t, dict) else t
            if not tid or tid in seen_track_ids:
                continue
            if isinstance(t, dict):
                artists = ", ".join(a["name"] for a in t.get("artists", []))
                title = t.get("name", "")
                key = _track_key(artists, title)
                # Check fuzzy match against all seen keys
                dupe = False
                for sk in seen_track_keys:
                    if _fuzzy_eq(key, sk, threshold=0.82):
                        dupe = True
                        break
                if dupe:
                    continue
                seen_track_keys.add(key)
            seen_track_ids.add(tid)
            all_track_ids.append(tid)

    for i, inf in enumerate(influences, 1):
        inf_name = inf["name"]
        named_tracks = inf.get("tracks", [])
        print(f"  [{i}/{len(influences)}] {inf_name}", end="", flush=True)

        try:
            # Search 1: general top tracks
            r1 = sp.search(q=f"artist:{inf_name}", type="track", limit=tracks_per)
            _add_unique(r1.get("tracks", {}).get("items", []))
            time.sleep(2)

            # Search 2: named tracks from interviews
            for track_name in named_tracks[:3]:
                r2 = sp.search(q=f"artist:{inf_name} track:{track_name}", type="track", limit=1)
                _add_unique(r2.get("tracks", {}).get("items", []))
                time.sleep(2)

            # Search 3: recent/popular (different angle)
            if tracks_per >= 5:
                r3 = sp.search(q=f"{inf_name}", type="track", limit=3)
                _add_unique(r3.get("tracks", {}).get("items", []))
                time.sleep(2)

            print(f" → {len(all_track_ids)} unique tracks", flush=True)

        except Exception as e:
            if "429" in str(e):
                print(f"\n  RATE LIMITED — saving {len(all_track_ids)} tracks collected")
                break
            print(f" → error: {e}")
            time.sleep(2)

    if all_track_ids:
        for start in range(0, len(all_track_ids), 100):
            batch = all_track_ids[start:start + 100]
            _add_tracks_to_playlist(sp, playlist_id, batch)

        print(f"\n  Added {len(all_track_ids)} unique tracks from {len(influences)} artists")
        print(f"  {playlist_url}")
    else:
        print("\n  No tracks found")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Discover an artist's influences and build a Spotify sampler playlist")
    ap.add_argument("artist", help="Artist name to research")
    ap.add_argument("--tracks-per-artist", type=int, default=5,
                    help="Base tracks per influence artist (default: 5)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show influences without creating playlist")
    ap.add_argument("--playlist", help="Custom playlist name")
    ap.add_argument("--no-youtube", action="store_true",
                    help="Skip YouTube transcript extraction")
    ap.add_argument("--deepgram", action="store_true",
                    help="Use Deepgram Nova-2 for YouTube transcription")
    ap.add_argument("--bright-data", action="store_true",
                    help="Use Bright Data as fallback for blocked articles")
    ap.add_argument("--save-research", action="store_true",
                    help="Save raw research content to a JSON file")
    args = ap.parse_args()

    artist = args.artist

    # --- Step 1: SERP search (wide) ---
    print(f"Researching {artist}'s musical influences...\n")

    print("  Web articles (5 query angles)...")
    articles = search_influence_articles(artist)
    print(f"    → {len(articles)} unique articles")

    yt_results = []
    if not args.no_youtube:
        print("  YouTube interviews (3 query angles)...")
        yt_results = search_youtube_interviews(artist)
        print(f"    → {len(yt_results)} unique videos")

    # --- Step 2: Scrape articles ---
    print(f"\n  Scraping {len(articles)} articles...")
    fetched = 0
    for art in articles:
        art["text"] = fetch_article_text(art["url"], use_bright_data=args.bright_data)
        if art["text"]:
            fetched += 1
            print(f"    {art['title'][:55]:<58} {len(art['text']):>5} chars")
        else:
            print(f"    {art['title'][:55]:<58} FAILED")
    print(f"    → {fetched}/{len(articles)} scraped")

    # --- Step 3: YouTube transcripts ---
    transcripts = []
    if yt_results:
        backend = "Deepgram Nova-2" if args.deepgram else "yt-dlp auto-subs"
        max_yt = 5
        print(f"\n  Transcribing {min(len(yt_results), max_yt)} videos ({backend})...")
        for yt in yt_results[:max_yt]:
            text = extract_youtube_transcript(yt["id"], use_deepgram=args.deepgram)
            transcripts.append({"title": yt["title"], "text": text})
            if text:
                print(f"    {yt['title'][:55]:<58} {len(text):>5} chars")
            else:
                print(f"    {yt['title'][:55]:<58} NO CAPTIONS")

    # --- Step 4: LLM extraction (Sonnet for thoroughness) ---
    total_content = sum(len(a.get("text", "")) for a in articles) + \
                    sum(len(t.get("text", "")) for t in transcripts)
    print(f"\n  Extracting influences via Claude Sonnet ({total_content:,} chars of content)...")
    influences = extract_influences_with_llm(artist, articles, transcripts)
    print(f"    → {len(influences)} influence artists identified")

    if not influences:
        print("\nNo influences found. Try different search terms or check SERP results.")
        return

    # --- Optional: save research ---
    if args.save_research:
        research_file = HERE / f"{artist.lower().replace(' ', '_')}_influences.json"
        with open(research_file, "w") as f:
            json.dump({
                "artist": artist,
                "articles": [{k: v for k, v in a.items() if k != "text"} for a in articles],
                "youtube": [{"title": t["title"], "chars": len(t.get("text", ""))}
                            for t in transcripts],
                "influences": influences,
            }, f, indent=2)
        print(f"  Research saved to {research_file}")

    # --- Step 5: Display + Playlist ---
    named_count = sum(1 for inf in influences if inf.get("tracks"))
    print(f"\n{'#':<3} {'Influence':<25} {'Named tracks':<30} {'Why'}")
    print("-" * 95)
    for i, inf in enumerate(influences, 1):
        named = ", ".join(inf.get("tracks", [])[:2]) or "—"
        if len(named) > 28:
            named = named[:25] + "..."
        print(f"{i:<3} {inf['name']:<25} {named:<30} {inf.get('reason', '')[:35]}")

    print(f"\n  {named_count}/{len(influences)} influences have specifically named tracks")

    build_influence_playlist(
        artist, influences,
        tracks_per=args.tracks_per_artist,
        playlist_name=args.playlist,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
