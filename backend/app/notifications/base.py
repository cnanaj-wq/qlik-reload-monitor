"""Abstractions des notifications, indépendantes de tout fournisseur.

Un `Notifier` représente un canal (email aujourd'hui ; Teams, Slack, webhook
plus tard). Il sait construire un envoi vers un destinataire. Le
`NotificationService` décide QUAND notifier, garantit l'unicité et gère les
nouvelles tentatives ; il ne connaît aucun protocole d'envoi.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class Message:
    subject: str
    body: str


class DeliveryError(Exception):
    """Échec d'envoi temporaire : une nouvelle tentative peut réussir."""


class PermanentDeliveryError(DeliveryError):
    """Échec qui ne se résoudra pas en réessayant (authentification, configuration)."""


class Notifier(Protocol):
    channel: str          # 'email', 'teams', 'slack', 'webhook'
    enabled: bool
    dry_run: bool

    def recipients(self) -> list[str]: ...

    def deliver(self, message: Message, recipient: str) -> None:
        """Envoie le message. Lève DeliveryError / PermanentDeliveryError en cas d'échec."""
        ...


@dataclass(frozen=True)
class NotificationResult:
    reload_id: str
    channel: str
    recipient: str
    status: str                 # SENT | FAILED | DRY_RUN
    attempts: int
    subject: str
    error_message: Optional[str] = None
    message: Optional[Message] = None
