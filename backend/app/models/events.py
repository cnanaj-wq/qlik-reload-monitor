"""Modèle générique d'événement de reload.

Un `ReloadEvent` est la seule structure échangée entre les producteurs
(simulateur, parser de logs, watcher QVD), le moteur, la base et l'API.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    QVD_EVENTS,
    SECTION_EVENTS,
    TABLE_EVENTS,
    EventSource,
    EventStatus,
    EventType,
    Platform,
)


class ReloadEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    # Identifiant monotone attribué par la base à l'insertion (None avant).
    seq: Optional[int] = None

    reload_id: str = Field(min_length=1)
    timestamp: datetime
    source: EventSource
    # Plateforme Qlik. Obligatoire, sauf pour la source `demo` où elle vaut
    # `demo` : aucune valeur par défaut ne doit étiqueter à tort un vrai reload.
    platform: Optional[Platform] = None

    # app_id : identifiant technique stable (GUID Qlik ou id de démo)
    # app_name : libellé affiché, qui peut changer
    app_id: str = Field(min_length=1)
    app_name: str = Field(min_length=1)

    event_type: EventType
    status: EventStatus

    section: Optional[str] = None
    table: Optional[str] = None
    rows: Optional[int] = Field(default=None, ge=0)

    qvd: Optional[str] = None
    qvd_path: Optional[str] = None
    qvd_size_bytes: Optional[int] = Field(default=None, ge=0)

    message: Optional[str] = None

    # Extension libre sans casser le schéma (ex. ligne du log, durée SQL...).
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_required_fields(self) -> "ReloadEvent":
        if self.platform is None:
            if self.source != EventSource.DEMO:
                raise ValueError(f"'platform' est obligatoire pour la source {self.source.value}")
            self.platform = Platform.DEMO
        et = self.event_type
        if et in SECTION_EVENTS and not self.section:
            raise ValueError(f"{et.value} requiert 'section'")
        if et in TABLE_EVENTS and not self.table:
            raise ValueError(f"{et.value} requiert 'table'")
        if et in QVD_EVENTS and not self.qvd:
            raise ValueError(f"{et.value} requiert 'qvd'")
        if (et in (EventType.QVD_SIZE_CHANGE, EventType.QVD_STABLE)
                and self.qvd_size_bytes is None):
            raise ValueError(f"{et.value} requiert 'qvd_size_bytes'")
        if et in (EventType.WARNING, EventType.ERROR) and not self.message:
            raise ValueError(f"{et.value} requiert 'message'")
        return self

    @property
    def event_key(self) -> str:
        """Empreinte déterministe servant à rejeter les doublons.

        Deux événements au contenu identique (hors `seq`) ont la même clé,
        ce qui permet de relire un log après redémarrage sans dupliquer.
        """
        payload = self.model_dump(mode="json", exclude={"seq"})
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
