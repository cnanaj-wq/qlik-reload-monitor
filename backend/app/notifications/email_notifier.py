"""Canal email : SMTP standard (STARTTLS ou SSL), compatible Gmail avec mot de passe d'application."""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, format_datetime, make_msgid
from typing import Optional, Protocol

from ..config import EmailConfig, SmtpConfig
from .base import DeliveryError, Message, PermanentDeliveryError


class Transport(Protocol):
    def send(self, msg: EmailMessage) -> None: ...


class SmtpTransport:
    """Envoi SMTP. Ne journalise jamais le mot de passe."""

    def __init__(self, cfg: SmtpConfig) -> None:
        self.cfg = cfg

    def send(self, msg: EmailMessage) -> None:
        c = self.cfg
        if not c.host:
            raise PermanentDeliveryError(
                "serveur SMTP non configuré (variable d'environnement SMTP_HOST absente ?)")
        if c.username and not c.password:
            raise PermanentDeliveryError(
                "mot de passe SMTP absent (variable d'environnement SMTP_PASSWORD absente ?)")
        try:
            context = ssl.create_default_context()
            if c.use_ssl:
                server = smtplib.SMTP_SSL(c.host, c.port, timeout=c.timeout_seconds,
                                          context=context)
            else:
                server = smtplib.SMTP(c.host, c.port, timeout=c.timeout_seconds)
            with server:
                server.ehlo()
                if c.use_tls and not c.use_ssl:
                    server.starttls(context=context)
                    server.ehlo()
                if c.username:
                    server.login(c.username, c.password)
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise PermanentDeliveryError(
                f"authentification SMTP refusée ({exc.smtp_code})") from None
        except (smtplib.SMTPException, OSError) as exc:
            raise DeliveryError(f"{type(exc).__name__}: {exc}") from None


class EmailNotifier:
    channel = "email"

    def __init__(self, cfg: EmailConfig, transport: Optional[Transport] = None,
                 clock=datetime.now) -> None:
        self.cfg = cfg
        self.transport = transport or SmtpTransport(cfg.smtp)
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    @property
    def dry_run(self) -> bool:
        return self.cfg.dry_run

    def recipients(self) -> list[str]:
        return list(self.cfg.recipients)

    def build(self, message: Message, recipient: str) -> EmailMessage:
        if not self.cfg.from_address:
            raise PermanentDeliveryError(
                "adresse d'expéditeur absente (variable d'environnement SMTP_FROM absente ?)")
        msg = EmailMessage()
        msg["From"] = formataddr((self.cfg.from_name, self.cfg.from_address))
        msg["To"] = recipient
        msg["Subject"] = message.subject
        msg["Date"] = format_datetime(self._clock().astimezone())
        msg["Message-ID"] = make_msgid(domain="qlik-reload-monitor.local")
        msg.set_content(message.body)
        return msg

    def deliver(self, message: Message, recipient: str) -> None:
        self.transport.send(self.build(message, recipient))
