"""Normalise scraped artist/title strings into Last.fm-friendly candidates.

Two defects in the scraped data kill Last.fm lookups outright:

1. Featured artists are concatenated without a separator — the scraper emits
   "Disclosureft. Eliza Doolittle" as one artist string.
2. Diacritics are not autocorrected by Last.fm. "RÜFÜS DU SOL" returns zero
   tags; "Rufus Du Sol" returns them fine.

`artist_candidates()` returns progressively looser spellings to try in order.
"""

import re
import unicodedata

# "Disclosureft. Eliza" / "A feat. B" / "A featuring B" / "A ft B"
_FEAT = re.compile(r"(?i)\s*\bf(?:ea)?t\.?\s+|\s+featuring\s+|(?<=[a-z])ft\.\s*")
# collaborator separators
_COLLAB = re.compile(r"(?i)\s*(?:&|\+|,|/| x | vs\.? | with | b2b | and )\s*")
# trailing country/label tags: "Chico Rose (NL)", "VLTRA (IT)"
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
# remix / edit suffixes on titles
# NB: no \b before remix/mix — the scraper concatenates words, so
# "(D-Nox & BeckersRemix)" has no boundary in front of "Remix".
_TITLE_SUFFIX = re.compile(
    r"(?i)\s*[\(\[]\s*[^)\]]*"
    r"(?:remix|mix|edit|version|bootleg|rework|remaster|extended|"
    r"original|radio|club|vip|instrumental|acapella)"
    r"[^)\]]*[\)\]]?\s*$"
)


# Scraper occasionally leaks markup into a field, e.g.
# 'Mac Wethaft. Aminé/index.html" class="notranslate tgHid">... & beabadoobee'.
# Rare (1 row in 6,940) but cheap to defend against at the boundary.
_HTML = re.compile(r"<[^>]*>|/[\w-]+\.html?\b.*$|\s+\w+=\"[^\"]*\".*$")
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|#\d+);")


def strip_markup(s):
    """Remove leaked HTML tags, attributes, entities and the U+FFFD marker."""
    if not s:
        return ""
    s = _HTML.sub("", s)
    s = _ENTITY.sub(" ", s).replace("\ufffd", " ")
    return re.sub(r"\s{2,}", " ", s).strip()


def _fold(s):
    """Strip diacritics: 'RÜFÜS' -> 'RUFUS'. Last.fm does not autocorrect these."""
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def _titlecase_shout(s):
    """'RUFUS DU SOL' -> 'Rufus Du Sol'. Last.fm matches the cased form."""
    return s.title() if s.isupper() and len(s) > 3 else s


def primary_artist(artist):
    """The lead artist: everything before the first feature/collab marker."""
    if not artist:
        return ""
    a = _FEAT.split(strip_markup(artist))[0]
    a = _COLLAB.split(a)[0]
    return a.strip(" -–—&,/")


def clean_title(title):
    """Title with one trailing remix/edit qualifier removed."""
    if not title:
        return ""
    t = _TITLE_SUFFIX.sub("", strip_markup(title)).strip(" -–—")
    return t or title.strip()


def artist_candidates(artist):
    """Spellings to try, most faithful first, deduped and non-empty."""
    out = []
    for base in (strip_markup(artist), primary_artist(artist)):
        if not base:
            continue
        base = base.strip()
        for v in (base, _PAREN.sub("", base).strip()):
            if not v:
                continue
            for w in (v, _fold(v), _titlecase_shout(v), _titlecase_shout(_fold(v))):
                w = w.strip()
                if w and w not in out:
                    out.append(w)
    return out
