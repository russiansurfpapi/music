"""SQLite schema for the tagged library."""

import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "library.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    spotify_id       TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    artist           TEXT NOT NULL,
    artist_id        TEXT,
    album            TEXT,
    release_year     INTEGER,
    duration_ms      INTEGER,
    added_at         TEXT,
    -- audio features
    tempo            REAL,
    energy           REAL,
    danceability     REAL,
    valence          REAL,
    acousticness     REAL,
    instrumentalness REAL,
    features_status  TEXT DEFAULT 'pending',  -- pending|ok|unavailable
    -- enrichment status
    lastfm_status    TEXT DEFAULT 'pending',  -- pending|track|artist|missing
    updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS track_sources (
    spotify_id   TEXT NOT NULL,
    source       TEXT NOT NULL,   -- liked | recent | playlist:<name> | lets_groove
    PRIMARY KEY (spotify_id, source),
    FOREIGN KEY (spotify_id) REFERENCES tracks(spotify_id)
);

CREATE TABLE IF NOT EXISTS artist_genres (
    artist_id   TEXT NOT NULL,
    genre       TEXT NOT NULL,
    PRIMARY KEY (artist_id, genre)
);

CREATE TABLE IF NOT EXISTS track_tags (
    spotify_id  TEXT NOT NULL,
    tag         TEXT NOT NULL,
    count       INTEGER DEFAULT 0,   -- last.fm weight (0..100)
    level       TEXT NOT NULL,       -- 'track' | 'artist'
    PRIMARY KEY (spotify_id, tag, level),
    FOREIGN KEY (spotify_id) REFERENCES tracks(spotify_id)
);

CREATE TABLE IF NOT EXISTS classifications (
    spotify_id     TEXT PRIMARY KEY,
    genre          TEXT,
    subgenre       TEXT,
    production_dna TEXT,   -- JSON array
    rhythm         TEXT,
    texture        TEXT,   -- JSON array
    lineage        TEXT,   -- JSON array
    confidence     TEXT,   -- high | medium | low
    classified_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (spotify_id) REFERENCES tracks(spotify_id)
);

CREATE INDEX IF NOT EXISTS idx_tracks_lastfm_status ON tracks(lastfm_status);
CREATE INDEX IF NOT EXISTS idx_tracks_features_status ON tracks(features_status);
CREATE INDEX IF NOT EXISTS idx_track_sources_source ON track_sources(source);

CREATE TABLE IF NOT EXISTS djs (
    slug          TEXT PRIMARY KEY,        -- 'shadow-child', 'dj-koze'
    name          TEXT NOT NULL,           -- 'Shadow Child'
    is_favorite   INTEGER DEFAULT 0,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS dj_sets (
    set_id          TEXT PRIMARY KEY,      -- HTML filename stem
    dj_slug         TEXT NOT NULL,
    title           TEXT,                  -- venue/show parsed from filename
    set_date        TEXT,                  -- YYYY-MM-DD parsed from filename
    source_file     TEXT NOT NULL,         -- relative path to sets/<file>.html
    track_count     INTEGER DEFAULT 0,
    resolved_count  INTEGER DEFAULT 0,
    ingested_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dj_slug) REFERENCES djs(slug)
);

CREATE TABLE IF NOT EXISTS dj_set_tracks (
    set_id      TEXT NOT NULL,
    position    INTEGER NOT NULL,
    raw_artist  TEXT NOT NULL,             -- exactly as scraped; never rewritten
    raw_title   TEXT NOT NULL,
    artist      TEXT,                      -- normalised lead artist
    title       TEXT,                      -- normalised title
    spotify_id  TEXT,                      -- nullable; soft-link to tracks
    PRIMARY KEY (set_id, position),
    FOREIGN KEY (set_id) REFERENCES dj_sets(set_id)
);

