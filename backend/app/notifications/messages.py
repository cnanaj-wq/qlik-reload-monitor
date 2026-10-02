"""Contenu des notifications : uniquement des informations réellement disponibles.

Toute donnée absente est écrite « Non disponible ». Rien n'est déduit ni
complété : ni section, ni table, ni lignes, ni QVD, ni message, ni durée.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..engine import ReloadState
from ..formatting import fmt_duration, fmt_int, fmt_size
from ..models import ReloadEvent
from .base import Message

NA = "Non disponible"
SIGNATURE = "Votre Agent Claude"

# Libellé compact du sujet (format demandé) et libellé lisible du corps.
SUBJECT_PLATFORM = {"qlik_sense": "QlikSense", "qlik_view": "QlikView", "demo": "DEMO"}
BODY_PLATFORM = {"qlik_sense": "Qlik Sense", "qlik_view": "QlikView", "demo": "DEMO"}


def _dt(value: Optional[datetime]) -> str:
    return value.strftime("%d/%m/%Y %H:%M:%S") if value else NA


def _or_na(value) -> str:
    return NA if value is None or value == "" else str(value)


def error_subject(state: ReloadState) -> str:
    """#Error Reload QlikSense | VENTES | 01/10/2026 22:47"""
    when = state.ended_at or state.started_at
    platform = SUBJECT_PLATFORM.get(state.platform, state.platform)
    return f"#Error Reload {platform} | {state.app_name} | {when:%d/%m/%Y %H:%M}"


def build_reload_error_message(state: ReloadState,
                               error_event: Optional[ReloadEvent],
                               end_event: Optional[ReloadEvent]) -> Message:
    """Email d'un reload terminé en erreur.

    Priorité des informations : l'événement ERROR (section, table, lignes et
    message au moment de l'échec), sinon le dernier état connu du reload.
    """
    section = (error_event.section if error_event else None) or state.current_section
    table = (error_event.table if error_event else None) or state.current_table
    rows = error_event.rows if error_event and error_event.rows is not None else state.current_rows
    error_text = (error_event.message if error_event else None) or (
        end_event.message if end_event else None)

    step = " > ".join(p for p in (section, table) if p) or NA
    duration = fmt_duration(state.elapsed_ms) if state.ended_at else NA
    label = "Document" if state.platform == "qlik_view" else "Application"
    qvd_size = (fmt_size(state.current_qvd_size_bytes)
                if state.current_qvd and state.current_qvd_size_bytes is not None else NA)

    body = "\n".join([
        "Bonjour,",
        "",
        "Une erreur a été détectée lors du rechargement Qlik.",
        "",
        f"Plateforme : {BODY_PLATFORM.get(state.platform, state.platform)}",
        f"{label} : {state.app_name}",
        f"Reload ID : {state.reload_id}",
        f"Début : {_dt(state.started_at)}",
        f"Fin : {_dt(state.ended_at)}",
        f"Durée : {duration}",
        "",
        f"Statut : {state.status.value}",
        "",
        "Étape :",
        step,
        "",
        "Dernière table :",
        _or_na(table),
        "",
        "Lignes chargées avant erreur :",
        fmt_int(rows) if rows is not None else NA,
        "",
        "Erreur rencontrée :",
        _or_na(error_text),
        "",
        "Warnings précédents :",
        str(state.warnings_count),
        "",
        "QVD courant :",
        _or_na(state.current_qvd),
        "",
        "Dernière taille observée :",
        qvd_size,
        "",
        "Vous pouvez consulter le détail complet du reload dans Qlik Reload Monitor.",
        "",
        SIGNATURE,
    ])
    return Message(subject=error_subject(state), body=body)


def build_test_message(now: datetime, *, recipients: list[str], smtp_host: str) -> Message:
    return Message(
        subject=f"#Test Qlik Reload Monitor | {now:%d/%m/%Y %H:%M}",
        body="\n".join([
            "Bonjour,",
            "",
            "Ceci est un email de test envoyé par Qlik Reload Monitor.",
            "Il confirme que la configuration des notifications fonctionne.",
            "Aucun reload n'a été déclenché ni modifié.",
            "",
            f"Date : {now:%d/%m/%Y %H:%M:%S}",
            f"Destinataires configurés : {', '.join(recipients) or NA}",
            f"Serveur SMTP : {smtp_host or NA}",
            "",
            SIGNATURE,
        ]),
    )
