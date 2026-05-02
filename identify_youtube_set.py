"""Identify tracks in a YouTube DJ set via audio fingerprinting.

Usage:
    python3 identify_youtube_set.py <youtube-url> --dj <slug> [--backend shazam|acrcloud]
                                    [--step 30] [--chunk 20] [--title "..."] [--date YYYY-MM-DD]

Pipeline:
  1. yt-dlp → wav (mono, 16kHz)
  2. ffmpeg slice into chunks
  3. For each chunk: query backend → (artist, title, confidence)
  4. Dedupe consecutive matches (one track spans many chunks)
  5. Upsert dj_sets + dj_set_tracks rows keyed by YouTube video ID

Backends:
  shazam    — shazamio (unofficial, free)
  acrcloud  — needs ACR_HOST / ACR_KEY / ACR_SECRET env vars
"""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "library.db"

try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env")
except ImportError:
    pass


# ---------- download + chunk ----------

def extract_video_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", url)
    if not m:
        raise ValueError(f"couldn't extract video id from {url}")
    return m.group(1)


def ydl_download(url: str, out_path: Path) -> dict:
    """Download audio as wav, return yt-dlp metadata dict."""
    # Get metadata first (title, duration)
    meta_raw = subprocess.check_output(
        ["yt-dlp", "-j", "--no-warnings", url], text=True
    )
    meta = json.loads(meta_raw)
    # Download as wav
    subprocess.run(
        [
            "yt-dlp", "-x", "--audio-format", "wav",
            "--postprocessor-args", "-ac 1 -ar 16000",
            "-o", str(out_path.with_suffix(".%(ext)s")),
            "--no-warnings", url,
        ],
        check=True,
    )
    return meta


def chunk_wav(src: Path, out_dir: Path, chunk_sec: int, step_sec: int) -> list[tuple[int, Path]]:
    """Slice src into fixed-length chunks starting every step_sec.
    Returns list of (start_sec, chunk_path)."""
    out_dir.mkdir(exist_ok=True)
    # Get duration
    dur = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(src)], text=True
    ).strip())
    chunks = []
    t = 0
    i = 0
    while t < dur:
        out = out_dir / f"chunk_{i:04d}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t),
             "-t", str(chunk_sec), "-i", str(src),
             "-ac", "1", "-ar", "16000", str(out)],
            check=True,
        )
        chunks.append((t, out))
        t += step_sec
        i += 1
    return chunks


# ---------- shazam backend ----------

def _extract_spotify_id(track: dict) -> str:
    """Extract Spotify track ID from Shazam/ACR response metadata."""
    # ACRCloud: external_metadata.spotify.track.id (direct ID)
    ext = track.get("external_metadata", {})
    sp = ext.get("spotify", {})
    if sp.get("track", {}).get("id"):
        return sp["track"]["id"]
    # Shazam doesn't return spotify:track:ID directly — it gives search deeplinks.
    # But it does return ISRC, which we can use for precise Spotify lookup later.
    return None


def _extract_isrc(track: dict) -> str:
    """Extract ISRC from Shazam response (unique recording identifier)."""
    return track.get("isrc") or None


async def shazam_identify(chunks: list[tuple[int, Path]]) -> list[dict]:
    from shazamio import Shazam
    shazam = Shazam()
    results = []
    for i, (t, path) in enumerate(chunks):
        try:
            r = await shazam.recognize(str(path))
        except Exception as e:
            print(f"  [{t//60:02d}:{t%60:02d}] error: {e}", file=sys.stderr)
            results.append({"t": t, "track": None})
            await asyncio.sleep(1.5)
            continue
        track = r.get("track")
        if track:
            title = track.get("title", "")
            artist = track.get("subtitle", "")
            spotify_id = _extract_spotify_id(track)
            isrc = _extract_isrc(track)
            tag = f" [spotify:{spotify_id[:8]}]" if spotify_id else (f" [isrc:{isrc}]" if isrc else "")
            print(f"  [{t//60:02d}:{t%60:02d}] {artist} — {title}{tag}")
            results.append({"t": t, "artist": artist, "title": title,
                            "confidence": 1.0, "spotify_id": spotify_id,
                            "isrc": isrc, "track": track})
        else:
            print(f"  [{t//60:02d}:{t%60:02d}] (no match)")
            results.append({"t": t, "track": None})
        await asyncio.sleep(0.8)  # be polite to unofficial endpoint
    return results


# ---------- acrcloud backend ----------

