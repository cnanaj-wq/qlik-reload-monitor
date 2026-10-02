"""Schémas historiques figés, pour tester les migrations depuis une vraie base ancienne."""

SCHEMA_V1 = """-- Qlik Reload Monitor — schéma SQLite minimal (version 1)
-- Idempotent : peut être rejoué à chaque démarrage.

CREATE TABLE IF NOT EXISTS reloads (
    reload_id       TEXT PRIMARY KEY,
    app_id          TEXT NOT NULL,
    app_name        TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('demo', 'qlik_log', 'qvd_watcher')),
    status          TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'WARNING', 'ERROR')),
    started_at      TEXT NOT NULL,           -- ISO 8601
    ended_at        TEXT,                    -- NULL tant que le reload n'est pas terminé
    duration_ms     INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    total_rows      INTEGER NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
    warnings_count  INTEGER NOT NULL DEFAULT 0 CHECK (warnings_count >= 0),
    errors_count    INTEGER NOT NULL DEFAULT 0 CHECK (errors_count >= 0)
);

CREATE INDEX IF NOT EXISTS ix_reloads_app_started ON reloads (app_id, started_at);

CREATE TABLE IF NOT EXISTS events (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,   -- ordre global, sert à la reprise SSE
    event_key       TEXT NOT NULL UNIQUE,                -- déduplication
    reload_id       TEXT NOT NULL REFERENCES reloads (reload_id),
    timestamp       TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('demo', 'qlik_log', 'qvd_watcher')),
    app_id          TEXT NOT NULL,
    app_name        TEXT NOT NULL,
    event_type      TEXT NOT NULL CHECK (event_type IN (
                        'RELOAD_START', 'RELOAD_END',
                        'SECTION_START', 'SECTION_END',
                        'TABLE_START', 'TABLE_PROGRESS', 'TABLE_END',
                        'QVD_WRITE_START', 'QVD_SIZE_CHANGE', 'QVD_WRITE_END',
                        'WARNING', 'ERROR')),
    status          TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'WARNING', 'ERROR')),
    section         TEXT,
    table_name      TEXT,                    -- "table" est un mot réservé SQL
    rows            INTEGER CHECK (rows IS NULL OR rows >= 0),
    qvd_name        TEXT,
    qvd_path        TEXT,
    qvd_size_bytes  INTEGER CHECK (qvd_size_bytes IS NULL OR qvd_size_bytes >= 0),
    message         TEXT,
    extra_json      TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_events_reload_seq ON events (reload_id, seq);

CREATE TABLE IF NOT EXISTS qvd_measures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    reload_id       TEXT REFERENCES reloads (reload_id),   -- NULL si non rattaché à un reload
    qvd_name        TEXT NOT NULL,
    path            TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL CHECK (size_bytes >= 0),
    delta_bytes     INTEGER NOT NULL,        -- peut être négatif (QVD réécrit plus petit)
    is_stable       INTEGER NOT NULL DEFAULT 0 CHECK (is_stable IN (0, 1)),
    source          TEXT NOT NULL CHECK (source IN ('demo', 'qlik_log', 'qvd_watcher'))
);

CREATE INDEX IF NOT EXISTS ix_qvd_measures_path_ts ON qvd_measures (path, timestamp);
CREATE INDEX IF NOT EXISTS ix_qvd_measures_reload ON qvd_measures (reload_id);
"""
