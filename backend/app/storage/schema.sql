-- Qlik Reload Monitor — schéma SQLite (version 3)
-- v2 : événement QVD_STABLE ; v3 : colonne platform + table notification_log.
-- Les colonnes ajoutées par migration sont placées en fin de table, comme le fait
-- ALTER TABLE ADD COLUMN : une base neuve et une base migrée ont la même structure.
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
    errors_count    INTEGER NOT NULL DEFAULT 0 CHECK (errors_count >= 0),
    platform        TEXT NOT NULL DEFAULT 'demo' CHECK (platform IN ('demo', 'qlik_sense', 'qlik_view'))
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
                        'QVD_WRITE_START', 'QVD_SIZE_CHANGE', 'QVD_WRITE_END', 'QVD_STABLE',
                        'WARNING', 'ERROR')),
    status          TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'WARNING', 'ERROR')),
    section         TEXT,
    table_name      TEXT,                    -- "table" est un mot réservé SQL
    rows            INTEGER CHECK (rows IS NULL OR rows >= 0),
    qvd_name        TEXT,
    qvd_path        TEXT,
    qvd_size_bytes  INTEGER CHECK (qvd_size_bytes IS NULL OR qvd_size_bytes >= 0),
    message         TEXT,
    extra_json      TEXT NOT NULL DEFAULT '{}',
    platform        TEXT NOT NULL DEFAULT 'demo' CHECK (platform IN ('demo', 'qlik_sense', 'qlik_view'))
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

-- Journal des notifications : une ligne par (reload, canal, destinataire).
-- La contrainte UNIQUE garantit qu'une occurrence de reload ne déclenche
-- jamais deux fois la même notification, même après redémarrage.
CREATE TABLE IF NOT EXISTS notification_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reload_id       TEXT NOT NULL REFERENCES reloads (reload_id),
    channel         TEXT NOT NULL CHECK (channel IN ('email', 'teams', 'slack', 'webhook')),
    recipient       TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('PENDING', 'SENT', 'FAILED', 'DRY_RUN')),
    attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    subject         TEXT,
    created_at      TEXT NOT NULL,
    sent_at         TEXT,
    error_message   TEXT,
    UNIQUE (reload_id, channel, recipient)
);

CREATE INDEX IF NOT EXISTS ix_notification_log_reload ON notification_log (reload_id);
