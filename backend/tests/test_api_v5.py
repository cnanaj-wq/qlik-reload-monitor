"""Tests des extensions de l'API (étape 5) : plateformes, filtres, notifications, frontend."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import (AppConfig, DatabaseConfig, EmailConfig, NotificationsConfig,
                        ServerConfig)
from app.engine import EventEngine
from app.models import EventSource, EventStatus, EventType, Platform, ReloadEvent
from app.notifications import EmailNotifier, NotificationService
from app.storage.db import open_db

ET = EventType
S = EventStatus


def ev(rid, et, when, platform, app, **kw):
    return ReloadEvent(reload_id=rid, timestamp=when, source=EventSource.QLIK_LOG,
                       platform=platform, app_id=f"id-{app}", app_name=app,
                       event_type=et, status=kw.pop("status", S.RUNNING), **kw)


@pytest.fixture
def client(tmp_path):
    """6 reloads : 3 plateformes, statuts variés, sur 3 jours."""
    db = tmp_path / "m.db"
    conn = open_db(db)
    eng = EventEngine(conn)
    d = datetime(2026, 9, 29, 22, 0, 0)
    rows = [
        ("S1", Platform.QLIK_SENSE, "VENTES", d, S.SUCCESS),
        ("S2", Platform.QLIK_SENSE, "Ventes_Detail", d + timedelta(days=1), S.ERROR),
        ("V1", Platform.QLIK_VIEW, "VENTES_FINANCE.qvw", d + timedelta(days=1, hours=1), S.SUCCESS),
        ("V2", Platform.QLIK_VIEW, "STOCKS.qvw", d + timedelta(days=2), S.WARNING),
        ("D1", Platform.DEMO, "Ventes", d + timedelta(days=2, hours=1), S.SUCCESS),
        ("R1", Platform.QLIK_SENSE, "RH", d + timedelta(days=2, hours=2), None),  # en cours
    ]
    for rid, plat, app, when, end in rows:
        eng.ingest(ev(rid, ET.RELOAD_START, when, plat, app))
        if end is S.WARNING:
            eng.ingest(ev(rid, ET.WARNING, when + timedelta(seconds=5), plat, app,
                          status=S.WARNING, message="w"))
        if end is not None:
            eng.ingest(ev(rid, ET.RELOAD_END, when + timedelta(seconds=60), plat, app,
                          status=S.ERROR if end is S.ERROR else S.SUCCESS,
                          message="ORA-00942" if end is S.ERROR else None))
    # Notification réelle (dry-run) pour le reload en erreur S2.
    cfg = EmailConfig(enabled=True, dry_run=True, recipients=["${ALERT_EMAIL_RECIPIENT}"])
    NotificationService(db, [EmailNotifier(cfg)]).process_reload("S2")
    conn.close()
    app_cfg = AppConfig(database=DatabaseConfig(path=db),
                        notifications=NotificationsConfig(email=cfg))
    return TestClient(create_app(app_cfg))


def ids(resp):
    return [i["reload_id"] for i in resp.json()["items"]]


# ---------------------------------------------------------------- plateformes

def test_platform_dans_les_reponses(client):
    items = client.get("/api/reloads").json()["items"]
    assert {i["reload_id"]: i["platform"] for i in items}["V1"] == "qlik_view"
    assert client.get("/api/reloads/S1").json()["platform"] == "qlik_sense"
    assert client.get("/api/reloads/current").json()["platform"] == "qlik_sense"   # R1
    events = client.get("/api/reloads/D1/events").json()
    assert {e["platform"] for e in events} == {"demo"}


# ------------------------------------------------------------------- filtres

def test_tri_par_defaut_plus_recent_d_abord(client):
    assert ids(client.get("/api/reloads")) == ["R1", "D1", "V2", "V1", "S2", "S1"]


@pytest.mark.parametrize("platform,attendu", [
    ("qlik_sense", ["R1", "S2", "S1"]), ("qlik_view", ["V2", "V1"]), ("demo", ["D1"])])
def test_filtre_plateforme(client, platform, attendu):
    assert ids(client.get("/api/reloads", params={"platform": platform})) == attendu


def test_filtre_application_partiel_insensible_a_la_casse(client):
    assert ids(client.get("/api/reloads", params={"app": "vEnTeS"})) == ["D1", "V1", "S2", "S1"]
    assert ids(client.get("/api/reloads", params={"app": ".qvw"})) == ["V2", "V1"]
    assert ids(client.get("/api/reloads", params={"app": "_"})) == ["V1", "S2"]  # _ littéral
    assert ids(client.get("/api/reloads", params={"app": "%"})) == []            # % littéral


@pytest.mark.parametrize("status,attendu", [
    ("SUCCESS", ["D1", "V1", "S1"]), ("ERROR", ["S2"]), ("WARNING", ["V2"]), ("RUNNING", ["R1"])])
def test_filtre_statut(client, status, attendu):
    assert ids(client.get("/api/reloads", params={"status": status})) == attendu


def test_filtre_dates_bornes_incluses(client):
    r = client.get("/api/reloads", params={"date_from": "2026-09-30", "date_to": "2026-09-30"})
    assert ids(r) == ["V1", "S2"]
    assert ids(client.get("/api/reloads", params={"date_from": "2026-10-01"})) == ["R1", "D1", "V2"]
    assert ids(client.get("/api/reloads", params={"date_to": "2026-09-29"})) == ["S1"]


def test_filtres_combines_et_pagination(client):
    r = client.get("/api/reloads", params={"platform": "qlik_sense", "app": "ventes",
                                           "limit": 1, "offset": 1})
    body = r.json()
    assert body["total"] == 2 and ids(r) == ["S1"]


@pytest.mark.parametrize("params", [{"platform": "power_bi"}, {"date_from": "hier"},
                                    {"date_from": "2026-10-02", "date_to": "2026-10-01"}])
def test_filtres_invalides(client, params):
    assert client.get("/api/reloads", params=params).status_code == 422


# ------------------------------------------------------------- notifications

def test_notifications_d_un_reload(client):
    [n] = client.get("/api/reloads/S2/notifications").json()
    assert n["status"] == "DRY_RUN" and n["recipient"] == "${ALERT_EMAIL_RECIPIENT}"
    assert n["channel"] == "email" and n["subject"].startswith("#Error Reload QlikSense | Ventes_Detail")
    assert set(n) == {"id", "reload_id", "channel", "recipient", "status", "attempts",
                      "subject", "created_at", "sent_at", "error_message"}


def test_notifications_vide_et_404(client):
    assert client.get("/api/reloads/S1/notifications").json() == []
    assert client.get("/api/reloads/INCONNU/notifications").status_code == 404


def test_health_expose_l_etat_des_notifications_sans_secret(client):
    body = client.get("/health").json()
    assert body["notifications"] == {"email_enabled": True, "email_dry_run": True}
    assert body["version"] == "0.5.0"
    assert "smtp" not in str(body).lower() and "password" not in str(body).lower()


# ----------------------------------------------------------------- frontend

def test_frontend_compile_servi_a_la_racine(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>QRM</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    c = TestClient(create_app(AppConfig(database=DatabaseConfig(path=tmp_path / "m.db"),
                                        server=ServerConfig(frontend_dist=dist))))
    assert "QRM" in c.get("/").text
    assert c.get("/assets/app.js").status_code == 200
    assert c.get("/health").json()["status"] == "ok"            # l'API reste prioritaire
    assert c.get("/api/reloads").json()["total"] == 0
    assert c.post("/").status_code == 405                        # fichiers en lecture seule


def test_sans_frontend_compile_racine_404(tmp_path):
    c = TestClient(create_app(AppConfig(database=DatabaseConfig(path=tmp_path / "m.db"),
                                        server=ServerConfig(frontend_dist=tmp_path / "absent"))))
    assert c.get("/").status_code == 404

