"""Space Miami anatomy: what makes a great 2-4h set?

Analyses:
  1. Set length distribution (track count as proxy for minutes)
  2. Arc — subgenre/genre distribution across 5 quintiles (0-20%, 20-40%, ..., 80-100%)
  3. Anchor tracks played across multiple Space Miami sets
  4. Per-DJ curveballs (tracks whose genre is <5% of that DJ's catalog — out of character)
  5. Energy progression — texture proxy (warm/soulful vs raw/gritty vs aggressive)
"""

import json
from collections import Counter, defaultdict
from db import connect


def _list(s):
    try:
        return json.loads(s) if s else []
    except Exception:
        return []


def main():
    with connect() as conn:
        # Space Miami set IDs
        sm_sets = conn.execute("""
            SELECT set_id, dj_slug, set_date, track_count
            FROM dj_sets
            WHERE set_id LIKE '%club-space-miami%' OR set_id LIKE '%miami-music-week%'
            ORDER BY track_count DESC
        """).fetchall()
        sm_ids = [s["set_id"] for s in sm_sets]
        print(f"# Space Miami Anatomy — {len(sm_ids)} sets analyzed\n")

        # --- 1. Length distribution
        print("## Set length distribution")
        for s in sm_sets:
            tc = s['track_count']
            bar = "█" * min(40, tc // 3)
            est = ("~1h" if tc <= 25 else "~2h" if tc <= 45 else "~3h" if tc <= 60 else "~4h" if tc <= 80 else "5h+")
            print(f"  {s['dj_slug']:20} {s['set_date']}  {tc:3}t  {est:4}  {bar}")

        # --- 2. Arc analysis: subgenre/genre by quintile
        print("\n\n## Arc across a 2-4h set — where each subgenre lives\n")
        print("_Quintiles = opening 20% / second 20% / middle / fourth 20% / closing 20%_\n")
        # Collect all tracks with position and classification
        quintile_counts = [Counter() for _ in range(5)]  # 5 bins
        genre_quintile = [Counter() for _ in range(5)]
        texture_quintile = [Counter() for _ in range(5)]
        rhythm_quintile = [Counter() for _ in range(5)]
        for s in sm_sets:
            tracks = conn.execute("""
                SELECT t.position, c.genre, c.subgenre, c.rhythm, c.texture
                FROM dj_set_tracks t
                JOIN classifications c ON c.spotify_id = t.spotify_id
                WHERE t.set_id = ?
                ORDER BY t.position
            """, (s["set_id"],)).fetchall()
            if not tracks:
                continue
            n = len(tracks)
            for i, t in enumerate(tracks):
                # Quintile 0..4 based on position fraction
                q = min(4, int(5 * i / n))
                if t["subgenre"]:
                    quintile_counts[q][t["subgenre"]] += 1
                if t["genre"]:
                    genre_quintile[q][t["genre"]] += 1
                if t["rhythm"]:
                    rhythm_quintile[q][t["rhythm"]] += 1
                for tex in _list(t["texture"]):
                    texture_quintile[q][tex] += 1

        labels = ["OPENING  (0-20%)", "BUILD    (20-40%)", "PEAK     (40-60%)", "PLATEAU  (60-80%)", "CLOSING  (80-100%)"]
        print("### Top 5 subgenres per quintile")
        for i, lbl in enumerate(labels):
            top = quintile_counts[i].most_common(5)
            total = sum(quintile_counts[i].values()) or 1
            line = ", ".join(f"{k} {100*v/total:.0f}%" for k, v in top)
            print(f"  **{lbl}**: {line}")

        print("\n### Texture shift across a set")
        for i, lbl in enumerate(labels):
            top = texture_quintile[i].most_common(3)
            total = sum(texture_quintile[i].values()) or 1
            line = ", ".join(f"{k} {100*v/total:.0f}%" for k, v in top)
            print(f"  **{lbl}**: {line}")

        print("\n### Rhythm shift")
        for i, lbl in enumerate(labels):
            top = rhythm_quintile[i].most_common(3)
            total = sum(rhythm_quintile[i].values()) or 1
            line = ", ".join(f"{k} {100*v/total:.0f}%" for k, v in top)
            print(f"  **{lbl}**: {line}")

        # --- 3. Space Miami canon: tracks played across multiple Space Miami sets
        print("\n\n## The Space Miami Canon — tracks played across multiple sets here\n")
        sm_id_list = "','".join(sm_ids)
        rows = conn.execute(f"""
            SELECT t.raw_artist || ' — ' || t.raw_title AS track,
                   COUNT(DISTINCT t.set_id) AS n_sets,
                   GROUP_CONCAT(DISTINCT s.dj_slug) AS djs
            FROM dj_set_tracks t
            JOIN dj_sets s ON s.set_id = t.set_id
            WHERE t.set_id IN ('{sm_id_list}')
              AND LOWER(t.raw_artist) NOT IN ('id', 'i.d.', 'unknown')
            GROUP BY track
            HAVING n_sets >= 2
            ORDER BY n_sets DESC, track LIMIT 25
        """).fetchall()
        if rows:
            for r in rows:
                print(f"  `{r['n_sets']} sets` · {r['track']}  _(by {r['djs']})_")
        else:
            print("  (none yet — low overlap between current set selection)")

        # --- 4. Subgenre overlap matrix: which DJs share territory
        print("\n\n## Subgenre character by DJ (Space Miami only)\n")
        print("_What each DJ stays on when playing Space Miami_\n")
        for s in sm_sets:
            if s['track_count'] < 20:
                continue
            tracks = conn.execute("""
                SELECT c.subgenre FROM dj_set_tracks t
                JOIN classifications c ON c.spotify_id=t.spotify_id
                WHERE t.set_id=? AND c.subgenre IS NOT NULL
            """, (s["set_id"],)).fetchall()
            if not tracks:
                continue
            c = Counter(t["subgenre"] for t in tracks)
            top3 = c.most_common(3)
            total = sum(c.values()) or 1
            line = ", ".join(f"{k} {100*v/total:.0f}%" for k, v in top3)
            print(f"  **{s['dj_slug']:18}** ({s['track_count']:3}t, {s['set_date']})  →  {line}")

        # --- 5. Curveballs: per-DJ out-of-character tracks (rare genres for that DJ, in a Space Miami set)
        print("\n\n## Curveballs — out-of-character drops in Space Miami sets\n")
        print("_A track whose genre is <5% of that DJ's catalog across ALL their sets — intentional surprises_\n")
        # For each DJ with ≥2 sets overall, compute genre share; flag <5% plays in Space Miami
        for s in sm_sets:
            # DJ's all-time genre distribution
            all_rows = conn.execute("""
                SELECT c.genre, COUNT(*) AS n FROM dj_set_tracks t
                JOIN dj_sets ss ON t.set_id=ss.set_id
                JOIN classifications c ON c.spotify_id=t.spotify_id
                WHERE ss.dj_slug=? AND c.genre IS NOT NULL
                GROUP BY c.genre
            """, (s["dj_slug"],)).fetchall()
            if not all_rows:
                continue
            total = sum(r["n"] for r in all_rows)
            if total < 15:
                continue
            rare_genres = {r["genre"] for r in all_rows if r["n"] / total < 0.05}
            if not rare_genres:
                continue
            # Find tracks in THIS Space Miami set with rare genres
            curvies = conn.execute("""
                SELECT t.position, t.raw_artist, t.raw_title, c.genre, c.subgenre
                FROM dj_set_tracks t
                JOIN classifications c ON c.spotify_id=t.spotify_id
                WHERE t.set_id=? AND c.genre IN (%s)
                ORDER BY t.position
            """ % ",".join("?"*len(rare_genres)), (s["set_id"], *rare_genres)).fetchall()
            if not curvies:
                continue
            print(f"\n### {s['dj_slug']} @ Space Miami {s['set_date']}")
            for cv in curvies[:6]:
                print(f"  - [{cv['genre']:>10}] track #{cv['position']}: {cv['raw_artist']} — {cv['raw_title']}")


if __name__ == "__main__":
    main()
