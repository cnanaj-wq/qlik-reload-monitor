"""Chargement et validation de config.yaml.

Aucun chemin n'est codé en dur : tout vient du fichier. Les chemins relatifs
sont résolus par rapport au dossier du fichier de configuration.
L'existence des dossiers Qlik n'est PAS vérifiée ici : un dossier
inaccessible sera géré au moment de la surveillance, sans planter le monitor.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import (BaseModel, ConfigDict, Field, PrivateAttr, field_validator,
                      model_validator)

from .models.enums import RunMode


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QlikConfig(_Strict):
    log_paths: list[Path] = Field(default_factory=list)


class QvdConfig(_Strict):
    directories: list[Path] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=lambda: [".qvd"])


class MonitoringConfig(_Strict):
    polling_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    stable_after_measures: int = Field(default=3, ge=2)
    min_delta_bytes: int = Field(default=1, ge=0)


class DatabaseConfig(_Strict):
    path: Path = Path("./data/qlik_monitor.db")


class ServerConfig(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # Flux SSE : fréquence de lecture de SQLite, heartbeat, taille de lot.
    sse_poll_seconds: float = Field(default=0.5, gt=0, le=10)
    sse_heartbeat_seconds: float = Field(default=15, gt=0, le=300)
    sse_batch_size: int = Field(default=500, ge=1, le=10_000)
    # Interface compilée (npm run build) servie par l'API si le dossier existe.
    frontend_dist: Path = Path("./frontend/dist")


class DemoConfig(_Strict):
    output_dir: Path = Path("./demo_data")


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SmtpConfig(_Strict):
    host: str = ""
    port: int = Field(default=587, ge=1, le=65535)
    username: str = ""
    # Jamais en clair : uniquement une référence ${VARIABLE} (vérifié au chargement).
    password: str = ""
    use_tls: bool = True        # STARTTLS (port 587, ex. Gmail)
    use_ssl: bool = False       # SMTPS implicite (port 465)
    timeout_seconds: float = Field(default=15, gt=0, le=120)


class EmailConfig(_Strict):
    enabled: bool = False
    # dry_run : le message est construit et journalisé, rien n'est envoyé.
    dry_run: bool = True
    recipients: list[str] = Field(default_factory=list)
    from_name: str = "Qlik Reload Monitor"
    from_address: str = ""
    max_attempts: int = Field(default=3, ge=1, le=3)
    retry_delay_seconds: float = Field(default=2, ge=0, le=30)
    smtp: SmtpConfig = Field(default_factory=SmtpConfig)
    # Variables d'environnement référencées mais absentes (diagnostic).
    _missing_env: list[str] = PrivateAttr(default_factory=list)

    @field_validator("recipients")
    @classmethod
    def _valid_recipients(cls, v: list[str]) -> list[str]:
        for r in v:
            if not _EMAIL_RE.match(r):
                raise ValueError(f"adresse email invalide : {r!r}")
        return v

    @property
    def missing_env(self) -> list[str]:
        return list(self._missing_env)


class NotificationsConfig(_Strict):
    email: EmailConfig = Field(default_factory=EmailConfig)
    # Rattrapage au démarrage d'un collecteur : reloads en erreur terminés
    # depuis moins de N minutes et jamais notifiés.
    catch_up_minutes: int = Field(default=60, ge=0, le=1440)


class AppConfig(_Strict):
    mode: RunMode = RunMode.DEMO
    qlik: QlikConfig = Field(default_factory=QlikConfig)
    qvd: QvdConfig = Field(default_factory=QvdConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    demo: DemoConfig = Field(default_factory=DemoConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    @model_validator(mode="after")
    def _live_requires_sources(self) -> "AppConfig":
        if self.mode == RunMode.LIVE:
            if not self.qlik.log_paths:
                raise ValueError("mode live : qlik.log_paths ne peut pas être vide")
            if not self.qvd.directories:
                raise ValueError("mode live : qvd.directories ne peut pas être vide")
        return self


def _resolve(p: Path, base: Path) -> Path:
    return p if p.is_absolute() else (base / p).resolve()


def load_config(path: str | Path) -> AppConfig:
    """Charge, valide et résout les chemins d'un fichier de configuration."""
    cfg_path = Path(path).resolve()
    if not cfg_path.is_file():
        raise FileNotFoundError(f"Fichier de configuration introuvable : {cfg_path}")

    with cfg_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError("config.yaml doit contenir un objet YAML à la racine")

    missing = _prepare_notifications(raw)
    cfg = AppConfig.model_validate(raw)
    cfg.notifications.email._missing_env = missing

    base = cfg_path.parent
    cfg.database.path = _resolve(cfg.database.path, base)
    cfg.demo.output_dir = _resolve(cfg.demo.output_dir, base)
    cfg.server.frontend_dist = _resolve(cfg.server.frontend_dist, base)
    cfg.qlik.log_paths = [_resolve(p, base) for p in cfg.qlik.log_paths]
    cfg.qvd.directories = [_resolve(p, base) for p in cfg.qvd.directories]
    cfg.qvd.extensions = [e.lower() if e.startswith(".") else f".{e.lower()}"
                          for e in cfg.qvd.extensions]
    return cfg


def _prepare_notifications(raw: dict) -> list[str]:
    """Contrôle les secrets puis remplace les ${VAR} de la section notifications.

    - `smtp.password` doit être vide ou de la forme exacte `${VARIABLE}` :
      un mot de passe écrit en clair dans le fichier est refusé.
    - Les références sont résolues depuis l'environnement ; une variable absente
      devient une chaîne vide et son nom est retourné (diagnostic à l'envoi).
    Seule la section notifications est concernée.
    """
    section = raw.get("notifications")
    if not isinstance(section, dict):
        return []
    smtp = (section.get("email") or {}).get("smtp") or {}
    password = smtp.get("password") if isinstance(smtp, dict) else None
    if password not in (None, "") and not _ENV_REF.fullmatch(str(password)):
        raise ValueError(
            "notifications.email.smtp.password ne doit jamais contenir de mot de passe en "
            "clair : utiliser une variable d'environnement, ex. \"${SMTP_PASSWORD}\"")

    missing: list[str] = []

    def expand(value):
        if isinstance(value, str):
            def repl(m):
                name = m.group(1)
                if name not in os.environ:
                    missing.append(name)
                return os.environ.get(name, "")
            return _ENV_REF.sub(repl, value)
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value

    raw["notifications"] = expand(section)
    return sorted(set(missing))