-- Artist/title -> Spotify ID lookups. Previously spotify_cache.json plus a
-- separate spotify_search_misses.json; a NULL spotify_id now records a search
-- that came back empty, so we never pay for the same dead end twice.
CREATE TABLE IF NOT EXISTS spotify_id_cache (
    cache_key   TEXT PRIMARY KEY,          -- "<artist>||<title>", lowercased
    spotify_id  TEXT,                      -- NULL = searched, no match
    searched_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- What a Spotify playlist contained the last time we looked. Previously
-- discovery_playlist_ids.json. Superseded by playlists/playlist_tracks, kept
-- because --source playlist still reads it.
CREATE TABLE IF NOT EXISTS playlist_snapshots (
    playlist_id TEXT NOT NULL,
    spotify_id  TEXT NOT NULL,
    snapshot_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (playlist_id, spotify_id)
);

-- Spotify request accounting. The daily quota is the binding constraint on
-- this whole pipeline, so we count what we spend and refuse to exceed it
-- rather than discovering the ceiling by getting banned for 24 hours.
CREATE TABLE IF NOT EXISTS spotify_api_usage (
    day            TEXT PRIMARY KEY,   -- YYYY-MM-DD, local
    requests       INTEGER DEFAULT 0,
    last_429_at    TEXT,
    cooldown_until TEXT,               -- no calls until this passes
    requests_at_ban INTEGER            -- spend when the ban landed; teaches the ceiling
);

-- Every Spotify playlist we know about, whether we built it or not.
CREATE TABLE IF NOT EXISTS playlists (
    playlist_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    owner        TEXT,
    description  TEXT,
    is_managed   INTEGER DEFAULT 0,   -- 1 = built by this repo
    managed_kind TEXT,                -- subgenre | genre | dj_study | session
    managed_key  TEXT,                -- e.g. 'deep house'
    track_count  INTEGER,
    snapshot_id  TEXT,                -- changes iff the membership changed
    synced_at    TEXT
);

-- Which tracks are on which playlist. The answer to "what is this song in?".
CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id TEXT NOT NULL,
    spotify_id  TEXT NOT NULL,
    position    INTEGER,
    PRIMARY KEY (playlist_id, spotify_id),
    FOREIGN KEY (playlist_id) REFERENCES playlists(playlist_id)
);

CREATE INDEX IF NOT EXISTS idx_playlist_tracks_sid ON playlist_tracks(spotify_id);
CREATE INDEX IF NOT EXISTS idx_playlists_managed ON playlists(is_managed, managed_kind);

CREATE INDEX IF NOT EXISTS idx_spotify_id_cache_id ON spotify_id_cache(spotify_id);
CREATE INDEX IF NOT EXISTS idx_dj_sets_dj ON dj_sets(dj_slug);
CREATE INDEX IF NOT EXISTS idx_dj_sets_date ON dj_sets(set_date);
CREATE INDEX IF NOT EXISTS idx_dj_set_tracks_sid ON dj_set_tracks(spotify_id);

-- One row per (track, place it came from). A track played in 8 different sets
-- gets 8 rows, so "where did this come from" is answerable in full rather than
-- collapsed to a single DJ name the way track_sources does it.
CREATE VIEW IF NOT EXISTS track_provenance AS
    SELECT t.spotify_id,
           t.artist,
           t.title,
           'dj_set'          AS source_kind,
           ds.dj_slug        AS dj_slug,
           COALESCE(dj.name, ds.dj_slug) AS dj_name,
           ds.set_id         AS set_id,
           ds.title          AS set_title,
           ds.set_date       AS set_date,
           dst.position      AS set_position,
           dst.raw_artist    AS scraped_as
    FROM tracks t
    JOIN dj_set_tracks dst ON dst.spotify_id = t.spotify_id
    JOIN dj_sets ds        ON ds.set_id      = dst.set_id
    LEFT JOIN djs dj       ON dj.slug        = ds.dj_slug
    UNION ALL
    SELECT t.spotify_id, t.artist, t.title,
           ts.source, NULL, NULL, NULL, NULL, NULL, NULL, NULL
    FROM tracks t
    JOIN track_sources ts ON ts.spotify_id = t.spotify_id
    WHERE ts.source NOT LIKE 'dj_set:%';
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


# (table, column, type) added after the table first shipped. ALTER TABLE ADD
# COLUMN is the only safe in-place change SQLite gives us.
_ADDED_COLUMNS = [
    ("dj_set_tracks", "artist", "TEXT"),
    ("dj_set_tracks", "title", "TEXT"),
    ("playlists", "snapshot_id", "TEXT"),
    ("spotify_api_usage", "requests_at_ban", "INTEGER"),
]


def migrate(conn) -> None:
    """Add columns that post-date the original CREATE TABLE. Idempotent."""
    for table, column, coltype in _ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
    print(f"Initialized {DB_PATH}")


if __name__ == "__main__":
    init()
