#!/bin/bash
# Phase 1 bridge — take fingerprinted YouTube sets through the existing
# classification pipeline so set_shapes.py / dj_archetype.py / etc. can analyze them.
#
# Assumes dj_set_tracks rows already exist from identify_youtube_set.py.
# Safe to re-run (each stage is idempotent).

set -eu
cd "$(dirname "$0")"

echo "═══ [1/3] Resolving tracks via Spotify cache (no API) ═══"
python3 resolve_dj_tracks.py cache

echo ""
echo "═══ [2/3] Fetching Last.fm tags for new tracks ═══"
python3 lastfm_tags.py || echo "  (skipped — lastfm_tags.py may need --help flags)"

echo ""
echo "═══ [3/3] Running 6-layer classification ═══"
python3 classify.py

echo ""
echo "✓ bridge complete. Fingerprinted sets are now analyzable via:"
echo "    python3 set_shapes.py --set <set_id>"
echo "    python3 set_energy.py --set <set_id>"
echo "    python3 set_anatomy.py --set <set_id>    # timestamp-aware (new)"
echo "    python3 dj_archetype.py --dj <slug>"