def acr_identify(chunks: list[tuple[int, Path]]) -> list[dict]:
    import requests
    host = os.environ.get("ACR_HOST")
    key = os.environ.get("ACR_KEY")
    secret = os.environ.get("ACR_SECRET")
    if not (host and key and secret):
        print("ACRCloud: set ACR_HOST, ACR_KEY, ACR_SECRET env vars", file=sys.stderr)
        sys.exit(1)
    url = f"https://{host}/v1/identify"
    results = []
    for i, (t, path) in enumerate(chunks):
        with open(path, "rb") as f:
            sample = f.read()
        timestamp = str(int(time.time()))
        string_to_sign = f"POST\n/v1/identify\n{key}\naudio\n1\n{timestamp}"
        sign = base64.b64encode(
            hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha1).digest()
        ).decode()
        data = {
            "access_key": key,
            "sample_bytes": str(len(sample)),
            "timestamp": timestamp,
            "signature": sign,
            "data_type": "audio",
            "signature_version": "1",
        }
        files = {"sample": sample}
        try:
            r = requests.post(url, data=data, files=files, timeout=30)
            resp = r.json()
        except Exception as e:
            print(f"  [{t//60:02d}:{t%60:02d}] error: {e}", file=sys.stderr)
            results.append({"t": t, "track": None})
            continue
        status = resp.get("status", {}).get("code", -1)
        if status == 0 and resp.get("metadata", {}).get("music"):
            m = resp["metadata"]["music"][0]
            artist = ", ".join(a["name"] for a in m.get("artists", []))
            title = m.get("title", "")
            score = m.get("score", 0) / 100.0
            spotify_id = _extract_spotify_id(m)
            tag = f" [spotify:{spotify_id[:8]}]" if spotify_id else ""
            print(f"  [{t//60:02d}:{t%60:02d}] {artist} — {title} (conf {score:.2f}){tag}")
            results.append({"t": t, "artist": artist, "title": title,
                            "confidence": score, "spotify_id": spotify_id,
                            "track": m})
        else:
            print(f"  [{t//60:02d}:{t%60:02d}] (no match)")
            results.append({"t": t, "track": None})
    return results


# ---------- dedupe ----------

_REMIX_RE = re.compile(
    r"\s*[\(\[](.*?(remix|mix|edit|dub|version|rework|bootleg|vip).*?)[\)\]]",
    re.IGNORECASE,
)


def _base_title(title: str) -> str:
    """Strip remix/edit suffixes for fuzzy dedup comparison."""
    return _REMIX_RE.sub("", title).strip().lower()


def dedupe(results: list[dict]) -> list[dict]:
    """Collapse consecutive chunks with same track into one entry.
    Uses fuzzy title matching to catch remix variants of the same song
    identified across adjacent chunks (e.g., 'Track' vs 'Track (Remix)')."""
    out = []
    prev_key = None
    for r in results:
        if r.get("track") is None:
            prev_key = None
            continue
        artist = r["artist"].lower().strip()
        title = r["title"].lower().strip()
        key = (artist, _base_title(r["title"]))
        # Exact match OR base-title match with same artist = same track
        if key == prev_key:
            continue
        out.append(r)
        prev_key = key
    return out


def merge_results(shazam_r: list[dict], acr_r: list[dict]) -> list[dict]:
    """Per-chunk union of two backends. Keeps higher-confidence match when both
    identified the same chunk; otherwise keeps whichever matched. Tags the
    retained row with _backend so downstream knows which engine won."""
    by_t: dict = {}
    for r in shazam_r:
        if r.get("track"):
            by_t[r["t"]] = {**r, "_backend": "shazam"}
    for r in acr_r:
        if not r.get("track"):
            continue
        existing = by_t.get(r["t"])
        if not existing or r.get("confidence", 0) > existing.get("confidence", 0):
            by_t[r["t"]] = {**r, "_backend": "acrcloud"}
    # Preserve unmatched chunks so caller can compute hit rate
    all_ts = {r["t"] for r in shazam_r} | {r["t"] for r in acr_r}
    for t in all_ts:
        by_t.setdefault(t, {"t": t, "track": None})
    return sorted(by_t.values(), key=lambda x: x["t"])


# ---------- db write ----------

