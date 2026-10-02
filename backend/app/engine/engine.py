"""Moteur d'événements : point d'entrée unique de tous les producteurs.

Principes
- SQLite est la source de vérité. Les agrégats d'un reload sont RECALCULÉS
  depuis la table `events` à chaque insertion : l'ordre d'arrivée, les
  doublons et les redémarrages n'ont donc aucun effet sur le résultat.
- La seule information gardée en mémoire est le suivi de stabilité des QVD
  (dernière taille + nombre de mesures identiques). Elle est rechargée
  depuis `qvd_measures` au démarrage.
- Déterministe : l'heure courante est injectable (paramètre `now` / `clock`).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Optional

from ..models.enums import EventSource, EventStatus, EventType, Platform
from ..models.events import ReloadEvent
from .state import (
    IngestResult,
    QvdMeasure,
    ReloadState,
    derive_reload_status,
)

log = logging.getLogger(__name__)

# Événements qui décrivent l'avancement (les WARNING/ERROR ne changent pas l'étape).
_STEP_EVENTS = frozenset(EventType) - {EventType.WARNING, EventType.ERROR}


def _ts(dt: datetime) -> str:
    """Format de stockage unique : ordre lexicographique = ordre chronologique."""
    return dt.isoformat(timespec="microseconds")


def _parse(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _now_like(reference: datetime) -> datetime:
    return datetime.now(reference.tzinfo) if reference.tzinfo else datetime.now()


@dataclass
class _QvdTrack:
    qvd_name: str
    size_bytes: int
    same_count: int
    is_stable: bool


class EventEngine:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        stable_after_measures: int = 3,
        min_delta_bytes: int = 1,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if stable_after_measures < 2:
            raise ValueError("stable_after_measures doit être >= 2")
        self._conn = conn
        self._stable_after = stable_after_measures
        self._min_delta = max(0, min_delta_bytes)
        self._clock = clock
        self._lock = threading.RLock()
        self._qvd: dict[str, _QvdTrack] = {}
        self._listeners: list[Callable[[ReloadEvent], None]] = []
        self._committed: list[ReloadEvent] = []
        self._reload_qvd_cache()

    # --------------------------------------------------------------- abonnés

    def add_listener(self, callback: Callable[[ReloadEvent], None]) -> None:
        """Abonne un composant aux événements PERSISTÉS (seq renseigné).

        Appelé après validation de la transaction SQLite, jamais avant : un
        abonné ne voit donc que des événements réellement enregistrés. Le
        moteur ignore tout de ses abonnés (notifications, etc.) ; une exception
        levée par un abonné est journalisée et n'interrompt jamais l'ingestion.
        """
        self._listeners.append(callback)

    def _flush_listeners(self) -> None:
        pending, self._committed = self._committed, []
        for event in pending:
            for callback in self._listeners:
                try:
                    callback(event)
                except Exception:  # noqa: BLE001 - un abonné ne doit rien casser
                    log.exception("listener_failed", extra={"seq": event.seq})

    # ------------------------------------------------------------------ ingest

    def ingest(self, event: ReloadEvent) -> IngestResult:
        """Enregistre un événement. Les doublons (même event_key) sont ignorés."""
        with self._lock:
            try:
                result = self._ingest_locked(event)
            except Exception:
                self._committed = []  # transaction annulée : rien à diffuser
                raise
            self._flush_listeners()
            return result

    def _ingest_locked(self, event: ReloadEvent) -> IngestResult:
        with self._conn:
            seq = self._insert_event(event)
            if seq is None:
                log.info("duplicate_event", extra={"reload_id": event.reload_id,
                                                   "event_type": event.event_type.value})
                return IngestResult(accepted=False, seq=None, reason="duplicate")

            measure = None
            if event.event_type == EventType.QVD_WRITE_START:
                # Un STORE réécrit le fichier depuis 0 octet : le prochain
                # delta se calcule depuis 0, et une nouvelle stabilisation
                # (nouveau QVD_STABLE) devient possible.
                self._qvd.pop(event.qvd_path or event.qvd, None)
            elif event.event_type == EventType.QVD_SIZE_CHANGE:
                measure = self._record_measure_locked(
                    timestamp=event.timestamp,
                    qvd_name=event.qvd,  # validé non vide par le modèle
                    path=event.qvd_path or event.qvd,
                    size_bytes=event.qvd_size_bytes,
                    reload_id=event.reload_id,
                    source=event.source.value,
                )
            return IngestResult(accepted=True, seq=seq, reason="inserted",
                                measure=measure)

    def _insert_event(self, event: ReloadEvent) -> Optional[int]:
        """Insère l'événement et recalcule le reload. Retourne seq, ou None si doublon."""
        self._ensure_reload(event)
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO events (
                   event_key, reload_id, timestamp, source, app_id, app_name,
                   event_type, status, section, table_name, rows,
                   qvd_name, qvd_path, qvd_size_bytes, message, extra_json, platform)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event.event_key, event.reload_id, _ts(event.timestamp),
                event.source.value, event.app_id, event.app_name,
                event.event_type.value, event.status.value,
                event.section, event.table, event.rows,
                event.qvd, event.qvd_path, event.qvd_size_bytes,
                event.message, json.dumps(event.extra, ensure_ascii=False),
                event.platform.value,
            ),
        )
        if cur.rowcount == 0:
            return None
        self._refresh_reload(event.reload_id)
        self._committed.append(event.model_copy(update={"seq": cur.lastrowid}))
        return cur.lastrowid

    def _ensure_reload(self, event: ReloadEvent) -> None:
        """Crée la ligne du reload au premier événement reçu, quel qu'il soit.

        Normalement c'est RELOAD_START ; si un autre événement arrive avant
        (ordre perturbé), la ligne est créée quand même puis corrigée par
        `_refresh_reload` dès que RELOAD_START est reçu.
        """
        self._conn.execute(
            """INSERT OR IGNORE INTO reloads
                   (reload_id, app_id, app_name, source, status, started_at, platform)
               VALUES (?,?,?,?,?,?,?)""",
            (event.reload_id, event.app_id, event.app_name, event.source.value,
             EventStatus.RUNNING.value, _ts(event.timestamp), event.platform.value),
        )

    def _refresh_reload(self, reload_id: str) -> None:
        """Recalcule tous les agrégats du reload depuis la table events."""
        agg = self._conn.execute(
            """SELECT
                 MIN(CASE WHEN event_type = 'RELOAD_START' THEN timestamp END) AS start_ts,
                 MIN(timestamp)                                                 AS first_ts,
                 MAX(CASE WHEN event_type = 'RELOAD_END'   THEN timestamp END) AS end_ts,
                 SUM(event_type = 'WARNING')                                    AS warnings,
                 SUM(event_type = 'ERROR')                                      AS errors,
                 COALESCE(SUM(CASE WHEN event_type = 'TABLE_END' THEN rows END), 0) AS total_rows
               FROM events WHERE reload_id = ?""",
            (reload_id,),
        ).fetchone()

        end_row = self._conn.execute(
            """SELECT status FROM events
               WHERE reload_id = ? AND event_type = 'RELOAD_END'
               ORDER BY timestamp DESC, seq DESC LIMIT 1""",
            (reload_id,),
        ).fetchone()
        name_row = self._conn.execute(
            "SELECT app_name FROM events WHERE reload_id = ? ORDER BY seq DESC LIMIT 1",
            (reload_id,),
        ).fetchone()

        started = _parse(agg["start_ts"] or agg["first_ts"])
        ended = _parse(agg["end_ts"])
        duration_ms = None
        if ended is not None:
            duration_ms = max(0, int((ended - started).total_seconds() * 1000))

        status = derive_reload_status(
            ended=ended is not None,
            end_status=EventStatus(end_row["status"]) if end_row else None,
            warnings_count=agg["warnings"] or 0,
            errors_count=agg["errors"] or 0,
        )
        self._conn.execute(
            """UPDATE reloads SET app_name = ?, status = ?, started_at = ?, ended_at = ?,
                      duration_ms = ?, total_rows = ?, warnings_count = ?, errors_count = ?
               WHERE reload_id = ?""",
            (name_row["app_name"], status.value, _ts(started),
             _ts(ended) if ended else None, duration_ms, agg["total_rows"],
             agg["warnings"] or 0, agg["errors"] or 0, reload_id),
        )

    # ------------------------------------------------------------ mesures QVD

    def record_qvd_measure(
        self,
        *,
        timestamp: datetime,
        qvd_name: str,
        path: str,
        size_bytes: int,
        reload_id: Optional[str] = None,
        source: str = "qvd_watcher",
    ) -> Optional[QvdMeasure]:
        """Enregistre une observation de taille. Retourne la mesure stockée,
        ou None si l'observation n'apporte rien (taille identique, pas encore stable).

        Règles :
        - variation significative (|delta| >= min_delta_bytes, delta != 0)
          -> mesure stockée, is_stable = 0, compteur remis à 1 ;
        - taille identique -> compteur + 1 ; quand il atteint
          stable_after_measures, UNE mesure is_stable = 1 est stockée ;
        - taille identique alors que déjà stable -> rien n'est stocké.
        """
        if size_bytes < 0:
            raise ValueError("size_bytes ne peut pas être négatif")
        with self._lock:
            try:
                with self._conn:
                    measure = self._record_measure_locked(
                        timestamp=timestamp, qvd_name=qvd_name, path=path,
                        size_bytes=size_bytes, reload_id=reload_id, source=source,
                    )
            except Exception:
                self._committed = []
                raise
            self._flush_listeners()
            return measure

    def _record_measure_locked(self, *, timestamp, qvd_name, path, size_bytes,
                               reload_id, source) -> Optional[QvdMeasure]:
        if reload_id is not None:
            exists = self._conn.execute(
                "SELECT 1 FROM reloads WHERE reload_id = ?", (reload_id,)).fetchone()
            if not exists:
                log.warning("measure_unknown_reload", extra={"reload_id": reload_id})
                reload_id = None

        prev = self._qvd.get(path)
        if prev is None:
            self._qvd[path] = _QvdTrack(qvd_name, size_bytes, 1, False)
            return self._insert_measure(timestamp, qvd_name, path, size_bytes,
                                        size_bytes, False, reload_id, source)

        delta = size_bytes - prev.size_bytes
        if delta != 0 and abs(delta) >= self._min_delta:
            self._qvd[path] = _QvdTrack(qvd_name, size_bytes, 1, False)
            return self._insert_measure(timestamp, qvd_name, path, size_bytes,
                                        delta, False, reload_id, source)

        # Taille identique (ou variation non significative).
        prev.same_count += 1
        if not prev.is_stable and prev.same_count >= self._stable_after:
            prev.is_stable = True
            measure = self._insert_measure(timestamp, qvd_name, path, prev.size_bytes,
                                           0, True, reload_id, source)
            stable_event = self._emit_qvd_stable(measure, source)
            return replace(measure, stable_event=stable_event)
        return None

    def _emit_qvd_stable(self, m: QvdMeasure, source: str) -> Optional[ReloadEvent]:
        """Persiste QVD_STABLE dans la MÊME transaction que la mesure stable.

        Une seule fois par épisode : la mesure stable n'est elle-même écrite
        qu'une fois tant que la taille ne change pas ; un QVD_WRITE_START ou
        une nouvelle variation de taille ré-arme la détection.
        Sans reload connu, aucun événement n'est émis (rien à quoi le rattacher).
        """
        if m.reload_id is None:
            return None
        r = self._conn.execute(
            "SELECT app_id, app_name, platform FROM reloads WHERE reload_id = ?",
            (m.reload_id,)).fetchone()
        event = ReloadEvent(
            reload_id=m.reload_id, timestamp=m.timestamp, source=EventSource(source),
            platform=Platform(r["platform"]),
            app_id=r["app_id"], app_name=r["app_name"], event_type=EventType.QVD_STABLE,
            status=EventStatus.SUCCESS, qvd=m.qvd_name, qvd_path=m.path,
            qvd_size_bytes=m.size_bytes, extra={"measure_id": m.id},
        )
        seq = self._insert_event(event)
        return event.model_copy(update={"seq": seq}) if seq is not None else None

    def _insert_measure(self, timestamp, qvd_name, path, size_bytes, delta,
                        is_stable, reload_id, source) -> QvdMeasure:
        cur = self._conn.execute(
            """INSERT INTO qvd_measures
                   (timestamp, reload_id, qvd_name, path, size_bytes,
                    delta_bytes, is_stable, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (_ts(timestamp), reload_id, qvd_name, path, size_bytes, delta,
             int(is_stable), source),
        )
        return QvdMeasure(timestamp=timestamp, qvd_name=qvd_name, path=path,
                          size_bytes=size_bytes, delta_bytes=delta,
                          is_stable=is_stable, reload_id=reload_id, id=cur.lastrowid)

    def _reload_qvd_cache(self) -> None:
        """Reconstruit le suivi de stabilité depuis la dernière mesure de chaque fichier."""
        rows = self._conn.execute(
            """SELECT m.path, m.qvd_name, m.size_bytes, m.is_stable
               FROM qvd_measures m
               JOIN (SELECT path, MAX(id) AS max_id FROM qvd_measures GROUP BY path) last
                 ON last.max_id = m.id"""
        ).fetchall()
        for r in rows:
            stable = bool(r["is_stable"])
            self._qvd[r["path"]] = _QvdTrack(
                r["qvd_name"], r["size_bytes"],
                self._stable_after if stable else 1, stable)

    # ----------------------------------------------------------- état courant

    def _now(self, reference: datetime) -> datetime:
        return self._clock() if self._clock else _now_like(reference)

    def get_reload_state(self, reload_id: str,
                         now: Optional[datetime] = None) -> Optional[ReloadState]:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM reloads WHERE reload_id = ?", (reload_id,)).fetchone()
            if r is None:
                return None
            events = self._conn.execute(
                "SELECT * FROM events WHERE reload_id = ? ORDER BY timestamp, seq",
                (reload_id,),
            ).fetchall()

            step = section = table = qvd = message = None
            qvd_size_event = None
            qvd_writing = False
            for e in events:
                et = EventType(e["event_type"])
                if et in _STEP_EVENTS:
                    target = e["table_name"] or e["qvd_name"] or e["section"]
                    step = f"{et.value} {target}" if target else et.value
                if e["section"]:
                    section = e["section"]
                if e["table_name"]:
                    table = e["table_name"]
                if e["qvd_name"]:
                    qvd = e["qvd_name"]
                    if et in (EventType.QVD_WRITE_START, EventType.QVD_SIZE_CHANGE):
                        qvd_writing = True
                    elif et in (EventType.QVD_WRITE_END, EventType.QVD_STABLE):
                        qvd_writing = False
                    if e["qvd_size_bytes"] is not None:
                        qvd_size_event = e["qvd_size_bytes"]
                if et in (EventType.WARNING, EventType.ERROR):
                    message = e["message"]

            rows = None
            if table is not None:
                for e in events:
                    if e["table_name"] == table and e["rows"] is not None:
                        rows = e["rows"]

            qvd_size = qvd_stable = None
            if qvd is not None:
                m = self._conn.execute(
                    """SELECT size_bytes, is_stable FROM qvd_measures
                       WHERE reload_id = ? AND qvd_name = ?
                       ORDER BY timestamp DESC, id DESC LIMIT 1""",
                    (reload_id, qvd),
                ).fetchone()
                if m is not None:
                    qvd_size, qvd_stable = m["size_bytes"], bool(m["is_stable"])
                else:
                    qvd_size = qvd_size_event

            started = _parse(r["started_at"])
            ended = _parse(r["ended_at"])
            if ended is not None:
                elapsed_ms = r["duration_ms"] or 0
            else:
                current = now or self._now(started)
                elapsed_ms = max(0, int((current - started).total_seconds() * 1000))

            return ReloadState(
                reload_id=r["reload_id"], app_id=r["app_id"], app_name=r["app_name"],
                source=r["source"], platform=r["platform"], status=EventStatus(r["status"]),
                is_running=ended is None, started_at=started, ended_at=ended,
                elapsed_ms=elapsed_ms, current_step=step, current_section=section,
                current_table=table, current_rows=rows, current_qvd=qvd,
                current_qvd_size_bytes=qvd_size, current_qvd_is_stable=qvd_stable,
                current_qvd_writing=qvd_writing and ended is None,
                total_rows=r["total_rows"], warnings_count=r["warnings_count"],
                errors_count=r["errors_count"], last_message=message,
                last_seq=events[-1]["seq"] if events else None,
            )

    def get_reload_platform(self, reload_id: str) -> Optional[Platform]:
        with self._lock:
            row = self._conn.execute("SELECT platform FROM reloads WHERE reload_id = ?",
                                     (reload_id,)).fetchone()
            return Platform(row["platform"]) if row else None

    def get_active_states(self, now: Optional[datetime] = None) -> list[ReloadState]:
        """Reloads non terminés, du plus récent au plus ancien."""
        with self._lock:
            ids = [row[0] for row in self._conn.execute(
                """SELECT reload_id FROM reloads WHERE ended_at IS NULL
                   ORDER BY started_at DESC, reload_id""")]
            return [self.get_reload_state(i, now) for i in ids]

    def get_current_state(self, app_id: Optional[str] = None,
                          now: Optional[datetime] = None) -> Optional[ReloadState]:
        """Reload « courant » : le plus récent en cours, sinon le dernier démarré.

        `app_id` restreint la recherche à une application.
        """
        with self._lock:
            where, params = ("WHERE app_id = ?", (app_id,)) if app_id else ("", ())
            row = self._conn.execute(
                f"""SELECT reload_id FROM reloads {where}
                    ORDER BY (ended_at IS NULL) DESC, started_at DESC LIMIT 1""",
                params,
            ).fetchone()
            return self.get_reload_state(row[0], now) if row else None
