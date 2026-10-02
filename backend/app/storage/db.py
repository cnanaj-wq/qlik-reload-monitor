"""Connexion SQLite, initialisation du schéma et migrations.

Cette base est la SEULE chose que le monitor écrit (avec demo_data/ en mode
démo). Les logs et QVD Qlik ne sont jamais ouverts en écriture.
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 3


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Ouvre la base (création du dossier parent si besoin) avec des réglages sûrs."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")      # lectures API pendant les écritures
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _schema_sql() -> str:
    return resources.files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")


# Colonnes communes v1/v2 de la table events (ordre physique).
_EVENTS_V2_COLUMNS = (
    "seq, event_key, reload_id, timestamp, source, app_id, app_name, event_type, "
    "status, section, table_name, rows, qvd_name, qvd_path, qvd_size_bytes, message, extra_json"
)

# DDL FIGÉ de events en v2 : une migration ne doit jamais dépendre de schema.sql,
# qui continue d'évoluer.
_EVENTS_V2_DDL = """
CREATE TABLE events (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key       TEXT NOT NULL UNIQUE,
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
    table_name      TEXT,
    rows            INTEGER CHECK (rows IS NULL OR rows >= 0),
    qvd_name        TEXT,
    qvd_path        TEXT,
    qvd_size_bytes  INTEGER CHECK (qvd_size_bytes IS NULL OR qvd_size_bytes >= 0),
    message         TEXT,
    extra_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_events_reload_seq ON events (reload_id, seq);
"""

_PLATFORM_COLUMN = (
    "platform TEXT NOT NULL DEFAULT 'demo' "
    "CHECK (platform IN ('demo', 'qlik_sense', 'qlik_view'))"
)


def _run_migration(conn: sqlite3.Connection, body: str, version: int) -> None:
    """Exécute une migration en UNE transaction : succès complet ou base inchangée."""
    script = f"BEGIN;\n{body}\nPRAGMA user_version = {version};\nCOMMIT;\n"
    try:
        conn.executescript(script)
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """v2 : ajout de QVD_STABLE dans la contrainte CHECK de events.event_type.

    SQLite ne sait pas modifier une contrainte CHECK : la table est recréée
    puis les lignes recopiées À L'IDENTIQUE, `seq` compris (les curseurs SSE
    des clients restent valides).
    """
    _run_migration(conn, (
        "DROP INDEX IF EXISTS ix_events_reload_seq;\n"
        "ALTER TABLE events RENAME TO events_v1;\n"
        + _EVENTS_V2_DDL
        + f"\nINSERT INTO events ({_EVENTS_V2_COLUMNS}) SELECT {_EVENTS_V2_COLUMNS} FROM events_v1;\n"
        "DROP TABLE events_v1;"
    ), 2)


def _migrate_2_to_3(conn: sqlite3.Connection) -> None:
    """v3 : colonne platform (reloads, events) + table notification_log.

    Les lignes existantes reçoivent platform = 'demo' : jusqu'en v2, seul le
    simulateur DEMO pouvait alimenter la base.
    """
    _run_migration(conn, (
        f"ALTER TABLE reloads ADD COLUMN {_PLATFORM_COLUMN};\n"
        f"ALTER TABLE events ADD COLUMN {_PLATFORM_COLUMN};\n"
        "CREATE TABLE notification_log (\n"
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    reload_id TEXT NOT NULL REFERENCES reloads (reload_id),\n"
        "    channel TEXT NOT NULL CHECK (channel IN ('email', 'teams', 'slack', 'webhook')),\n"
        "    recipient TEXT NOT NULL,\n"
        "    status TEXT NOT NULL CHECK (status IN ('PENDING', 'SENT', 'FAILED', 'DRY_RUN')),\n"
        "    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),\n"
        "    subject TEXT,\n"
        "    created_at TEXT NOT NULL,\n"
        "    sent_at TEXT,\n"
        "    error_message TEXT,\n"
        "    UNIQUE (reload_id, channel, recipient)\n"
        ");\n"
        "CREATE INDEX ix_notification_log_reload ON notification_log (reload_id);"
    ), 3)


_MIGRATIONS = {2: _migrate_1_to_2, 3: _migrate_2_to_3}


def init_db(conn: sqlite3.Connection) -> None:
    """Crée le schéma (base neuve) ou applique les migrations manquantes. Idempotent."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Base en version {current}, plus récente que le code (v{SCHEMA_VERSION})"
        )
    if current == 0:
        with conn:
            conn.executescript(_schema_sql())
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return
    for version in range(current + 1, SCHEMA_VERSION + 1):
        _MIGRATIONS[version](conn)
    with conn:
        conn.executescript(_schema_sql())  # no-op si tout existe déjà


def open_db(db_path: str | Path) -> sqlite3.Connection:
    """Raccourci : connexion + schéma."""
    conn = connect(db_path)
    init_db(conn)
    return conn
