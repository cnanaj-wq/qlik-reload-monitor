"""Tests de l'événement QVD_STABLE et de la migration de schéma v1 -> v2."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.engine import EventEngine
from app.models import EventSource, EventStatus, EventType, Platform, ReloadEvent
from app.storage.db import SCHEMA_VERSION, open_db
from tests.schema_history import SCHEMA_V1
from tests.test_demo_simulator import run_scenario
from tests.test_sse import LiveServer, SseClient, ids

T0 = datetime(2026, 10, 1, 18, 50, 0)
PATH = "D:/demo/qvd/VENTES.qvd"
ET = EventType


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def ev(et, t, **kw) -> ReloadEvent:
    base = dict(reload_id="R1", timestamp=at(t), source=EventSource.DEMO,
                app_id="app-ventes", app_name="Ventes", event_type=et,
                status=EventStatus.RUNNING)
    base.update(kw)
    return ReloadEvent(**base)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "m.db"


@pytest.fixture
def engine(db):
    conn = open_db(db)
    eng = EventEngine(conn, stable_after_measures=3)
    eng.ingest(ev(ET.RELOAD_START, 0))
    yield eng
    conn.close()


def observe(eng, t, size, rid="R1"):
    """Une observation du watcher (taille lue sur disque)."""
    return eng.record_qvd_measure(timestamp=at(t), qvd_name="VENTES.qvd", path=PATH,
                                  size_bytes=size, reload_id=rid, source="qvd_watcher")


def write_start(eng, t):
    return eng.ingest(ev(ET.QVD_WRITE_START, t, qvd="VENTES.qvd", qvd_path=PATH))


def stable_rows(eng):
    return eng._conn.execute(
        "SELECT * FROM events WHERE event_type = 'QVD_STABLE' ORDER BY seq").fetchall()


# -------------------------------------------------------------- modèle

def test_qvd_stable_exige_qvd_et_taille():
    with pytest.raises(ValidationError, match="qvd"):
        ev(ET.QVD_STABLE, 0, qvd_size_bytes=10)
    with pytest.raises(ValidationError, match="qvd_size_bytes"):
        ev(ET.QVD_STABLE, 0, qvd="A.qvd")


# ------------------------------------------------------- émission unique

def test_emission_unique(engine):
    write_start(engine, 1)
    observe(engine, 2, 100)
    observe(engine, 3, 250)
    observe(engine, 4, 250)
    m = observe(engine, 5, 250)          # 3e mesure identique -> stable
    assert m.is_stable and m.stable_event is not None
    for t in range(6, 15):               # le fichier ne bouge plus
        assert observe(engine, t, 250) is None
    assert len(stable_rows(engine)) == 1


def test_presence_dans_sqlite_et_contenu(engine):
    write_start(engine, 1)
    for t, size in ((2, 100), (3, 250), (4, 250)):
        observe(engine, t, size)
    m = observe(engine, 5, 250)
    [row] = stable_rows(engine)
    assert row["seq"] == m.stable_event.seq and row["seq"] is not None
    assert row["reload_id"] == "R1"
    assert row["timestamp"] == at(5).isoformat(timespec="microseconds")
    assert row["source"] == "qvd_watcher"
    assert (row["app_id"], row["app_name"]) == ("app-ventes", "Ventes")
    assert row["event_type"] == "QVD_STABLE" and row["status"] == "SUCCESS"
    assert (row["qvd_name"], row["qvd_path"], row["qvd_size_bytes"]) == ("VENTES.qvd", PATH, 250)
    # Cohérence avec la mesure stable écrite dans la même transaction.
    measure = engine._conn.execute(
        "SELECT * FROM qvd_measures WHERE is_stable = 1").fetchone()
    assert json.loads(row["extra_json"]) == {"measure_id": measure["id"]}
    assert measure["timestamp"] == row["timestamp"]


def test_etat_courant_apres_stabilisation(engine):
    write_start(engine, 1)
    for t in (2, 3, 4):
        observe(engine, t, 80)
    st = engine.get_reload_state("R1", now=at(5))
    assert st.current_step == "QVD_STABLE VENTES.qvd"
    assert st.current_qvd_is_stable is True and st.current_qvd_writing is False


# ------------------------------------------------------- absence de doublon

def test_pas_de_doublon_apres_redemarrage(db, engine):
    write_start(engine, 1)
    for t in (2, 3, 4):
        observe(engine, t, 80)
    engine._conn.close()

    conn = open_db(db)                       # redémarrage du moteur
    eng2 = EventEngine(conn, stable_after_measures=3)
    for t in range(5, 12):
        assert observe(eng2, t, 80) is None  # déjà stable : rien de réémis
    assert len(stable_rows(eng2)) == 1
    conn.close()


def test_pas_de_doublon_si_meme_evenement_reinjecte(engine):
    write_start(engine, 1)
    for t in (2, 3, 4):
        observe(engine, t, 80)
    stable = stable_rows(engine)[0]
    replay = ev(ET.QVD_STABLE, 4, source=EventSource.QVD_WATCHER, platform=Platform.DEMO,
                qvd="VENTES.qvd",
                qvd_path=PATH, qvd_size_bytes=80, status=EventStatus.SUCCESS,
                extra={"measure_id": json.loads(stable["extra_json"])["measure_id"]})
    assert engine.ingest(replay).accepted is False
    assert len(stable_rows(engine)) == 1


def test_sans_reload_aucun_evenement(engine):
    for t in (1, 2, 3):
        m = observe(engine, t, 10, rid=None)
    assert m.is_stable and m.stable_event is None
    assert stable_rows(engine) == []


# ------------------------------------------- nouvelle stabilisation

def test_nouvelle_stabilisation_apres_write_start(engine):
    write_start(engine, 1)
    for t in (2, 3, 4):
        observe(engine, t, 300)
    write_start(engine, 10)                  # nouveau STORE du même fichier
    observe(engine, 11, 100)
    for t in (12, 13, 14):
        observe(engine, t, 320)
    rows = stable_rows(engine)
    assert [r["qvd_size_bytes"] for r in rows] == [300, 320]
    assert rows[0]["seq"] < rows[1]["seq"]


def test_write_start_rearme_meme_si_taille_identique(engine):
    write_start(engine, 1)
    for t in (2, 3, 4):
        observe(engine, t, 300)
    write_start(engine, 10)                  # réécrit à l'identique
    for t in (11, 12, 13):
        observe(engine, t, 300)
    assert len(stable_rows(engine)) == 2


def test_variation_de_taille_rearme_aussi(engine):
    """Choix documenté : en mode LIVE, QVD_WRITE_START peut manquer dans les
    logs ; une nouvelle variation de taille ouvre donc aussi un nouvel épisode."""
    write_start(engine, 1)
    for t in (2, 3, 4):
        observe(engine, t, 300)
    observe(engine, 20, 450)
    for t in (21, 22):
        observe(engine, t, 450)
    assert [r["qvd_size_bytes"] for r in stable_rows(engine)] == [300, 450]


# ------------------------------------------------------------ simulateur

def test_simulateur_un_qvd_stable_par_qvd(tmp_path):
    run = run_scenario(tmp_path, "successful")
    events = [e for e, _ in run.events]
    stables = [e for e in events if e.event_type == ET.QVD_STABLE]
    assert [e.qvd for e in stables] == ["CLIENTS.qvd", "PRODUITS.qvd", "VENTES.qvd"]
    assert all(e.source == EventSource.QVD_WATCHER and e.seq for e in stables)
    for e in stables:
        i_stable = events.index(e)
        i_end = next(i for i, x in enumerate(events)
                     if x.event_type == ET.QVD_WRITE_END and x.qvd == e.qvd)
        assert i_end < i_stable
        assert e.qvd_size_bytes == (run.qvd_dir / e.qvd).stat().st_size


def test_simulateur_scenario_erreur(tmp_path):
    run = run_scenario(tmp_path, "error")
    assert [e.qvd for e, _ in run.events if e.event_type == ET.QVD_STABLE] == ["CLIENTS.qvd"]


# ---------------------------------------------------------------- SSE

def test_qvd_stable_dans_le_flux_sse(tmp_path):
    run_scenario(tmp_path, "successful")
    with LiveServer(tmp_path / "monitor.db") as srv:
        c = SseClient(f"{srv.url}/api/stream", params={"after_seq": 0})
        msgs = c.read_until(lambda ms: sum(
            "data" in m and '"QVD_STABLE"' in m["data"] for m in ms) >= 3)
        c.close()
    stables = [json.loads(m["data"]) for m in msgs
               if "data" in m and '"QVD_STABLE"' in m["data"]]
    assert [s["qvd"] for s in stables] == ["CLIENTS.qvd", "PRODUITS.qvd", "VENTES.qvd"]
    for s, m in zip(stables, [m for m in msgs if "data" in m and '"QVD_STABLE"' in m["data"]]):
        assert int(m["id"]) == s["seq"]
        assert s["source"] == "qvd_watcher" and s["status"] == "SUCCESS"
        assert s["qvd_path"] and s["qvd_size_bytes"] > 0
    all_ids = ids([m for m in msgs if "id" in m])
    assert all_ids == sorted(all_ids)


def test_reprise_sse_incluant_qvd_stable(db):
    conn = open_db(db)
    eng = EventEngine(conn, stable_after_measures=3)
    eng.ingest(ev(ET.RELOAD_START, 0))
    write_start(eng, 1)
    observe(eng, 2, 500)                      # 1re mesure (pas d'événement)
    conn.close()

    with LiveServer(db) as srv:
        first = SseClient(f"{srv.url}/api/stream", params={"after_seq": 0})
        got = ids(first.read_events(2))[:2]   # RELOAD_START, QVD_WRITE_START
        first.close()                          # déconnexion

        # Pendant la coupure : le fichier se stabilise, puis un autre événement.
        conn = open_db(db)
        eng = EventEngine(conn, stable_after_measures=3)
        observe(eng, 3, 500)
        observe(eng, 4, 500)                   # -> QVD_STABLE
        eng.ingest(ev(ET.QVD_WRITE_END, 5, qvd="VENTES.qvd", qvd_path=PATH,
                      status=EventStatus.SUCCESS))
        conn.close()

        second = SseClient(f"{srv.url}/api/stream", headers={"Last-Event-ID": str(got[-1])})
        rest = second.read_events(2)
        second.close()

    types = [json.loads(m["data"])["event_type"] for m in rest]
    assert types == ["QVD_STABLE", "QVD_WRITE_END"]
    assert got + ids(rest) == list(range(1, 5))   # ni perte, ni doublon


# --------------------------------------------------- migration de schéma

def make_v1_db(path, n_events=5):
    """Base au format v1 (sans QVD_STABLE dans la contrainte), avec des données."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_V1)
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO reloads (reload_id, app_id, app_name, source, status,"
                 " started_at) VALUES ('R1','a','A','demo','RUNNING','2026-10-01T10:00:00')")
    for i in range(n_events):
        conn.execute(
            "INSERT INTO events (seq, event_key, reload_id, timestamp, source, app_id,"
            " app_name, event_type, status) VALUES (?, ?, 'R1', ?, 'demo', 'a', 'A',"
            " 'RELOAD_START', 'RUNNING')", (10 + i * 10, f"k{i}", f"2026-10-01T10:00:0{i}"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO events (event_key, reload_id, timestamp, source, app_id,"
                     " app_name, event_type, status) VALUES ('x','R1','t','demo','a','A',"
                     " 'QVD_STABLE','SUCCESS')")
    conn.commit()
    conn.close()


def test_migration_v1_vers_version_courante(tmp_path):
    path = tmp_path / "v1.db"
    make_v1_db(path)
    conn = open_db(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3
    seqs = [r[0] for r in conn.execute("SELECT seq FROM events ORDER BY seq")]
    assert seqs == [10, 20, 30, 40, 50]                     # seq conservés
    idx = {r[1] for r in conn.execute("PRAGMA index_list(events)")}
    assert "ix_events_reload_seq" in idx
    assert "events_v1" not in {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    # QVD_STABLE accepté, et la numérotation continue après le max existant.
    eng = EventEngine(conn)
    res = eng.ingest(ReloadEvent(
        reload_id="R1", timestamp=at(9), source=EventSource.QVD_WATCHER,
        platform=Platform.DEMO, app_id="a",
        app_name="A", event_type=ET.QVD_STABLE, status=EventStatus.SUCCESS,
        qvd="X.qvd", qvd_size_bytes=1))
    assert res.accepted and res.seq == 51
    conn.close()


def test_migration_idempotente(tmp_path):
    path = tmp_path / "v1.db"
    make_v1_db(path)
    open_db(path).close()
    conn = open_db(path)  # second démarrage : aucune migration rejouée
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 5
    conn.close()
