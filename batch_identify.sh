#!/bin/bash
# Serial batch runner for YouTube DJ-set fingerprinting.
# Edit the queue below, run: bash batch_identify.sh
set -eu
cd "$(dirname "$0")"

run() {
  local url="$1" slug="$2" date="$3" title="$4"
  echo ""
  echo "════════════════════════════════════════"
  echo "▶ $slug — $title"
  echo "════════════════════════════════════════"
  python3 identify_youtube_set.py "$url" --dj "$slug" --date "$date" --title "$title" --backend shazam || \
    echo "  ✗ failed (continuing to next)"
}

# slug, date, title
run "https://www.youtube.com/watch?v=DuHtRCWrALM" "carlita-tennis-kaz-bm"  "2025-08-30" "Carlita B3B DJ Tennis & Kaz James — Burning Man 2025 (Sunset, Edge of the Playa)"
run "https://www.youtube.com/watch?v=DBzleT8XF0M" "baltra"                 "2023-01-01" "Baltra — Boiler Room New York (Live Set)"
run "https://www.youtube.com/watch?v=nKHpbiYCtDQ" "peggy-gou"              "2019-08-16" "Peggy Gou — Boiler Room x Dekmantel Festival, Amsterdam"
run "https://www.youtube.com/watch?v=M_vDO25av6E" "peggy-gou"              "2025-07-01" "Peggy Gou — BitterSweet Festival 2025"
run "https://www.youtube.com/watch?v=LwJCSYLDnYM" "yaeji"                  "2018-02-01" "Yaeji — Boiler Room: New York"
run "https://www.youtube.com/watch?v=uBp6t_wf8_M" "dj-koze"                "2024-01-01" "DJ Koze @ CSides Festival"
run "https://www.youtube.com/watch?v=I0vq-zYsvxI" "dj-koze"                "2009-02-20" "DJ Koze @ Indigo Club Istanbul"
run "https://www.youtube.com/watch?v=6ozCirwYTwM" "blackchild"             "2024-01-01" "Blackchild — Space Miami (presented by Link Miami Rebels)"
run "https://www.youtube.com/watch?v=oDsU1Mzllbc" "hot-since-82"           "2025-07-01" "Hot Since 82 — Dance Arena, EXIT 2025"

echo ""
echo "════════════════════════════════════════"
echo "✓ batch complete"
echo "════════════════════════════════════════"
