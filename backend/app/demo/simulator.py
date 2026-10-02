"""Simulateur de reload Qlik (mode DEMO).

Il joue le rôle du « log Qlik » : il émet les événements de cycle de vie
(sections, tables, STORE...) avec la source `demo`. Pour les QVD, il écrit
réellement des octets factices sur disque, par blocs, et laisse le
`QvdWatcher` CONSTATER la taille via os.stat : les tailles et deltas stockés
viennent donc du système de fichiers, comme en mode LIVE.

Aucune donnée réelle : les fichiers ne contiennent que des octets nuls et
ne sont écrits que dans le dossier de démo préparé par `prepare_demo_dir`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Protocol

from ..engine import EventEngine, IngestResult
from ..models import EventSource, EventStatus, EventType, Platform, ReloadEvent
from ..watcher import QvdWatcher, ReloadContext
from .scenario import Scenario, SectionSpec, TableSpec

_BLOCK = b"\x00" * (1024 * 1024)  # tampon réutilisé : 1 Mio d'octets nuls
MIB = 1024 * 1024


class Clock(Protocol):
    def now(self): ...
    def sleep(self, seconds: float) -> None: ...


class ReloadSimulator:
    def __init__(
        self,
        engine: EventEngine,
        scenario: Scenario,
        *,
        qvd_dir: str | Path,
        clock: Clock,
        watcher: QvdWatcher,
        polling_interval_seconds: float = 1.0,
        stable_after_measures: int = 3,
        size_scale: float = 1.0,
        on_event: Optional[Callable[[ReloadEvent, IngestResult], None]] = None,
    ) -> None:
        if size_scale <= 0:
            raise ValueError("size_scale doit être > 0")
        self.engine = engine
        self.scenario = scenario
        self.qvd_dir = Path(qvd_dir)
        self.clock = clock
        self.watcher = watcher
        self.poll_interval = polling_interval_seconds
        self.stable_after = stable_after_measures
        self.size_scale = size_scale  # réduit les tailles (tests)
        self.on_event = on_event
        self.ctx: Optional[ReloadContext] = None
        self._warnings = 0

    # ----------------------------------------------------------------- run

    def run(self) -> str:
        """Joue le scénario jusqu'au bout et retourne le reload_id."""
        sc = self.scenario
        start = self.clock.now()
        reload_id = self._new_reload_id(start)
        self.ctx = ReloadContext(reload_id, sc.app_id, sc.app_name, Platform.DEMO)
        self._warnings = 0

        self._emit(EventType.RELOAD_START, message=sc.description or None,
                   extra={"scenario": sc.name})
        failed = False
        for section in sc.sections:
            if not self._run_section(section):
                failed = True
                break

        if failed:
            self._emit(EventType.RELOAD_END, status=EventStatus.ERROR,
                       message="Le rechargement a échoué")
        else:
            status = EventStatus.WARNING if self._warnings else EventStatus.SUCCESS
            self._emit(EventType.RELOAD_END, status=status)
        return reload_id

    def _run_section(self, section: SectionSpec) -> bool:
        self._emit(EventType.SECTION_START, section=section.name)
        self.clock.sleep(section.duration_seconds)
        section_warnings = 0

        for table in section.tables:
            ok, n_warn = self._run_table(section, table)
            section_warnings += n_warn
            if not ok:
                return False  # Qlik s'arrête : pas de SECTION_END
            if table.store:
                self._store(section, table)

        for msg in section.warnings:
            self._warn(msg, section=section.name)
            section_warnings += 1

        self._emit(EventType.SECTION_END, section=section.name,
                   status=EventStatus.WARNING if section_warnings else EventStatus.SUCCESS)
        return True

    def _run_table(self, section: SectionSpec, table: TableSpec) -> tuple[bool, int]:
        s, t = section.name, table.name
        self._emit(EventType.TABLE_START, section=s, table=t)
        steps = table.progress_steps
        step_time = table.duration_seconds / steps
        rows = 0

        for i in range(1, steps + 1):
            if table.error is not None and i / steps > table.error.at_fraction:
                self._emit(EventType.ERROR, section=s, table=t, rows=rows,
                           status=EventStatus.ERROR, message=table.error.message)
                return False, 0
            self.clock.sleep(step_time)
            rows = round(table.rows * i / steps)
            if i < steps:
                self._emit(EventType.TABLE_PROGRESS, section=s, table=t, rows=rows)

        self._emit(EventType.TABLE_END, section=s, table=t, rows=table.rows,
                   status=EventStatus.SUCCESS)
        for msg in table.warnings:
            self._warn(msg, section=s, table=t)
        return True, len(table.warnings)

    # --------------------------------------------------------------- STORE

    def _store(self, section: SectionSpec, table: TableSpec) -> None:
        spec = table.store
        path = self.qvd_dir / spec.qvd
        common = dict(section=section.name, table=table.name, qvd=spec.qvd,
                      qvd_path=str(path))
        total = max(1, int(spec.size_mb * MIB * self.size_scale))
        chunk_sizes = [total // spec.chunks] * spec.chunks
        chunk_sizes[-1] += total - sum(chunk_sizes)

        self._emit(EventType.QVD_WRITE_START, **common)
        with open(path, "wb") as f:  # uniquement dans demo_data/qvd
            for size in chunk_sizes:
                self._write_zeros(f, size)
                f.flush()
                os.fsync(f.fileno())
                self.clock.sleep(spec.chunk_interval_seconds)
                self.watcher.poll_once(self.ctx)
        self._emit(EventType.QVD_WRITE_END, status=EventStatus.SUCCESS, **common)

        # Laisse le watcher constater la stabilisation (N mesures identiques).
        for _ in range(self.stable_after - 1):
            self.clock.sleep(self.poll_interval)
            self.watcher.poll_once(self.ctx)

    @staticmethod
    def _write_zeros(f, size: int) -> None:
        while size > 0:
            n = min(size, len(_BLOCK))
            f.write(_BLOCK[:n])
            size -= n

    # ------------------------------------------------------------ helpers

    def _warn(self, message: str, **kw) -> None:
        self._warnings += 1
        self._emit(EventType.WARNING, status=EventStatus.WARNING, message=message, **kw)

    def _emit(self, event_type: EventType, *, status: EventStatus = EventStatus.RUNNING,
              **fields) -> None:
        event = ReloadEvent(
            reload_id=self.ctx.reload_id, timestamp=self.clock.now(),
            source=EventSource.DEMO, app_id=self.ctx.app_id,
            app_name=self.ctx.app_name, event_type=event_type, status=status,
            **fields,
        )
        result = self.engine.ingest(event)
        if self.on_event:
            self.on_event(event, result)

    def _new_reload_id(self, start) -> str:
        base = f"{start:%Y%m%d-%H%M%S}-{self.scenario.app_id}-{self.scenario.name}"
        reload_id, n = base, 1
        while self.engine.get_reload_state(reload_id) is not None:
            n += 1
            reload_id = f"{base}-{n}"
        return reload_id
