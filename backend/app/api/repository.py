"""Accès en LECTURE SEULE à la base SQLite pour l'API.

Chaque appel ouvre sa propre connexion avec `PRAGMA query_only = ON` :
SQLite refuse alors toute écriture, quelle que soit la requête. L'API ne
peut donc rien modifier, même par erreur. Les connexions courtes rendent
l'accès concurrent sûr (plusieurs requêtes et flux SSE en parallèle ;
le mode WAL permet de lire pendant que la démo ou le moteur écrivent).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, Optional

from ..engine import EventEngine, ReloadState
from ..models import ReloadEvent
from ..storage.db import open_db
from ..storage.rows import row_to_event  # noqa: F401 (réexport)


class Repository:
    def __init__(self, db_path: str | Path, *, stable_after_measures: int = 3) -> None:
        self.db_path = Path(db_path)
        self._stable_after = stable_after_measures
        # Seule écriture de l'API : créer le schéma si la base n'existe pas
        # encore (base vide au premier lancement). Idempotent.
        open_db(self.db_path).close()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------- état

    def _engine(self, conn) -> EventEngine:
        # Le moteur ne sert ici qu'à calculer l'état (lecture).
        return EventEngine(conn, stable_after_measures=self._stable_after)

    def current_state(self, app_id: Optional[str] = None) -> Optional[ReloadState]:
        with self.connect() as conn:
            return self._engine(conn).get_current_state(app_id=app_id)

    def active_states(self) -> list[ReloadState]:
        with self.connect() as conn:
            return self._engine(conn).get_active_states()

    def reload_state(self, reload_id: str) -> Optional[ReloadState]:
        with self.connect() as conn:
            return self._engine(conn).get_reload_state(reload_id)

    # -------------------------------------------------------- historique

    def list_reloads(self, *, limit: int, offset: int, app_id: Optional[str] = None,
                     status: Optional[str] = None, platform: Optional[str] = None,
                     app: Optional[str] = None, date_from: Optional[date] = None,
                     date_to: Optional[date] = None) -> tuple[list[dict], int]:
        """Historique filtré, trié par started_at DESC.

        - `app` : recherche partielle, insensible à la casse, sur le nom ou l'identifiant ;
        - `date_from` / `date_to` : bornes INCLUSES sur la date de début (heure locale
          du monitor, comme les horodatages stockés).
        """
        where, params = [], []
        if app_id:
            where.append("app_id = ?")
            params.append(app_id)
        if status:
            where.append("status = ?")
            params.append(status)
        if platform:
            where.append("platform = ?")
            params.append(platform)
        if app:
            like = "%" + app.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(lower(app_name) LIKE ? ESCAPE '\\' OR lower(app_id) LIKE ? ESCAPE '\\')")
            params += [like, like]
        if date_from:
            where.append("started_at >= ?")
            params.append(date_from.isoformat())
        if date_to:
            where.append("started_at < ?")
            params.append((date_to + timedelta(days=1)).isoformat())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM reloads {clause}", params).fetchone()[0]
            rows = conn.execute(
                f"""SELECT r.*,
                       (SELECT COALESCE(SUM(m.size_bytes), 0) FROM qvd_measures m
                         WHERE m.reload_id = r.reload_id AND m.is_stable = 1) AS qvd_bytes,
                       (SELECT COUNT(*) FROM qvd_measures m
                         WHERE m.reload_id = r.reload_id AND m.is_stable = 1) AS qvd_count
                    FROM reloads r {clause}
                    ORDER BY r.started_at DESC, r.reload_id
                    LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
        return [dict(r) for r in rows], total

    def reload_exists(self, reload_id: str) -> bool:
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM reloads WHERE reload_id = ?",
                                (reload_id,)).fetchone() is not None

    # --------------------------------------------------------- événements

    def events(self, reload_id: str, *, after_seq: int = 0, limit: int = 1000,
               order: str = "seq") -> list[ReloadEvent]:
        order_by = "seq" if order == "seq" else "timestamp, seq"
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM events WHERE reload_id = ? AND seq > ?
                    ORDER BY {order_by} LIMIT ?""",
                (reload_id, after_seq, limit),
            ).fetchall()
        return [row_to_event(r) for r in rows]

    def events_after(self, after_seq: int, *, limit: int,
                     reload_id: Optional[str] = None) -> list[ReloadEvent]:
        """Événements de tous les reloads, strictement après `after_seq`, par seq."""
        sql, params = "SELECT * FROM events WHERE seq > ?", [after_seq]
        if reload_id:
            sql += " AND reload_id = ?"
            params.append(reload_id)
        with self.connect() as conn:
            rows = conn.execute(sql + " ORDER BY seq LIMIT ?", [*params, limit]).fetchall()
        return [row_to_event(r) for r in rows]

    def max_seq(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]

    # ------------------------------------------------------- mesures QVD

    def qvd_measures(self, reload_id: str, qvd_name: Optional[str] = None) -> list[dict]:
        sql, params = "SELECT * FROM qvd_measures WHERE reload_id = ?", [reload_id]
        if qvd_name:
            sql += " AND qvd_name = ?"
            params.append(qvd_name)
        with self.connect() as conn:
            rows = conn.execute(sql + " ORDER BY timestamp, id", params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["is_stable"] = bool(d["is_stable"])
            out.append(d)
        return out

    def notifications(self, reload_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT id, reload_id, channel, recipient, status, attempts, subject,
                          created_at, sent_at, error_message
                   FROM notification_log WHERE reload_id = ? ORDER BY id""",
                (reload_id,)).fetchall()
        return [dict(r) for r in rows]

    def schema_version(self) -> int:
        with self.connect() as conn:
            return conn.execute("PRAGMA user_version").fetchone()[0]
