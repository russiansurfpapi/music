"""Phase 3 — compute per-chunk audio features (BPM, RMS energy, key) via librosa.

Re-downloads audio for a fingerprinted set, slices into chunks, runs librosa
on each chunk, writes rows to dj_set_chunks (including rows for chunks where
shazam found nothing — that's the point: we get BPM/energy/key in the
dead zones too).

Usage:
    python3 compute_audio_features.py --set <set_id>
    python3 compute_audio_features.py --all-fingerprinted    # all YouTube sets

For sets already fingerprinted by identify_youtube_set.py, this backfills the
per-chunk data table. It does NOT re-run shazam — the `matched` flag is
populated from existing dj_set_tracks rows by nearest-timestamp lookup.
"""

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Optional

from db import connect
from identify_youtube_set import ydl_download, chunk_wav

HERE = Path(__file__).parent
KEYS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def analyze_chunk(path: Path) -> dict:
    """Returns {'bpm': float, 'rms': float, 'key': str}."""
    import librosa
    import numpy as np
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    # Tempo (librosa.feature.tempo returns array; take first estimate)
    try:
        tempo = float(librosa.feature.tempo(y=y, sr=sr)[0])
    except Exception:
        tempo = 0.0
    # Energy (mean RMS)
    rms = float(np.mean(librosa.feature.rms(y=y)))
    # Key (chroma argmax)
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    key = KEYS[int(np.argmax(chroma.mean(axis=1)))]
    return {"bpm": tempo, "rms": rms, "key": key}


def nearest_matched_track(tracks: list, t_sec: int, window: int = 30) -> Optional[dict]:
    """Given chunk timestamp, find the dj_set_tracks row that starts closest
    before this chunk and within `window` seconds."""
    best = None
    for tr in tracks:
        if tr["timestamp_sec"] <= t_sec and t_sec - tr["timestamp_sec"] <= window:
            if best is None or tr["timestamp_sec"] > best["timestamp_sec"]:
                best = tr
    return best


def process_set(conn, set_id: str, chunk_sec: int = 20, step_sec: int = 45,
                delete_existing: bool = True) -> dict:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT set_id, youtube_url, title FROM dj_sets WHERE set_id = ?",
        (set_id,)
    ).fetchone()
    if not row:
        print(f"no set {set_id}", file=sys.stderr); return {}
    url = row["youtube_url"]
    if not url:
        print(f"set {set_id} has no youtube_url — can't re-download",
              file=sys.stderr); return {}

    tracks = [dict(r) for r in conn.execute(
        "SELECT position, timestamp_sec, raw_artist, raw_title, confidence, source "
        "FROM dj_set_tracks WHERE set_id = ? ORDER BY position", (set_id,)
    ).fetchall()]

    print(f"▶ {set_id}  {row['title'][:70]}")
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        wav = tdir / f"{set_id}.wav"
        print("  downloading...")
        ydl_download(url, wav)
        if not wav.exists():
            cand = list(tdir.glob(f"{set_id}*.wav"))
            if cand:
                wav = cand[0]
        print(f"  chunking {chunk_sec}s windows every {step_sec}s...")
        chunks = chunk_wav(wav, tdir / "chunks", chunk_sec, step_sec)
        print(f"  {len(chunks)} chunks → librosa...")

        if delete_existing:
            conn.execute("DELETE FROM dj_set_chunks WHERE set_id = ?", (set_id,))

        total = 0; matched = 0
        for i, (t0, path) in enumerate(chunks):
            try:
                feat = analyze_chunk(path)
            except Exception as e:
                print(f"  [{t0//60:02d}:{t0%60:02d}] librosa error: {e}", file=sys.stderr)
                continue
            near = nearest_matched_track(tracks, t0, window=step_sec)
            is_match = 1 if near else 0
            conn.execute(
                """INSERT OR REPLACE INTO dj_set_chunks
                   (set_id, chunk_idx, timestamp_sec, matched,
                    raw_artist, raw_title, confidence, source, bpm, rms, key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (set_id, i, t0, is_match,
                 near["raw_artist"] if near else None,
                 near["raw_title"] if near else None,
                 near["confidence"] if near else None,
                 near["source"] if near else None,
                 feat["bpm"], feat["rms"], feat["key"])
            )
            total += 1
            if is_match: matched += 1
            if i % 10 == 0:
                print(f"  [{t0//60:02d}:{t0%60:02d}] bpm={feat['bpm']:.1f} "
                      f"rms={feat['rms']:.3f} key={feat['key']} "
                      f"{'✓' if is_match else '·'}")
        conn.commit()

    hit = matched / total * 100 if total else 0
    print(f"  → {total} chunks, {matched} matched ({hit:.0f}%), {total-matched} dead zones now have BPM/energy/key")
    return {"total": total, "matched": matched}


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--set", dest="set_id")
    g.add_argument("--all-fingerprinted", action="store_true")
    ap.add_argument("--chunk", type=int, default=20)
    ap.add_argument("--step", type=int, default=45)
    args = ap.parse_args()

    with connect() as conn:
        if args.set_id:
            process_set(conn, args.set_id, args.chunk, args.step)
        else:
            conn.row_factory = sqlite3.Row
            ids = [r["set_id"] for r in conn.execute(
                "SELECT set_id FROM dj_sets WHERE youtube_url IS NOT NULL "
                "ORDER BY ingested_at DESC"
            ).fetchall()]
            for sid in ids:
                process_set(conn, sid, args.chunk, args.step)


if __name__ == "__main__":
    main()
