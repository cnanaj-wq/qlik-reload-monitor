"""Tests de l'API REST (étape 4)."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import AppConfig, DatabaseConfig
from app.engine import EventEngine
from app.models import EventSource, EventStatus, EventType, ReloadEvent
from app.storage.db import SCHEMA_VERSION, open_db
from tests.test_demo_simulator import run_scenario


def make_client(db_path) -> TestClient:
    return TestClient(create_app(AppConfig(database=DatabaseConfig(path=db_path))))


@pytest.fixture
def populated(tmp_path):
    """Base contenant les 3 scénarios de démo (successful, slow, error)."""
    conn = open_db(tmp_path / "monitor.db")
    runs = {name: run_scenario(tmp_path, name, conn=conn)
            for name in ("successful", "slow", "error")}
    conn.close()
    return make_client(tmp_path / "monitor.db"), runs


@pytest.fixture
def empty(tmp_path):
    return make_client(tmp_path / "vide" / "monitor.db")


def ev(rid, et, t, status=EventStatus.RUNNING, app_id="app-x", **kw):
    return ReloadEvent(reload_id=rid, timestamp=datetime(2026, 10, 1, 9, 0, t),
                       source=EventSource.DEMO, app_id=app_id, app_name="X",
                       event_type=et, status=status, **kw)


# ---------------------------------------------------------------- health

def test_health(populated):
    client, runs = populated
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["schema_version"] == SCHEMA_VERSION
    assert body["mode"] == "demo"
    all_seqs = [e["seq"] for run in runs.values()
                for e in client.get(f"/api/reloads/{run.reload_id}/events").json()]
    assert body["last_seq"] == max(all_seqs)


def test_health_base_vide(empty):
    body = empty.get("/health").json()
    assert body["status"] == "ok" and body["last_seq"] == 0


# ------------------------------------------------------------ base vide

def test_base_vide(empty):
    assert empty.get("/api/reloads/current").json() is None
    assert empty.get("/api/reloads/active").json() == []
    assert empty.get("/api/reloads").json() == {"items": [], "total": 0,
                                                "limit": 50, "offset": 0}
    assert empty.get("/api/reloads/x").status_code == 404


# ---------------------------------------------------------- état courant

def test_etat_courant(populated):
    client, runs = populated
    body = client.get("/api/reloads/current").json()
    # Aucun reload actif : le plus récent démarré. Les 3 runs démarrent au même
    # instant virtuel ; on vérifie surtout la forme et la cohérence.
    assert body["reload_id"] in {r.reload_id for r in runs.values()}
    for key in ("status", "elapsed_ms", "current_section", "current_table", "current_rows",
                "current_qvd", "current_qvd_size_bytes", "current_qvd_writing",
                "warnings_count", "errors_count", "last_seq"):
        assert key in body


def test_etat_courant_reload_en_cours(tmp_path):
    db = tmp_path / "m.db"
    conn = open_db(db)
    eng = EventEngine(conn)
    eng.ingest(ev("R-OLD", EventType.RELOAD_START, 0, app_id="a"))
    eng.ingest(ev("R-OLD", EventType.RELOAD_END, 5, EventStatus.SUCCESS, app_id="a"))
    eng.ingest(ev("R-RUN", EventType.RELOAD_START, 1, app_id="b"))
    eng.ingest(ev("R-RUN", EventType.TABLE_PROGRESS, 3, app_id="b", section="F",
                  table="VENTES", rows=1234))
    conn.close()
    client = make_client(db)
    cur = client.get("/api/reloads/current").json()
    assert cur["reload_id"] == "R-RUN" and cur["is_running"] is True
    assert cur["status"] == "RUNNING" and cur["current_rows"] == 1234
    assert client.get("/api/reloads/current", params={"app_id": "a"}).json()["reload_id"] == "R-OLD"
    assert [s["reload_id"] for s in client.get("/api/reloads/active").json()] == ["R-RUN"]


def test_detail_reload(populated):
    client, runs = populated
    rid = runs["error"].reload_id
    body = client.get(f"/api/reloads/{rid}").json()
    assert body["status"] == "ERROR" and body["errors_count"] == 1
    assert body["warnings_count"] == 1 and body["is_running"] is False
    assert "ORA-00942" in body["last_message"]


# ------------------------------------------------------- reload introuvable

@pytest.mark.parametrize("suffix", ["", "/events", "/qvd-measures"])
def test_reload_introuvable(populated, suffix):
    client, _ = populated
    r = client.get(f"/api/reloads/INCONNU{suffix}")
    assert r.status_code == 404
    assert "INCONNU" in r.json()["detail"]


# --------------------------------------------------------------- historique

def test_historique(populated):
    client, runs = populated
    body = client.get("/api/reloads").json()
    assert body["total"] == 3 and len(body["items"]) == 3
    by_id = {i["reload_id"]: i for i in body["items"]}
    ok = by_id[runs["successful"].reload_id]
    assert ok["status"] == "SUCCESS" and ok["total_rows"] == 1_893_300
    assert ok["qvd_count"] == 3 and ok["qvd_bytes"] > 0
    assert ok["duration_ms"] > 0
    assert by_id[runs["slow"].reload_id]["status"] == "WARNING"
    err = by_id[runs["error"].reload_id]
    assert err["status"] == "ERROR" and err["qvd_count"] == 1


def test_historique_pagination_et_filtres(populated):
    client, runs = populated
    page = client.get("/api/reloads", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/api/reloads", params={"limit": 2, "offset": 2}).json()
    assert len(page["items"]) == 2 and len(page2["items"]) == 1
    ids = [i["reload_id"] for i in page["items"] + page2["items"]]
    assert len(set(ids)) == 3
    only_err = client.get("/api/reloads", params={"status": "ERROR"}).json()
    assert [i["reload_id"] for i in only_err["items"]] == [runs["error"].reload_id]
    assert client.get("/api/reloads", params={"app_id": "inconnue"}).json()["total"] == 0


def test_historique_ordre_recent_d_abord(tmp_path):
    db = tmp_path / "m.db"
    conn = open_db(db)
    eng = EventEngine(conn)
    for i, rid in enumerate(["R1", "R2", "R3"]):
        eng.ingest(ev(rid, EventType.RELOAD_START, i * 10))
    conn.close()
    items = make_client(db).get("/api/reloads").json()["items"]
    assert [i["reload_id"] for i in items] == ["R3", "R2", "R1"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1},
                                    {"status": "OK"}])
def test_historique_parametres_invalides(populated, params):
    client, _ = populated
    assert client.get("/api/reloads", params=params).status_code == 422


# ------------------------------------------------------------- événements

def test_evenements_ordonnes(populated):
    client, runs = populated
    rid = runs["successful"].reload_id
    events = client.get(f"/api/reloads/{rid}/events").json()
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
    assert events[0]["event_type"] == "RELOAD_START"
    assert events[-1]["event_type"] == "RELOAD_END"
    assert {e["reload_id"] for e in events} == {rid}
    # Même contenu que ce que le simulateur a produit.
    assert len(events) == len(runs["successful"].events)


def test_evenements_ordre_chronologique(populated):
    client, runs = populated
    rid = runs["slow"].reload_id
    events = client.get(f"/api/reloads/{rid}/events", params={"order": "timestamp"}).json()
    ts = [e["timestamp"] for e in events]
    assert ts == sorted(ts)


def test_evenements_reprise_after_seq_et_limit(populated):
    client, runs = populated
    rid = runs["successful"].reload_id
    all_ev = client.get(f"/api/reloads/{rid}/events").json()
    pivot = all_ev[9]["seq"]
    rest = client.get(f"/api/reloads/{rid}/events", params={"after_seq": pivot}).json()
    assert [e["seq"] for e in rest] == [e["seq"] for e in all_ev[10:]]
    first3 = client.get(f"/api/reloads/{rid}/events", params={"limit": 3}).json()
    assert [e["seq"] for e in first3] == [e["seq"] for e in all_ev[:3]]


def test_evenement_format(populated):
    client, runs = populated
    rid = runs["error"].reload_id
    err = [e for e in client.get(f"/api/reloads/{rid}/events").json()
           if e["event_type"] == "ERROR"][0]
    assert err["table"] == "VENTES" and err["status"] == "ERROR"
    assert err["source"] == "demo" and err["app_id"] == "demo-app-ventes"


# ------------------------------------------------------------ mesures QVD

def test_mesures_qvd(populated):
    client, runs = populated
    rid = runs["successful"].reload_id
    ms = client.get(f"/api/reloads/{rid}/qvd-measures").json()
    assert {m["qvd_name"] for m in ms} == {"CLIENTS.qvd", "PRODUITS.qvd", "VENTES.qvd"}
    clients = client.get(f"/api/reloads/{rid}/qvd-measures",
                         params={"qvd_name": "CLIENTS.qvd"}).json()
    assert [m["is_stable"] for m in clients] == [False, False, False, False, True]
    sizes = [m["size_bytes"] for m in clients]
    assert sizes == sorted(sizes)
    assert sum(m["delta_bytes"] for m in clients) == sizes[-1]
    assert all(m["source"] == "qvd_watcher" for m in clients)


def test_mesures_qvd_reload_sans_qvd(tmp_path):
    db = tmp_path / "m.db"
    conn = open_db(db)
    EventEngine(conn).ingest(ev("R", EventType.RELOAD_START, 0))
    conn.close()
    assert make_client(db).get("/api/reloads/R/qvd-measures").json() == []


# ---------------------------------------------------------- lecture seule

def test_aucune_route_en_ecriture(empty):
    for route in empty.app.routes:
        methods = getattr(route, "methods", None) or set()
        assert methods <= {"GET", "HEAD"}, route.path


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_methodes_ecriture_refusees(populated, method):
    client, runs = populated
    r = getattr(client, method)(f"/api/reloads/{runs['error'].reload_id}")
    assert r.status_code == 405


def test_connexion_api_refuse_les_ecritures(populated):
    import sqlite3
    client, _ = populated
    with client.app.state.repo.connect() as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly|read-only|query_only"):
            conn.execute("DELETE FROM events")


def test_documentation_openapi(empty):
    paths = empty.get("/openapi.json").json()["paths"]
    for p in ("/health", "/api/reloads/current", "/api/reloads/active", "/api/reloads",
              "/api/reloads/{reload_id}", "/api/reloads/{reload_id}/events",
              "/api/reloads/{reload_id}/qvd-measures", "/api/stream"):
        assert p in paths
