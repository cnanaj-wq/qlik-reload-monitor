"""Énumérations partagées par le modèle d'événements, la base et l'API."""

from enum import Enum


class RunMode(str, Enum):
    """Mode de fonctionnement du monitor."""

    DEMO = "demo"
    LIVE = "live"


class Platform(str, Enum):
    """Plateforme Qlik d'origine d'un reload (indépendante du format de log)."""

    DEMO = "demo"
    QLIK_SENSE = "qlik_sense"
    QLIK_VIEW = "qlik_view"


class EventSource(str, Enum):
    """Origine d'un événement."""

    DEMO = "demo"                # simulateur
    QLIK_LOG = "qlik_log"        # parsing des script logs Qlik
    QVD_WATCHER = "qvd_watcher"  # surveillance os.stat des QVD


class EventType(str, Enum):
    """Types d'événements du cycle de vie d'un reload."""

    RELOAD_START = "RELOAD_START"
    RELOAD_END = "RELOAD_END"

    SECTION_START = "SECTION_START"
    SECTION_END = "SECTION_END"

    TABLE_START = "TABLE_START"
    TABLE_PROGRESS = "TABLE_PROGRESS"
    TABLE_END = "TABLE_END"

    QVD_WRITE_START = "QVD_WRITE_START"
    QVD_SIZE_CHANGE = "QVD_SIZE_CHANGE"
    QVD_WRITE_END = "QVD_WRITE_END"
    # Émis une fois par épisode d'écriture, quand le watcher constate que la
    # taille du fichier ne bouge plus (N mesures identiques).
    QVD_STABLE = "QVD_STABLE"

    WARNING = "WARNING"
    ERROR = "ERROR"


class EventStatus(str, Enum):
    """Statut porté par un événement (et par un reload ou une étape)."""

    PENDING = "PENDING"   # ⚪ en attente
    RUNNING = "RUNNING"   # 🔵 en cours
    SUCCESS = "SUCCESS"   # 🟢 succès
    WARNING = "WARNING"   # 🟠 warning
    ERROR = "ERROR"       # 🔴 erreur


# Familles d'événements : utilisées pour valider les champs obligatoires.
SECTION_EVENTS = frozenset({EventType.SECTION_START, EventType.SECTION_END})
TABLE_EVENTS = frozenset(
    {EventType.TABLE_START, EventType.TABLE_PROGRESS, EventType.TABLE_END}
)
QVD_EVENTS = frozenset(
    {EventType.QVD_WRITE_START, EventType.QVD_SIZE_CHANGE, EventType.QVD_WRITE_END,
     EventType.QVD_STABLE}
)
