"""Notifications (email aujourd'hui ; Teams, Slack, webhook prévus)."""

from .base import (DeliveryError, Message, NotificationResult, Notifier,
                   PermanentDeliveryError)
from .email_notifier import EmailNotifier, SmtpTransport
from .messages import SIGNATURE, build_reload_error_message, build_test_message, error_subject
from .service import NotificationService


def build_service(config, *, on_result=None, transport=None) -> NotificationService:
    """Assemble le service à partir de la configuration (seul point de câblage)."""
    email_cfg = config.notifications.email
    notifier = EmailNotifier(email_cfg, transport=transport)
    return NotificationService(
        config.database.path, [notifier],
        max_attempts=email_cfg.max_attempts,
        retry_delay_seconds=email_cfg.retry_delay_seconds,
        catch_up_minutes=config.notifications.catch_up_minutes,
        stable_after_measures=config.monitoring.stable_after_measures,
        on_result=on_result,
    )


__all__ = ["DeliveryError", "Message", "NotificationResult", "Notifier",
           "PermanentDeliveryError", "EmailNotifier", "SmtpTransport", "SIGNATURE",
           "build_reload_error_message", "build_test_message", "error_subject",
           "NotificationService", "build_service"]
