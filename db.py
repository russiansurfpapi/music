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
    raw_artist  TEXT NOT NULL,
    raw_title   TEXT NOT NULL,
    spotify_id  TEXT,                      -- nullable; soft-link to tracks
    PRIMARY KEY (set_id, position),
    FOREIGN KEY (set_id) REFERENCES dj_sets(set_id)
);

CREATE INDEX IF NOT EXISTS idx_dj_sets_dj ON dj_sets(dj_slug);
CREATE INDEX IF NOT EXISTS idx_dj_sets_date ON dj_sets(set_date);
CREATE INDEX IF NOT EXISTS idx_dj_set_tracks_sid ON dj_set_tracks(spotify_id);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    print(f"Initialized {DB_PATH}")


if __name__ == "__main__":
    init()
