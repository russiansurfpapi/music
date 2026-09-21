"""Live listen mode — identify tracks as they play and feed the set sketcher.

Captures audio from the system via ffmpeg (avfoundation on macOS), Shazam-ID's
each chunk, looks up the matched track in library.db for classification, and
appends it to the shared `.sketch_state.json`. The web-app at localhost:5000
auto-refreshes to show new tracks, advisories, and forks.

Usage:
  # List available audio devices (find your loopback / mic index)
  python3 live_listen.py --list-devices

  # Listen to the default mic
  python3 live_listen.py --device ":0" --hours 3

  # Listen via BlackHole loopback (DJing into speakers, BlackHole captures system audio)
  python3 live_listen.py --device ":2" --hours 3 --like "mochakk,duke-dumont"

  # Start from a fresh sketch
  python3 live_listen.py --device ":0" --reset --hours 3

While running, also keep the web-app open at http://127.0.0.1:5000 for visuals.
"""

import argparse
import asyncio
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from set_sketch import (
    load_state, save_state, init_state,
    lookup_library, get_forks, current_position_frac,
    analyze_state,
)
from transition_atlas import bpm_mid

CHUNK_SEC_DEFAULT = 12


# ---------------------------------------------------------------------------
# Audio capture (ffmpeg subprocess, avfoundation on macOS)
# ---------------------------------------------------------------------------

def list_audio_devices() -> str:
    """ffmpeg lists devices to stderr on macOS. Return the whole output."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "avfoundation",
         "-list_devices", "true", "-i", ""],
        capture_output=True, text=True, check=False,
    )
    return r.stderr


def record_chunk(device: str, seconds: int, out_path: Path) -> None:
    """Record `seconds` of audio from the given avfoundation device to a mono 16kHz wav."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "avfoundation", "-i", device,
         "-t", str(seconds), "-ac", "1", "-ar", "16000",
         str(out_path)],
        check=True,
    )


# ---------------------------------------------------------------------------
# Shazam identify (reuses shazamio)
# ---------------------------------------------------------------------------

async def shazam_one(wav_path: Path):
    from shazamio import Shazam
    shazam = Shazam()
    try:
        r = await shazam.recognize(str(wav_path))
    except Exception as e:
        return None, None, str(e)
    track = r.get("track")
    if not track:
        return None, None, None
    return track.get("subtitle", ""), track.get("title", ""), None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _pos_label(frac: float) -> str:
    if frac < 0.2: return "OPEN"
    if frac < 0.4: return "BUILD"
    if frac < 0.6: return "PEAK"
    if frac < 0.8: return "PLATEAU"
    return "CLOSE"


