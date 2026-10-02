"""Surveillance des fichiers QVD par interrogation périodique (polling).

STRICTEMENT EN LECTURE SEULE : ce module n'utilise que `os.scandir` et
`os.stat`. Il n'ouvre jamais un QVD, ne le lit pas, ne le modifie pas.

Pourquoi `os.stat(chemin)` plutôt que `DirEntry.stat()` : sous Windows, la
taille mise en cache dans l'entrée de répertoire peut être en retard pour un
fichier en cours d'écriture ; `os.stat` interroge le fichier lui-même.

Un appel à `poll_once()` = un passage sur tous les dossiers. La boucle
périodique (thread, intervalle) sera ajoutée avec l'API ; ici, l'appelant
(simulateur ou test) décide quand interroger, ce qui reste déterministe.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..engine import EventEngine, IngestResult, QvdMeasure
from ..models import EventSource, EventStatus, EventType, Platform, ReloadEvent

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReloadContext:
    """Reload auquel rattacher les fichiers observés (connu du producteur)."""

    reload_id: str
    app_id: str
    app_name: str
    # Si absente, reprise de la plateforme du reload déjà enregistré.
    platform: Optional[Platform] = None


@dataclass(frozen=True)
class Observation:
    path: str
    qvd_name: str
    size_bytes: int
    previous_size: Optional[int]
    changed: bool
    measure: Optional[QvdMeasure]


class QvdWatcher:
    def __init__(
        self,
        engine: EventEngine,
        directories: Iterable[str | Path],
        *,
        extensions: Iterable[str] = (".qvd",),
        clock: Optional[Callable[[], datetime]] = None,
        on_event: Optional[Callable[[ReloadEvent, IngestResult], None]] = None,
        on_measure: Optional[Callable[[QvdMeasure], None]] = None,
    ) -> None:
        self._engine = engine
        self._dirs = [Path(d) for d in directories]
        self._ext = tuple(e.lower() for e in extensions)
        self._clock = clock or datetime.now
        self._on_event = on_event
        self._on_measure = on_measure
        self._last: dict[str, int] = {}

    def poll_once(self, context: Optional[ReloadContext] = None) -> list[Observation]:
        """Mesure la taille de chaque QVD des dossiers surveillés.

        - taille nouvelle ou différente + contexte connu -> événement
          QVD_SIZE_CHANGE (le moteur enregistre la mesure et le delta) ;
        - taille identique (ou pas de contexte) -> simple observation
          transmise au moteur, qui décide de la stabilisation.
        Un dossier ou un fichier inaccessible est journalisé et ignoré.
        """
        now = self._clock()
        observations: list[Observation] = []
        seen: set[str] = set()
        scanned: list[Path] = []

        for directory in self._dirs:
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                log.warning("qvd_dir_unreachable", extra={"dir": str(directory),
                                                          "error": repr(exc)})
                continue
            scanned.append(directory)

            for entry in entries:
                if not entry.name.lower().endswith(self._ext):
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    size = os.stat(entry.path).st_size
                except FileNotFoundError:
                    continue  # supprimé entre le listing et la mesure
                except OSError as exc:  # verrou, droits...
                    log.warning("qvd_stat_failed", extra={"path": entry.path,
                                                          "error": repr(exc)})
                    continue

                path = str(Path(entry.path))
                seen.add(path)
                obs = self._observe(now, path, entry.name, size, context)
                observations.append(obs)

        self._forget_missing(seen, scanned)
        return observations

    def _observe(self, now, path, name, size, context) -> Observation:
        previous = self._last.get(path)
        changed = previous is None or size != previous
        measure: Optional[QvdMeasure] = None

        platform = None
        if context is not None:
            platform = context.platform or self._engine.get_reload_platform(context.reload_id)

        if changed and context is not None and platform is not None:
            event = ReloadEvent(
                reload_id=context.reload_id, timestamp=now,
                source=EventSource.QVD_WATCHER, platform=platform, app_id=context.app_id,
                app_name=context.app_name, event_type=EventType.QVD_SIZE_CHANGE,
                status=EventStatus.RUNNING, qvd=name, qvd_path=path,
                qvd_size_bytes=size,
            )
            result = self._engine.ingest(event)
            measure = result.measure
            if self._on_event:
                self._on_event(event, result)
        else:
            measure = self._engine.record_qvd_measure(
                timestamp=now, qvd_name=name, path=path, size_bytes=size,
                reload_id=context.reload_id if context else None,
                source=EventSource.QVD_WATCHER.value,
            )
            if measure is not None and self._on_measure:
                self._on_measure(measure)

        # Stabilisation constatée : le moteur a persisté QVD_STABLE (avec son seq).
        if measure is not None and measure.stable_event is not None and self._on_event:
            ev = measure.stable_event
            self._on_event(ev, IngestResult(accepted=True, seq=ev.seq, reason="inserted",
                                            measure=measure))

        self._last[path] = size
        return Observation(path=path, qvd_name=name, size_bytes=size,
                           previous_size=previous, changed=changed, measure=measure)

    def _forget_missing(self, seen: set[str], scanned: list[Path]) -> None:
        """Oublie les fichiers disparus des dossiers effectivement parcourus."""
        scanned_str = {str(d) for d in scanned}
        for path in list(self._last):
            if path not in seen and str(Path(path).parent) in scanned_str:
                log.info("qvd_disappeared", extra={"path": path})
                del self._last[path]
