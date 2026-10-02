"""Déclenchement des notifications quand un reload se termine en erreur.

Règles
- Une notification part UNIQUEMENT pour un reload terminé (`ended_at` connu)
  dont le statut final est ERROR. Les événements ERROR intermédiaires ne
  déclenchent rien par eux-mêmes ; ils servent seulement à re-vérifier un
  reload déjà terminé (cas d'un événement ERROR arrivé en retard).
- Unicité : avant tout envoi, une ligne est réservée dans `notification_log`
  (UNIQUE reload_id, canal, destinataire). Si la ligne existe déjà, rien
  n'est envoyé : un reload ne produit jamais deux fois la même notification,
  même après redémarrage ou réception en double.
- Échec : au plus `max_attempts` tentatives (3) avec une courte attente ;
  l'échec est journalisé et conservé dans `notification_log`. Il ne modifie
  jamais le statut du reload et ne lève jamais d'exception vers l'appelant.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..engine import EventEngine
from ..models import EventType, ReloadEvent
from ..storage.db import open_db
from ..storage.rows import row_to_event
from .base import (DeliveryError, Message, NotificationResult, Notifier,
                   PermanentDeliveryError)
from .messages import build_reload_error_message

log = logging.getLogger(__name__)

_TRIGGERS = frozenset({EventType.RELOAD_END, EventType.ERROR})


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class NotificationService:
    def __init__(
        self,
        db_path: str | Path,
        notifiers: Iterable[Notifier],
        *,
        max_attempts: int = 3,
        retry_delay_seconds: float = 2.0,
        catch_up_minutes: int = 60,
        stable_after_measures: int = 3,
        clock: Callable[[], datetime] = datetime.now,
        sleep: Callable[[float], None] = time.sleep,
        on_result: Optional[Callable[[NotificationResult], None]] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.notifiers = [n for n in notifiers if n.enabled]
        self.max_attempts = max(1, min(3, max_attempts))
        self.retry_delay = retry_delay_seconds
        self.catch_up_minutes = catch_up_minutes
        self._stable_after = stable_after_measures
        self._clock = clock
        self._sleep = sleep
        self._on_result = on_result

    # ------------------------------------------------------------ entrées

    def on_event(self, event: ReloadEvent) -> None:
        """Abonné du moteur (EventEngine.add_listener)."""
        if event.event_type in _TRIGGERS:
            self.process_reload(event.reload_id)

    def process_reload(self, reload_id: str) -> list[NotificationResult]:
        """Notifie le reload s'il est terminé en ERROR et pas encore notifié. Ne lève jamais."""
        if not self.notifiers:
            return []
        try:
            conn = open_db(self.db_path)
        except Exception:
            log.exception("notification_db_unavailable", extra={"reload_id": reload_id})
            return []
        try:
            return self._process(conn, reload_id)
        except Exception:
            log.exception("notification_failed_unexpectedly", extra={"reload_id": reload_id})
            return []
        finally:
            conn.close()

    def catch_up(self) -> list[NotificationResult]:
        """Rattrapage : reloads en erreur terminés récemment et jamais notifiés.

        Destiné au démarrage d'un collecteur (arrêt survenu juste après la fin
        d'un reload en erreur). Limité à `catch_up_minutes` pour ne pas
        notifier un historique ancien.
        """
        if not self.notifiers or self.catch_up_minutes <= 0:
            return []
        since = _ts(self._clock() - timedelta(minutes=self.catch_up_minutes))
        conn = open_db(self.db_path)
        try:
            ids = [r[0] for r in conn.execute(
                """SELECT reload_id FROM reloads
                   WHERE status = 'ERROR' AND ended_at IS NOT NULL AND ended_at >= ?
                   ORDER BY ended_at""", (since,))]
        finally:
            conn.close()
        results: list[NotificationResult] = []
        for rid in ids:
            results.extend(self.process_reload(rid))
        return results

    # ----------------------------------------------------------- logique

    def _process(self, conn: sqlite3.Connection, reload_id: str) -> list[NotificationResult]:
        row = conn.execute("SELECT status, ended_at FROM reloads WHERE reload_id = ?",
                           (reload_id,)).fetchone()
        if row is None or row["ended_at"] is None or row["status"] != "ERROR":
            return []

        message = self._build_message(conn, reload_id)
        results = []
        for notifier in self.notifiers:
            for recipient in notifier.recipients():
                if not self._claim(conn, reload_id, notifier.channel, recipient, message):
                    continue  # déjà traité : jamais de doublon
                result = self._deliver(conn, reload_id, notifier, recipient, message)
                results.append(result)
                if self._on_result:
                    try:
                        self._on_result(result)
                    except Exception:  # noqa: BLE001
                        log.exception("notification_callback_failed")
        return results

    def _build_message(self, conn: sqlite3.Connection, reload_id: str) -> Message:
        engine = EventEngine(conn, stable_after_measures=self._stable_after)
        state = engine.get_reload_state(reload_id)
        err = conn.execute(
            """SELECT * FROM events WHERE reload_id = ? AND event_type = 'ERROR'
               ORDER BY timestamp, seq LIMIT 1""", (reload_id,)).fetchone()
        end = conn.execute(
            """SELECT * FROM events WHERE reload_id = ? AND event_type = 'RELOAD_END'
               ORDER BY timestamp DESC, seq DESC LIMIT 1""", (reload_id,)).fetchone()
        return build_reload_error_message(
            state, row_to_event(err) if err else None, row_to_event(end) if end else None)

    def _claim(self, conn, reload_id, channel, recipient, message: Message) -> bool:
        with conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO notification_log
                       (reload_id, channel, recipient, status, attempts, subject, created_at)
                   VALUES (?, ?, ?, 'PENDING', 0, ?, ?)""",
                (reload_id, channel, recipient, message.subject, _ts(self._clock())))
        return cur.rowcount == 1

    def _finish(self, conn, reload_id, channel, recipient, *, status, attempts,
                sent_at=None, error=None) -> None:
        with conn:
            conn.execute(
                """UPDATE notification_log SET status = ?, attempts = ?, sent_at = ?,
                          error_message = ?
                   WHERE reload_id = ? AND channel = ? AND recipient = ?""",
                (status, attempts, sent_at, error, reload_id, channel, recipient))

    def _deliver(self, conn, reload_id, notifier: Notifier, recipient: str,
                 message: Message) -> NotificationResult:
        ch = notifier.channel
        if notifier.dry_run:
            log.warning("EMAIL DRY RUN — rien n'est envoyé\nÀ : %s\nSujet : %s\n\n%s",
                        recipient, message.subject, message.body)
            self._finish(conn, reload_id, ch, recipient, status="DRY_RUN", attempts=0)
            return NotificationResult(reload_id, ch, recipient, "DRY_RUN", 0,
                                      message.subject, message=message)

        error: Optional[str] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                notifier.deliver(message, recipient)
                self._finish(conn, reload_id, ch, recipient, status="SENT", attempts=attempt,
                             sent_at=_ts(self._clock()))
                log.info("notification_sent", extra={"reload_id": reload_id,
                                                     "channel": ch, "attempt": attempt})
                return NotificationResult(reload_id, ch, recipient, "SENT", attempt,
                                          message.subject, message=message)
            except PermanentDeliveryError as exc:
                error = str(exc)
                break  # inutile de réessayer (authentification, configuration)
            except DeliveryError as exc:
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 - un bug d'envoi ne doit rien casser
                error = f"{type(exc).__name__}: {exc}"
            if attempt < self.max_attempts:
                self._sleep(self.retry_delay)

        error = (error or "échec inconnu")[:500]
        log.error("notification_failed", extra={"reload_id": reload_id, "channel": ch,
                                                "error": error})
        self._finish(conn, reload_id, ch, recipient, status="FAILED",
                     attempts=attempt, error=error)
        return NotificationResult(reload_id, ch, recipient, "FAILED", attempt,
                                  message.subject, error_message=error, message=message)
