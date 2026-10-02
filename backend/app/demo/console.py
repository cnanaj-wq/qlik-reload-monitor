"""Affichage console de la chronologie d'un reload (format du cahier des charges)."""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional, TextIO

from ..engine import IngestResult, QvdMeasure, ReloadState
from ..models import EventStatus, EventType, ReloadEvent

from ..formatting import MIB, fmt_duration, fmt_int, fmt_size  # noqa: F401


class ConsoleReporter:
    def __init__(self, stream: Optional[TextIO] = None) -> None:
        self.out = stream or sys.stdout
        self._starts: dict[str, datetime] = {}
        self._sizes: dict[str, int] = {}

    def _line(self, ts: datetime, text: str) -> None:
        print(f"{ts:%d/%m/%Y %H:%M:%S}  {text}", file=self.out, flush=True)

    def on_event(self, e: ReloadEvent, result: IngestResult) -> None:
        if not result.accepted:
            return
        et, ts = e.event_type, e.timestamp
        if et == EventType.RELOAD_START:
            self._starts["reload"] = ts
            self._line(ts, f"🟢 Reload démarré — Application {e.app_name}")
        elif et == EventType.SECTION_START:
            self._starts[f"s:{e.section}"] = ts
            self._line(ts, f"▶ Section : {e.section}")
        elif et == EventType.SECTION_END:
            d = self._since(f"s:{e.section}", ts)
            icon = "🟠" if e.status == EventStatus.WARNING else "✅"
            self._line(ts, f"{icon} Section terminée — {e.section} — {d}")
        elif et == EventType.TABLE_START:
            self._line(ts, f"⏳ {e.table} en cours")
        elif et == EventType.TABLE_PROGRESS:
            self._line(ts, f"   {fmt_int(e.rows)} lignes")
        elif et == EventType.TABLE_END:
            self._line(ts, f"✅ {e.table} — {fmt_int(e.rows)} lignes")
        elif et == EventType.QVD_WRITE_START:
            self._line(ts, f"💾 Écriture {e.qvd}")
        elif et == EventType.QVD_SIZE_CHANGE:
            m = result.measure
            self._sizes[e.qvd] = e.qvd_size_bytes
            delta = f"  (+{fmt_size(m.delta_bytes)})" if m and m.delta_bytes > 0 else ""
            self._line(ts, f"   {fmt_size(e.qvd_size_bytes)}{delta}")
        elif et == EventType.QVD_WRITE_END:
            size = self._sizes.get(e.qvd)
            self._line(ts, f"✅ {e.qvd}" + (f" — {fmt_size(size)}" if size is not None else ""))
        elif et == EventType.QVD_STABLE:
            self._line(ts, f"   ✔ {e.qvd} stabilisé — {fmt_size(e.qvd_size_bytes)}")
        elif et == EventType.WARNING:
            self._line(ts, f"🟠 Warning : {e.message}")
        elif et == EventType.ERROR:
            self._line(ts, f"🔴 Erreur : {e.message}")
        elif et == EventType.RELOAD_END:
            label = {EventStatus.ERROR: "🔴 Reload en erreur",
                     EventStatus.WARNING: "🟠 Reload terminé avec warnings"}.get(
                         e.status, "✅ Reload terminé")
            self._line(ts, label)

    def on_measure(self, m: QvdMeasure) -> None:
        # Avec un reload connu, la stabilisation arrive comme événement QVD_STABLE.
        if m.is_stable and m.stable_event is None:
            self._line(m.timestamp, f"   ✔ {m.qvd_name} stabilisé — {fmt_size(m.size_bytes)}")

    def on_notification(self, r) -> None:
        """Résultat d'une notification (NotificationResult)."""
        label = {"SENT": f"✅ envoyée à {r.recipient}",
                 "DRY_RUN": f"🧪 DRY RUN (non envoyée) → {r.recipient}",
                 "FAILED": f"🔴 échec après {r.attempts} tentative(s) → {r.recipient} : "
                           f"{r.error_message}"}.get(r.status, r.status)
        print(f"\n📧 Notification email : {label}", file=self.out, flush=True)
        if r.status == "DRY_RUN" and r.message is not None:
            print("─" * 72, file=self.out)
            print(f"Sujet : {r.message.subject}", file=self.out)
            print("─" * 72, file=self.out)
            print(r.message.body, file=self.out)
            print("─" * 72, file=self.out, flush=True)

    def summary(self, st: ReloadState, db_path: str) -> None:
        p = lambda s="": print(s, file=self.out)  # noqa: E731
        p()
        p(f"Durée totale : {fmt_duration(st.elapsed_ms)}")
        p(f"Statut final : {st.status.value}  |  lignes : {fmt_int(st.total_rows)}"
          f"  |  warnings : {st.warnings_count}  |  erreurs : {st.errors_count}")
        p(f"reload_id    : {st.reload_id}")
        p(f"Base SQLite  : {db_path}")

    def _since(self, key: str, ts: datetime) -> str:
        start = self._starts.get(key)
        return fmt_duration(int((ts - start).total_seconds() * 1000)) if start else "?"
