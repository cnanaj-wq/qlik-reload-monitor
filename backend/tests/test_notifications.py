"""Tests des notifications email sur reload en erreur (étape 5)."""

from __future__ import annotations

import logging
import smtplib
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import pytest
from pydantic import ValidationError

from app.config import AppConfig, DatabaseConfig, EmailConfig, NotificationsConfig, load_config
from app.engine import EventEngine
from app.models import EventSource, EventStatus, EventType, Platform, ReloadEvent
from app.notifications import (EmailNotifier, NotificationService, PermanentDeliveryError,
                               SmtpTransport, build_service)
from app.notifications.__main__ import main as notif_cli
from app.storage.db import open_db

T0 = datetime(2026, 10, 1, 22, 46, 2)
ET = EventType
S = EventStatus
MIB = 1024 * 1024


# ------------------------------------------------------------------ outils

class FakeTransport:
    """Transport SMTP simulé : enregistre les messages, peut échouer N fois."""

    def __init__(self, fail_times: int = 0, exc: Optional[Exception] = None):
        self.sent = []
        self.calls = 0
        self.fail_times = fail_times
        self.exc = exc or smtplib.SMTPServerDisconnected("connexion perdue")

    def send(self, msg):
        self.calls += 1
        if self.calls <= self.fail_times:
            if isinstance(self.exc, PermanentDeliveryError):
                raise self.exc
            from app.notifications import DeliveryError
            raise DeliveryError(str(self.exc))
        self.sent.append(msg)


def email_cfg(**kw) -> EmailConfig:
    base = dict(enabled=True, dry_run=False, recipients=["alertes@exemple.fr"],
                from_address="monitor@exemple.fr")
    base.update(kw)
    return EmailConfig(**base)