def upsert_set(db: sqlite3.Connection, video_id: str, dj_slug: str,
               title: str, set_date: str, youtube_url: str,
               duration_sec: int, tracks: list[dict]):
    resolved = sum(1 for t in tracks if t.get("spotify_id"))
    db.execute(
        """INSERT OR REPLACE INTO dj_sets
           (set_id, dj_slug, title, set_date, source_file, track_count,
            resolved_count, youtube_url, duration_sec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (video_id, dj_slug, title, set_date, youtube_url,
         len(tracks), resolved, youtube_url, duration_sec),
    )
    db.execute("DELETE FROM dj_set_tracks WHERE set_id = ?", (video_id,))
    for pos, t in enumerate(tracks, 1):
        spotify_id = t.get("spotify_id")
        isrc = t.get("isrc")
        db.execute(
            """INSERT INTO dj_set_tracks
               (set_id, position, raw_artist, raw_title,
                timestamp_sec, confidence, source, spotify_id, isrc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (video_id, pos, t["artist"], t["title"],
             t["t"], t.get("confidence", 1.0), t["_backend"], spotify_id, isrc),
        )
    # Ensure dj exists
    db.execute("INSERT OR IGNORE INTO djs (slug, name) VALUES (?, ?)",
               (dj_slug, dj_slug.replace("-", " ").title()))
    db.commit()


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--dj", required=True, help="DJ slug (matches existing djs.slug)")
    ap.add_argument("--backend", choices=["shazam", "acrcloud", "merge"], default="shazam",
                    help="merge = run both and union per-chunk matches")
    ap.add_argument("--chunk", type=int, default=20, help="chunk length (sec)")
    ap.add_argument("--step", type=int, default=45, help="start step between chunks (sec)")
    ap.add_argument("--title", default=None)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD")
    ap.add_argument("--keep-audio", action="store_true", help="don't delete tmp wav")
    args = ap.parse_args()

    video_id = extract_video_id(args.url)
    print(f"video_id: {video_id}")

    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        wav = tdir / f"{video_id}.wav"
        print("downloading audio...")
        meta = ydl_download(args.url, wav)
        if not wav.exists():
            # yt-dlp may have picked a different extension label
            candidates = list(tdir.glob(f"{video_id}*.wav"))
            if candidates:
                wav = candidates[0]
        duration = int(meta.get("duration", 0))
        title = args.title or meta.get("title", video_id)
        set_date = args.date or (meta.get("upload_date", "")[:4] + "-" +
                                 meta.get("upload_date", "")[4:6] + "-" +
                                 meta.get("upload_date", "")[6:8]) \
            if meta.get("upload_date") else None

        print(f"duration: {duration//60}m {duration%60}s  title: {title}")
        print(f"chunking: {args.chunk}s windows every {args.step}s...")
        chunks = chunk_wav(wav, tdir / "chunks", args.chunk, args.step)
        print(f"{len(chunks)} chunks → querying {args.backend}...")

        if args.backend == "shazam":
            results = asyncio.run(shazam_identify(chunks))
            for r in results:
                if r.get("track"): r["_backend"] = "shazam"
        elif args.backend == "acrcloud":
            results = acr_identify(chunks)
            for r in results:
                if r.get("track"): r["_backend"] = "acrcloud"
        else:  # merge
            print("  → shazam pass")
            sh = asyncio.run(shazam_identify(chunks))
            print("  → acrcloud pass")
            ac = acr_identify(chunks)
            results = merge_results(sh, ac)
            sh_hits = sum(1 for r in sh if r.get("track"))
            ac_hits = sum(1 for r in ac if r.get("track"))
            merged_hits = sum(1 for r in results if r.get("track"))
            print(f"  → merge: shazam={sh_hits}, acr={ac_hits}, union={merged_hits} "
                  f"(/{len(chunks)} chunks)")

    deduped = dedupe(results)
    for r in deduped:
        r.setdefault("_backend", args.backend)

    spotify_count = sum(1 for r in deduped if r.get("spotify_id"))
    print(f"\nmatched {len(deduped)} unique tracks from {len(results)} chunks "
          f"({sum(1 for r in results if r.get('track')) / max(len(results),1):.0%} hit rate)")
    print(f"  spotify IDs from fingerprint: {spotify_count}/{len(deduped)} "
          f"({spotify_count/max(len(deduped),1):.0%})")

    with sqlite3.connect(DB) as db:
        upsert_set(db, video_id, args.dj, title, set_date,
                   args.url, duration, deduped)
    print(f"→ wrote {len(deduped)} tracks to dj_set_tracks (set_id={video_id})")
    if spotify_count < len(deduped):
        print(f"\nnext: python3 resolve_dj_tracks.py --set {video_id} "
              f"({len(deduped) - spotify_count} still need Spotify search)")


if __name__ == "__main__":
    main()