def render(state: dict) -> None:
    tracks = state.get("tracks", [])
    n = len(tracks)
    target = state.get("target_tracks", 0)
    frac = n / max(1, target) if target else 0

    print("\n" + "─" * 60)
    if tracks:
        last = tracks[-1]
        sg = last.get("subgenre") or last.get("genre") or "—"
        bpm = bpm_mid(sg) or "?"
        print(f"▶ Last ID: {last['artist']} — {last['title']}")
        print(f"  [{sg}]  {bpm} BPM")
    print(f"Position: {n}/{target} ({int(100*frac)}%, {_pos_label(frac)})")

    signals = analyze_state(state)
    if signals:
        print()
        for s in signals:
            marker = {"info": "·", "nudge": "⚠", "warn": "🚨"}.get(s["level"], "·")
            # Strip markdown ** for terminal
            text = s["text"].replace("**", "")
            print(f"  {marker} {text}")

    # Show top 3 forks based on current subgenre
    current_sg = tracks[-1].get("subgenre") or tracks[-1].get("genre") if tracks else None
    if current_sg:
        with sqlite3.connect(HERE / "library.db") as conn:
            conn.row_factory = sqlite3.Row
            forks = get_forks(conn, current_sg, frac)
            if any(f["target_sg"] for f in forks):
                print("\n  Suggested forks:")
                for f in forks:
                    if not f["target_sg"]:
                        continue
                    b = bpm_mid(f["target_sg"]) or "?"
                    n_cites = len(f["citations"])
                    print(f"    [{f['letter']}] {f['move']:12} → {f['target_sg']:22} ({b} BPM, {n_cites} cites)")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main_loop(args, state: dict):
    last_ident = None
    conn = sqlite3.connect(HERE / "library.db")
    conn.row_factory = sqlite3.Row

    while True:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "chunk.wav"
            print(f"\n[listening {args.chunk}s on {args.device}...]", flush=True)
            try:
                record_chunk(args.device, args.chunk, tmp)
            except subprocess.CalledProcessError as e:
                print(f"  recording failed (is the device correct? try --list-devices): {e}")
                await asyncio.sleep(2)
                continue

            print("  → Shazam...")
            artist, title, err = await shazam_one(tmp)
            if err:
                print(f"  error: {err}")
                await asyncio.sleep(2)
                continue
            if not artist:
                print("  (no match — same track, silence, or unidentifiable)")
                continue

            ident_key = (artist.lower().strip(), title.lower().strip())
            if ident_key == last_ident:
                print(f"  (still: {artist} — {title})")
                continue
            last_ident = ident_key

            print(f"  ✓ MATCH: {artist} — {title}")

            # Enrich via library lookup
            m = lookup_library(conn, artist, title)
            if m:
                sg_label = m["subgenre"] or m["genre"] or "—"
                print(f"  Library hit: [{sg_label}]")
                state["tracks"].append({
                    "artist": m["artist"], "title": m["title"],
                    "spotify_id": m["spotify_id"],
                    "genre": m["genre"], "subgenre": m["subgenre"],
                    "rhythm": m["rhythm"], "texture": m["texture"],
                    "release_year": m["release_year"],
                    "identified_at": time.time(),
                    "source": "shazam",
                })
            else:
                print(f"  Not in library — added unclassified")
                state["tracks"].append({
                    "artist": artist, "title": title, "spotify_id": None,
                    "genre": None, "subgenre": None, "rhythm": None, "texture": None,
                    "release_year": None,
                    "identified_at": time.time(),
                    "source": "shazam",
                })

            save_state(state)
            render(state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default=":0",
                    help="avfoundation device index. Use --list-devices to see options. "
                         "':0' is typically default mic, ':1' or ':2' is BlackHole loopback if installed.")
    ap.add_argument("--hours", type=float, default=3.0,
                    help="Target set length (used if starting fresh)")
    ap.add_argument("--chunk", type=int, default=CHUNK_SEC_DEFAULT,
                    help=f"Chunk length in seconds (default {CHUNK_SEC_DEFAULT})")
    ap.add_argument("--list-devices", action="store_true",
                    help="List audio devices ffmpeg can see, then exit")
    ap.add_argument("--reset", action="store_true",
                    help="Start a fresh sketch (wipe existing .sketch_state.json)")
    args = ap.parse_args()

    if args.list_devices:
        print(list_audio_devices())
        print("\nUse --device ':N' where N is the AVFoundation audio device index "
              "from the '[AVFoundation indev] audio devices' section above.")
        return

    state = {} if args.reset else load_state()
    if not state:
        state = init_state(args.hours, [])
        save_state(state)
        print(f"▶ Fresh live sketch: target {state['target_tracks']} tracks over {state['target_hours']}h")
    else:
        print(f"▶ Resuming sketch with {len(state.get('tracks', []))} tracks")

    print(f"  Device: {args.device}  ·  Chunk: {args.chunk}s")
    print(f"  Shared state in .sketch_state.json — web-app at http://127.0.0.1:5000 auto-reflects new tracks")
    print(f"  Ctrl-C to stop.\n")

    try:
        asyncio.run(main_loop(args, state))
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
