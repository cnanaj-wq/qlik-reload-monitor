"""Tests des finitions de l'étape 5 : validation SMTP, secrets masqués, test email
contrôlé, état email exposé, service de l'interface compilée."""

from __future__ import annotations

import smtplib
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import (AppConfig, DatabaseConfig, EmailConfig, NotificationsConfig,
                        ServerConfig, SmtpConfig, load_config)
from app.engine import EventEngine
from app.models import EventSource, EventStatus, EventType, Platform, ReloadEvent
from app.notifications import EmailNotifier, NotificationService, SmtpTransport
from app.notifications.__main__ import main as notif_cli
from app.notifications.base import DeliveryError
from app.notifications.email_notifier import MAX_ERROR_LENGTH, redact
from app.storage.db import open_db

SECRET = "app-p4ssw0rd-tres-secret"
USER = "compte.smtp@exemple.fr"
T0 = datetime(2026, 10, 1, 22, 46, 2)


def write_cfg(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


REAL = ("notifications:\n  email:\n    enabled: true\n    dry_run: false\n"
        "    recipients: ['${ALERT_EMAIL_RECIPIENT}']\n    from_address: '${SMTP_FROM}'\n"
        "    smtp:\n      host: '${SMTP_HOST}'\n      username: '${SMTP_USERNAME}'\n"
        "      password: '${SMTP_PASSWORD}'\n")
ENV_VARS = ("ALERT_EMAIL_RECIPIENT", "SMTP_FROM", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")


@pytest.fixture
def no_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def full_env(monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_RECIPIENT", "alertes@exemple.fr")
    monkeypatch.setenv("SMTP_FROM", "monitor@exemple.fr")
    monkeypatch.setenv("SMTP_HOST", "smtp.exemple.fr")
    monkeypatch.setenv("SMTP_USERNAME", USER)
    monkeypatch.setenv("SMTP_PASSWORD", SECRET)


# ------------------------------------------------------------ validation SMTP

def test_destinataire_en_variable_absente_ne_bloque_pas_le_demarrage(tmp_path, no_env):
    e = load_config(write_cfg(tmp_path, REAL)).notifications.email
    assert e.recipients == []
    assert "ALERT_EMAIL_RECIPIENT" in e.missing_env


def test_adresse_litterale_invalide_toujours_refusee(tmp_path, no_env):
    with pytest.raises(ValueError, match="adresse email invalide"):
        load_config(write_cfg(tmp_path, "notifications:\n  email:\n    recipients: ['']\n"))


def test_problemes_d_envoi_listes_avec_la_variable_en_cause(tmp_path, no_env):
    e = load_config(write_cfg(tmp_path, REAL)).notifications.email
    problems = e.delivery_problems()
    joined = " | ".join(problems)
    assert e.readiness == "incomplete"
    assert "aucun destinataire" in joined and "ALERT_EMAIL_RECIPIENT" in joined
    assert "serveur SMTP non défini" in joined and "SMTP_HOST" in joined
    assert "expéditeur non définie" in joined and "SMTP_FROM" in joined


def test_configuration_complete_prete(tmp_path, full_env):
    e = load_config(write_cfg(tmp_path, REAL)).notifications.email
    assert e.delivery_problems() == [] and e.readiness == "ready"


def test_identifiant_sans_mot_de_passe(tmp_path, full_env, monkeypatch):
    monkeypatch.delenv("SMTP_PASSWORD")
    e = load_config(write_cfg(tmp_path, REAL)).notifications.email
    assert any("mot de passe SMTP absent" in p and "SMTP_PASSWORD" in p
               for p in e.delivery_problems())


@pytest.mark.parametrize("enabled,dry_run,expected", [
    (False, False, "disabled"), (False, True, "disabled"), (True, True, "dry_run")])
def test_rien_a_signaler_si_aucun_envoi_reel(enabled, dry_run, expected):
    e = EmailConfig(enabled=enabled, dry_run=dry_run)
    assert e.delivery_problems() == [] and e.readiness == expected


def test_expediteur_invalide_signale():
    e = EmailConfig(enabled=True, dry_run=False, recipients=["a@exemple.fr"],
                    from_address="pas-une-adresse", smtp=SmtpConfig(host="smtp.exemple.fr"))
    assert e.delivery_problems() == ["adresse d'expéditeur invalide "
                                     "(notifications.email.from_address)"]


# -------------------------------------------------------- secrets masqués

def test_redact_masque_secrets_et_borne_le_message():
    msg = redact(f"535 refus pour {USER}\n mot de passe {SECRET} " + "x" * 1000, [SECRET, USER])
    assert SECRET not in msg and USER not in msg and "***" in msg
    assert "\n" not in msg and len(msg) <= MAX_ERROR_LENGTH


def test_transport_smtp_ne_recopie_jamais_le_mot_de_passe(monkeypatch):
    def boom(*a, **kw):
        raise OSError(f"connexion refusée ({USER}:{SECRET})")
    monkeypatch.setattr(smtplib, "SMTP", boom)
    t = SmtpTransport(SmtpConfig(host="smtp.exemple.fr", username=USER, password=SECRET))
    with pytest.raises(DeliveryError) as exc:
        t.send(None)
    assert SECRET not in str(exc.value) and USER not in str(exc.value)


class LeakyTransport:
    """Transport défaillant dont l'exception recopie les identifiants."""

    def send(self, msg):
        raise RuntimeError(f"bug inattendu avec {USER} / {SECRET}")


def _error_reload(db):
    conn = open_db(db)
    eng = EventEngine(conn)
    kw = dict(reload_id="R1", source=EventSource.DEMO, platform=Platform.DEMO,
              app_id="ventes", app_name="VENTES")
    eng.ingest(ReloadEvent(timestamp=T0, event_type=EventType.RELOAD_START,
                           status=EventStatus.RUNNING, **kw))
    eng.ingest(ReloadEvent(timestamp=T0 + timedelta(seconds=72), event_type=EventType.RELOAD_END,
                           status=EventStatus.ERROR, message="ORA-00942", **kw))
    conn.close()


def test_echec_inattendu_journalise_sans_secret(tmp_path):
    db = tmp_path / "m.db"
    _error_reload(db)
    cfg = EmailConfig(enabled=True, dry_run=False, recipients=["alertes@exemple.fr"],
                      from_address="monitor@exemple.fr",
                      smtp=SmtpConfig(host="smtp.exemple.fr", username=USER, password=SECRET))
    service = NotificationService(db, [EmailNotifier(cfg, transport=LeakyTransport())],
                                  max_attempts=2, sleep=lambda s: None)
    [result] = service.process_reload("R1")
    assert result.status == "FAILED"
    row = sqlite3.connect(db).execute("SELECT error_message FROM notification_log").fetchone()
    assert SECRET not in row[0] and USER not in row[0] and "RuntimeError" in row[0]


# ---------------------------------------------------------- test email contrôlé

def test_test_email_configuration_incomplete_aucune_tentative(tmp_path, no_env, monkeypatch,
                                                               capsys):
    def interdit(*a, **kw):
        raise AssertionError("aucune connexion SMTP ne doit être tentée")
    monkeypatch.setattr(smtplib, "SMTP", interdit)
    monkeypatch.setattr(smtplib, "SMTP_SSL", interdit)
    assert notif_cli(["--config", str(write_cfg(tmp_path, REAL)), "test-email"]) == 1
    out = capsys.readouterr().out
    assert "Configuration incomplète, aucun envoi tenté" in out and "SMTP_HOST" in out


def test_test_email_desactive_refuse(tmp_path, capsys):
    p = write_cfg(tmp_path, "notifications:\n  email:\n    enabled: false\n    dry_run: false\n"
                            "    recipients: ['alertes@exemple.fr']\n")
    assert notif_cli(["--config", str(p), "test-email"]) == 1
    assert "désactivées" in capsys.readouterr().out


def test_test_email_echec_affiche_sans_secret(tmp_path, full_env, monkeypatch, capsys):
    def boom(*a, **kw):
        raise OSError(f"refus {SECRET}")
    monkeypatch.setattr(smtplib, "SMTP", boom)
    assert notif_cli(["--config", str(write_cfg(tmp_path, REAL)), "test-email"]) == 1
    out = capsys.readouterr().out
    assert "❌ Échec pour alertes@exemple.fr" in out
    assert SECRET not in out and USER not in out


# ---------------------------------------------------------- API : état email

def _client(tmp_path, email: EmailConfig, dist=None) -> TestClient:
    server = ServerConfig(frontend_dist=dist or tmp_path / "absent")
    return TestClient(create_app(AppConfig(
        database=DatabaseConfig(path=tmp_path / "m.db"), server=server,
        notifications=NotificationsConfig(email=email))))


def test_statut_email_dry_run(tmp_path):
    body = _client(tmp_path, EmailConfig(enabled=True, dry_run=True,
                                         recipients=["alertes@exemple.fr"])
                   ).get("/api/notifications/status").json()
    assert body == {"enabled": True, "dry_run": True, "readiness": "dry_run", "problems": []}


def test_statut_email_incomplet_sans_secret(tmp_path):
    email = EmailConfig(enabled=True, dry_run=False, recipients=["alertes@exemple.fr"],
                        smtp=SmtpConfig(username=USER, password=SECRET))
    body = _client(tmp_path, email).get("/api/notifications/status").json()
    assert body["readiness"] == "incomplete" and body["problems"]
    assert SECRET not in str(body) and USER not in str(body)
    assert "alertes@exemple.fr" not in str(body)


# ------------------------------------------------------ interface compilée

def test_interface_compilee_politique_de_cache(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>QRM</title>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    c = _client(tmp_path, EmailConfig(), dist=dist)
    index = c.get("/")
    assert index.status_code == 200 and index.headers["cache-control"] == "no-cache"
    asset = c.get("/assets/index-abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert c.get("/assets/absent.js").status_code == 404
    assert c.get("/api/notifications/status").status_code == 200  # l'API reste prioritaire
