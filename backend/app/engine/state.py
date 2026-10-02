"""Structures d'état exposées par le moteur et règle de statut d'un reload.

Tout est dérivé des données stockées dans SQLite : aucun état n'est
conservé uniquement en mémoire, ce qui permet de tout reconstruire après
un redémarrage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional

from ..models.enums import EventStatus


def derive_reload_status(
    *,
    ended: bool,
    end_status: Optional[EventStatus],
    warnings_count: int,
    errors_count: int,
) -> EventStatus:
    """Règle déterministe et explicable du statut d'un reload.

    En cours :
      - ERROR dès qu'une erreur a été reçue (statut « collant ») ;
      - sinon RUNNING (les warnings restent visibles via leur compteur).
    Terminé :
      - ERROR si au moins une erreur, ou si RELOAD_END porte ERROR ;
      - WARNING si au moins un warning, ou si RELOAD_END porte WARNING ;
      - SUCCESS sinon.
    Un statut ne peut donc jamais être « adouci » par un événement tardif.
    """
    if errors_count > 0 or end_status == EventStatus.ERROR:
        return EventStatus.ERROR
    if not ended:
        return EventStatus.RUNNING
    if warnings_count > 0 or end_status == EventStatus.WARNING:
        return EventStatus.WARNING
    return EventStatus.SUCCESS


@dataclass(frozen=True)
class QvdMeasure:
    timestamp: datetime
    qvd_name: str
    path: str
    size_bytes: int
    delta_bytes: int
    is_stable: bool
    reload_id: Optional[str]
    id: Optional[int] = None
    # Événement QVD_STABLE persisté avec cette mesure (avec son seq), le cas échéant.
    stable_event: Optional[Any] = None


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    seq: Optional[int]
    reason: str  # "inserted" | "duplicate"
    # Mesure QVD stockée suite à un QVD_SIZE_CHANGE (None sinon).
    measure: Optional[QvdMeasure] = None


@dataclass(frozen=True)
class ReloadState:
    reload_id: str
    app_id: str
    app_name: str
    source: str
    platform: str
    status: EventStatus
    is_running: bool
    started_at: datetime
    ended_at: Optional[datetime]
    elapsed_ms: int
    current_step: Optional[str]
    current_section: Optional[str]
    current_table: Optional[str]
    current_rows: Optional[int]
    current_qvd: Optional[str]
    current_qvd_size_bytes: Optional[int]
    current_qvd_is_stable: Optional[bool]
    # True entre QVD_WRITE_START et QVD_WRITE_END du dernier QVD connu.
    current_qvd_writing: bool
    total_rows: int
    warnings_count: int
    errors_count: int
    last_message: Optional[str]
    last_seq: Optional[int]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["started_at"] = self.started_at.isoformat()
        d["ended_at"] = self.ended_at.isoformat() if self.ended_at else None
        return d
