"""The title cleaner in resolve_dj_tracks.py: a fingerprinted title like "Glue (Extended Mix)"
must also be searched as "Glue" by the lead artist, or Spotify misses it. Offline, no API calls.
Run: python3 -m pytest tests/test_title_cleaner.py
"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import resolve_dj_tracks as r


def base(t):
    return r._base_title(t)


def test_version_tags_are_stripped():
    assert base("Glue (Extended Mix)") == "Glue"
    assert base("Baby Again - Skrillex Remix") == "Baby Again"
    assert base("Rain [Kerri Chandler VIP]") == "Rain"
    assert base("Silhouettes (I, II & III) - Original Mix") == "Silhouettes (I, II & III)"
    assert base("Strings of Life (feat. Someone)") == "Strings of Life"


def test_a_title_that_is_only_a_tag_is_kept():
    # Nothing left after stripping -> search the raw title, never an empty query.
    assert base("(Original Mix)").strip()


def test_garbled_text_is_repaired():
    assert r._fix_text("Fiesta MÃ¡gica") == "Fiesta Mágica"        # UTF-8 read as Latin-1
    assert r._fix_text("Ã‰dith Piaf") == "Édith Piaf"             # UTF-8 read as Windows-1252
    assert r._fix_text("Plain title") == "Plain title"


def test_queries_go_from_faithful_to_clean_and_never_repeat():
    qs, a, t, bt = r._queries("Fred again.. & Four Tet", "Baby Again - Skrillex Remix")
    assert qs[0] == "track:Baby Again - Skrillex Remix artist:Fred again.. & Four Tet"
    assert "track:Baby Again artist:Fred again.." in qs
    assert len(qs) == len({q.lower() for q in qs})
    qs, *_ = r._queries("Bicep", "Glue")
    assert len(qs) == 2          # the cleaned query is the raw one, so it is dropped


def test_set_scope_reads_ids_or_a_discovery_file(tmp_path):
    assert r._set_scope(types.SimpleNamespace(sets=None)) == ("", [])
    sql, ids = r._set_scope(types.SimpleNamespace(sets="a, b"))
    assert ids == ["a", "b"] and sql.count("?") == 2
    f = tmp_path / "sets.json"
    f.write_text(json.dumps([{"id": "x"}, {"id": "y"}, {"id": "z"}]))
    sql, ids = r._set_scope(types.SimpleNamespace(sets=str(f)))
    assert ids == ["x", "y", "z"] and "set_id IN" in sql