class Env:
    def __init__(self, tmp_path, *, transport=None, cfg: Optional[EmailConfig] = None,
                 clock=lambda: T0 + timedelta(minutes=5)):
        self.db = tmp_path / "m.db"
        self.conn = open_db(self.db)
        self.engine = EventEngine(self.conn)
        self.transport = transport or FakeTransport()
        self.sleeps: list[float] = []
        self.cfg = cfg or email_cfg()
        self.notifier = EmailNotifier(self.cfg, transport=self.transport, clock=clock)
        self.service = NotificationService(self.db, [self.notifier], max_attempts=3,
                                           retry_delay_seconds=2, clock=clock,
                                           sleep=self.sleeps.append)
        self.engine.add_listener(self.service.on_event)

    def ev(self, et, t, rid="R1", platform=Platform.QLIK_SENSE, app="VENTES", **kw):
        base = dict(reload_id=rid, timestamp=T0 + timedelta(seconds=t),
                    source=EventSource.QLIK_LOG, platform=platform, app_id=f"id-{app}",
                    app_name=app, event_type=et, status=S.RUNNING)
        base.update(kw)
        return self.engine.ingest(ReloadEvent(**base))

    def log_rows(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM notification_log ORDER BY id")]

    def error_reload(self, rid="R1", platform=Platform.QLIK_SENSE, app="VENTES"):
        """Reload réaliste en erreur : warning, VENTES.qvd en écriture, ORA-00942."""
        kw = dict(rid=rid, platform=platform, app=app)
        self.ev(ET.RELOAD_START, 0, **kw)
        self.ev(ET.WARNING, 2, status=S.WARNING, section="PARAMETRES",
                message="Variable vDateDebut non définie", **kw)
        self.ev(ET.SECTION_START, 10, section="FAITS", **kw)
        self.ev(ET.QVD_WRITE_START, 20, section="FAITS", qvd="VENTES.qvd",
                qvd_path="D:/QVD/VENTES.qvd", **kw)
        self.engine.record_qvd_measure(timestamp=T0 + timedelta(seconds=21), qvd_name="VENTES.qvd",
                                       path="D:/QVD/VENTES.qvd", size_bytes=16 * MIB,
                                       reload_id=rid)
        self.ev(ET.TABLE_START, 30, section="FAITS", table="VENTES", **kw)
        self.ev(ET.TABLE_PROGRESS, 50, section="FAITS", table="VENTES", rows=737_022, **kw)
        self.ev(ET.ERROR, 79, status=S.ERROR, section="FAITS", table="VENTES", rows=737_022,
                message="ORA-00942: table or view does not exist", **kw)
        self.ev(ET.RELOAD_END, 79, status=S.ERROR, message="Le rechargement a échoué", **kw)

    def close(self):
        self.conn.close()


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


def body_of(msg) -> str:
    return msg.get_content()


# ------------------------------------------------------------- déclenchement

def test_email_sur_reload_end_error(env):
    env.error_reload()
    assert len(env.transport.sent) == 1
    [row] = env.log_rows()
    assert (row["reload_id"], row["channel"], row["recipient"], row["status"],
            row["attempts"]) == ("R1", "email", "alertes@exemple.fr", "SENT", 1)
    assert row["sent_at"] is not None and row["error_message"] is None


def test_aucune_notification_sur_success(env):
    env.ev(ET.RELOAD_START, 0)
    env.ev(ET.RELOAD_END, 10, status=S.SUCCESS)
    assert env.transport.sent == [] and env.log_rows() == []


def test_aucune_notification_sur_warning(env):
    env.ev(ET.RELOAD_START, 0)
    env.ev(ET.WARNING, 3, status=S.WARNING, message="clé synthétique")
    env.ev(ET.RELOAD_END, 10, status=S.SUCCESS)
    assert env.engine.get_reload_state("R1").status is S.WARNING
    assert env.transport.sent == [] and env.log_rows() == []


def test_erreur_intermediaire_sans_fin_ne_notifie_pas(env):
    env.ev(ET.RELOAD_START, 0)
    env.ev(ET.ERROR, 5, status=S.ERROR, message="erreur non bloquante")
    assert env.engine.get_reload_state("R1").is_running
    assert env.transport.sent == []


def test_fin_success_apres_erreur_notifie_car_statut_final_error(env):
    """Règle documentée : le statut final du reload fait foi (ERROR collant)."""
    env.ev(ET.RELOAD_START, 0)
    env.ev(ET.ERROR, 5, status=S.ERROR, message="ErrorMode=0 : le script a continué")
    env.ev(ET.RELOAD_END, 10, status=S.SUCCESS)
    assert len(env.transport.sent) == 1


# -------------------------------------------------------------- unicité

def test_un_seul_email_par_reload(env, tmp_path):
    env.error_reload()
    # Fin reçue en double, erreur arrivée en retard, retraitement manuel :
    env.ev(ET.RELOAD_END, 79, status=S.ERROR, message="Le rechargement a échoué")
    env.ev(ET.ERROR, 90, status=S.ERROR, message="erreur tardive")
    env.service.process_reload("R1")
    env.service.process_reload("R1")
    # Redémarrage : nouveau service sur la même base.
    other = NotificationService(env.db, [env.notifier], sleep=lambda s: None)
    other.process_reload("R1")
    other.catch_up()
    assert len(env.transport.sent) == 1
    assert len(env.log_rows()) == 1


def test_unicite_garantie_par_la_base(env):
    env.error_reload()
    with pytest.raises(sqlite3.IntegrityError):
        env.conn.execute("INSERT INTO notification_log (reload_id, channel, recipient, status,"
                         " created_at) VALUES ('R1','email','alertes@exemple.fr','PENDING','t')")


def test_deux_reloads_en_erreur_deux_emails(env):
    env.error_reload("R1")
    env.error_reload("R2")
    assert len(env.transport.sent) == 2
    assert {r["reload_id"] for r in env.log_rows()} == {"R1", "R2"}


# ---------------------------------------------------------- retry / échecs

def test_retry_puis_succes(tmp_path):
    env = Env(tmp_path, transport=FakeTransport(fail_times=2))
    env.error_reload()
    [row] = env.log_rows()
    assert row["status"] == "SENT" and row["attempts"] == 3
    assert env.sleeps == [2, 2]                     # attente courte entre tentatives
    env.close()


def test_echec_smtp_apres_3_tentatives(tmp_path, caplog):
    env = Env(tmp_path, transport=FakeTransport(fail_times=99))
    with caplog.at_level(logging.ERROR):
        env.error_reload()                          # aucune exception ne remonte
    [row] = env.log_rows()
    assert row["status"] == "FAILED" and row["attempts"] == 3
    assert "connexion perdue" in row["error_message"]
    assert env.transport.calls == 3                 # pas de retry infini
    assert env.engine.get_reload_state("R1").status is S.ERROR
    assert env.engine.get_reload_state("R1").errors_count == 1  # pas d'erreur Qlik ajoutée
    assert any("notification_failed" in r.message for r in caplog.records)
    env.service.process_reload("R1")                # pas de relance automatique en boucle
    assert env.transport.calls == 3
    env.close()


def test_echec_permanent_pas_de_retry(tmp_path):
    env = Env(tmp_path, transport=FakeTransport(
        fail_times=99, exc=PermanentDeliveryError("authentification SMTP refusée (535)")))
    env.error_reload()
    [row] = env.log_rows()
    assert row["status"] == "FAILED" and row["attempts"] == 1
    assert "535" in row["error_message"] and env.sleeps == []
    env.close()


def test_exception_inattendue_du_transport(tmp_path):
    class Broken:
        def send(self, msg):
            raise RuntimeError("bug")
    env = Env(tmp_path, transport=Broken())
    env.error_reload()
    assert env.log_rows()[0]["status"] == "FAILED"
    env.close()


# ---------------------------------------------------------------- dry_run

def test_dry_run(tmp_path, caplog):
    env = Env(tmp_path, cfg=email_cfg(dry_run=True))
    with caplog.at_level(logging.WARNING):
        env.error_reload()
    assert env.transport.calls == 0
    [row] = env.log_rows()
    assert row["status"] == "DRY_RUN" and row["sent_at"] is None
    dry = [r for r in caplog.records if "EMAIL DRY RUN" in r.getMessage()]
    assert dry and "#Error Reload QlikSense | VENTES" in dry[0].getMessage()
    assert "Votre Agent Claude" in dry[0].getMessage()
    env.close()


def test_notifications_desactivees(tmp_path):
    env = Env(tmp_path, cfg=email_cfg(enabled=False))
    env.error_reload()
    assert env.transport.calls == 0 and env.log_rows() == []
    env.close()


# ---------------------------------------------------------- destinataires

def test_destinataires_configures(tmp_path):
    env = Env(tmp_path, cfg=email_cfg(recipients=["alertes@exemple.fr", "ops@exemple.fr"]))
    env.error_reload()
    assert [m["To"] for m in env.transport.sent] == ["alertes@exemple.fr", "ops@exemple.fr"]
    assert env.transport.sent[0]["From"] == "Qlik Reload Monitor <monitor@exemple.fr>"
    assert {r["recipient"] for r in env.log_rows()} == {"alertes@exemple.fr", "ops@exemple.fr"}
    env.close()


def test_destinataire_par_defaut_de_la_configuration_projet(monkeypatch):
    # config.yaml ne contient aucune adresse réelle : le destinataire vient de l'environnement.
    from pathlib import Path
    monkeypatch.setenv("ALERT_EMAIL_RECIPIENT", "alertes@exemple.fr")
    cfg = load_config(Path(__file__).resolve().parents[2] / "config.yaml")
    assert cfg.notifications.email.recipients == ["alertes@exemple.fr"]
    assert cfg.notifications.email.dry_run is True


# ------------------------------------------------------------ sujet / corps

@pytest.mark.parametrize("platform,app,attendu", [
    (Platform.QLIK_SENSE, "VENTES", "#Error Reload QlikSense | VENTES | 01/10/2026 22:47"),
    (Platform.QLIK_VIEW, "VENTES_FINANCE.qvw",
     "#Error Reload QlikView | VENTES_FINANCE.qvw | 01/10/2026 22:47"),
    (Platform.DEMO, "VENTES", "#Error Reload DEMO | VENTES | 01/10/2026 22:47"),
])
def test_sujet_par_plateforme(env, platform, app, attendu):
    env.error_reload(platform=platform, app=app)
    assert env.transport.sent[0]["Subject"] == attendu


def test_corps_complet_qlik_sense(env):
    env.error_reload()
    body = body_of(env.transport.sent[0])
    attendu = """Bonjour,

Une erreur a été détectée lors du rechargement Qlik.

Plateforme : Qlik Sense
Application : VENTES
Reload ID : R1
Début : 01/10/2026 22:46:02
Fin : 01/10/2026 22:47:21
Durée : 1 min 19 s

Statut : ERROR

Étape :
FAITS > VENTES

Dernière table :
VENTES

Lignes chargées avant erreur :
737 022

Erreur rencontrée :
ORA-00942: table or view does not exist

Warnings précédents :
1

QVD courant :
VENTES.qvd

Dernière taille observée :
16.0 MB

Vous pouvez consulter le détail complet du reload dans Qlik Reload Monitor.

Votre Agent Claude
"""
    assert body == attendu


def test_corps_qlikview(env):
    env.error_reload(platform=Platform.QLIK_VIEW, app="VENTES_FINANCE.qvw")
    body = body_of(env.transport.sent[0])
    assert "Plateforme : QlikView\nDocument : VENTES_FINANCE.qvw\n" in body


def test_corps_demo(env):
    env.error_reload(platform=Platform.DEMO)
    assert "Plateforme : DEMO\nApplication : VENTES\n" in body_of(env.transport.sent[0])


def test_signature_obligatoire(env):
    env.error_reload()
    assert body_of(env.transport.sent[0]).rstrip().endswith("Votre Agent Claude")


def test_donnees_facultatives_absentes(env):
    env.ev(ET.RELOAD_START, 0)
    env.ev(ET.RELOAD_END, 4, status=S.ERROR)        # aucun détail disponible
    body = body_of(env.transport.sent[0])
    for label in ("Étape :", "Dernière table :", "Lignes chargées avant erreur :",
                  "Erreur rencontrée :", "QVD courant :", "Dernière taille observée :"):
        assert f"{label}\nNon disponible\n" in body, label
    assert "Warnings précédents :\n0\n" in body
    assert "Durée : 4.0 s" in body


def test_message_de_fin_utilise_si_pas_d_evenement_error(env):
    env.ev(ET.RELOAD_START, 0)
    env.ev(ET.RELOAD_END, 4, status=S.ERROR, message="Connexion ODBC perdue")
    assert "Erreur rencontrée :\nConnexion ODBC perdue\n" in body_of(env.transport.sent[0])


def test_section_seule_sans_table(env):
    env.ev(ET.RELOAD_START, 0)
    env.ev(ET.ERROR, 3, status=S.ERROR, section="PARAMETRES", message="x")
    env.ev(ET.RELOAD_END, 4, status=S.ERROR)
    body = body_of(env.transport.sent[0])
    assert "Étape :\nPARAMETRES\n" in body and "Dernière table :\nNon disponible\n" in body


# ------------------------------------------------------------ persistance

def test_persistance_notification_log(env):
    env.error_reload()
    [row] = env.log_rows()
    assert row["subject"] == "#Error Reload QlikSense | VENTES | 01/10/2026 22:47"
    assert row["created_at"] == "2026-10-01T22:51:02"
    assert row["sent_at"] == "2026-10-01T22:51:02"
    env.conn.close()
    conn = sqlite3.connect(env.db)                  # relu après fermeture
    assert conn.execute("SELECT status FROM notification_log").fetchone()[0] == "SENT"
    conn.close()
    env.conn = open_db(env.db)


# ---------------------------------------------------------------- rattrapage

def test_rattrapage_recent_uniquement(tmp_path):
    env = Env(tmp_path)
    env.engine._listeners.clear()                   # collecteur arrêté pendant les reloads
    env.error_reload("ANCIEN")
    env.ev(ET.RELOAD_START, 3600, rid="RECENT")
    env.ev(ET.RELOAD_END, 3700, rid="RECENT", status=S.ERROR, message="x")
    service = NotificationService(env.db, [env.notifier], catch_up_minutes=30,
                                  clock=lambda: T0 + timedelta(seconds=3700 + 60),
                                  sleep=lambda s: None)
    results = service.catch_up()
    assert [r.reload_id for r in results] == ["RECENT"]
    env.close()


# --------------------------------------------------------------- abonnés moteur

def test_abonne_en_echec_ne_casse_pas_l_ingestion(tmp_path):
    conn = open_db(tmp_path / "m.db")
    eng = EventEngine(conn)
    seen = []

    def boom(e):
        raise RuntimeError("abonné défaillant")
    eng.add_listener(boom)
    eng.add_listener(seen.append)
    res = eng.ingest(ReloadEvent(reload_id="R", timestamp=T0, source=EventSource.DEMO,
                                 app_id="a", app_name="A", event_type=ET.RELOAD_START,
                                 status=S.RUNNING))
    assert res.accepted and [e.seq for e in seen] == [res.seq]
    # doublon : pas de nouvel appel
    eng.ingest(ReloadEvent(reload_id="R", timestamp=T0, source=EventSource.DEMO,
                           app_id="a", app_name="A", event_type=ET.RELOAD_START,
                           status=S.RUNNING))
    assert len(seen) == 1
    conn.close()


# ------------------------------------------------------------ configuration

def write_cfg(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_mot_de_passe_en_clair_refuse(tmp_path):
    p = write_cfg(tmp_path, "notifications:\n  email:\n    smtp:\n      password: 'secret123'\n")
    with pytest.raises(ValueError, match="clair"):
        load_config(p)


def test_variables_d_environnement(tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.delenv("SMTP_FROM", raising=False)
    p = write_cfg(tmp_path, "notifications:\n  email:\n    from_address: '${SMTP_FROM}'\n"
                            "    smtp:\n      host: '${SMTP_HOST}'\n      password: '${SMTP_PASSWORD}'\n")
    e = load_config(p).notifications.email
    assert e.smtp.host == "smtp.gmail.com" and e.smtp.password == "app-password"
    assert e.from_address == "" and e.missing_env == ["SMTP_FROM"]


@pytest.mark.parametrize("bad", ["recipients: ['pas-un-email']", "max_attempts: 5",
                                 "max_attempts: 0"])
def test_configuration_email_invalide(tmp_path, bad):
    with pytest.raises(ValidationError):
        load_config(write_cfg(tmp_path, f"notifications:\n  email:\n    {bad}\n"))


def test_defaut_sans_section_notifications(tmp_path):
    e = load_config(write_cfg(tmp_path, "mode: demo\n")).notifications.email
    assert e.enabled is False and e.recipients == []


# ---------------------------------------------------------------- transport SMTP

class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.log = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.log.append("quit")

    def ehlo(self):
        self.log.append("ehlo")

    def starttls(self, context=None):
        self.log.append("starttls")

    def login(self, user, pwd):
        self.log.append(("login", user))
        if pwd == "mauvais":
            raise smtplib.SMTPAuthenticationError(535, b"refus")

    def send_message(self, msg):
        self.log.append(("send", msg["To"]))


def test_smtp_starttls_login_envoi(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    cfg = email_cfg(smtp={"host": "smtp.gmail.com", "username": "u@gmail.com",
                          "password": "app-pass"})
    EmailNotifier(cfg).deliver(
        __import__("app.notifications", fromlist=["Message"]).Message("s", "b"), "x@y.fr")
    log = FakeSMTP.instances[0].log
    assert log == ["ehlo", "starttls", "ehlo", ("login", "u@gmail.com"), ("send", "x@y.fr"),
                   "quit"]


def test_smtp_authentification_refusee_est_permanente(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    cfg = email_cfg(smtp={"host": "h", "username": "u", "password": "mauvais"})
    with pytest.raises(PermanentDeliveryError, match="535") as exc:
        SmtpTransport(cfg.smtp).send(EmailNotifier(cfg).build(
            __import__("app.notifications", fromlist=["Message"]).Message("s", "b"), "x@y.fr"))
    assert "mauvais" not in str(exc.value)          # jamais de secret dans l'erreur


def test_smtp_non_configure(monkeypatch):
    with pytest.raises(PermanentDeliveryError, match="SMTP_HOST"):
        SmtpTransport(email_cfg().smtp).send(None)


def test_expediteur_absent():
    with pytest.raises(PermanentDeliveryError, match="SMTP_FROM"):
        EmailNotifier(email_cfg(from_address="")).build(
            __import__("app.notifications", fromlist=["Message"]).Message("s", "b"), "x@y.fr")


# ----------------------------------------------------------------------- CLI

def test_cli_test_email_dry_run(tmp_path, capsys):
    p = write_cfg(tmp_path, "notifications:\n  email:\n    enabled: true\n    dry_run: true\n"
                            "    recipients: ['alertes@exemple.fr']\n")
    assert notif_cli(["--config", str(p), "test-email"]) == 0
    out = capsys.readouterr().out
    assert "EMAIL DRY RUN" in out and "#Test Qlik Reload Monitor" in out
    assert "Votre Agent Claude" in out


def test_cli_test_email_envoi_reel_en_echec(tmp_path, capsys):
    p = write_cfg(tmp_path, "notifications:\n  email:\n    enabled: true\n    dry_run: false\n"
                            "    recipients: ['alertes@exemple.fr']\n    from_address: 'a@b.fr'\n")
    assert notif_cli(["--config", str(p), "test-email"]) == 1
    assert "❌" in capsys.readouterr().out


def test_cli_preview(env, capsys):
    env.error_reload()
    cfg = write_cfg(env.db.parent, f"database:\n  path: '{env.db.as_posix()}'\n")
    assert notif_cli(["--config", str(cfg), "preview", "R1"]) == 0
    out = capsys.readouterr().out
    assert "#Error Reload QlikSense | VENTES" in out and "ORA-00942" in out


def test_build_service_depuis_la_configuration(tmp_path):
    cfg = AppConfig(database=DatabaseConfig(path=tmp_path / "m.db"),
                    notifications=NotificationsConfig(email=email_cfg(dry_run=True)))
    service = build_service(cfg)
    assert service.max_attempts == 3 and len(service.notifiers) == 1

