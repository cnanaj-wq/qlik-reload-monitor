"""Tests du champ platform et du schéma v3 (migrations comprises)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.engine import EventEngine
from app.models import EventSource, EventStatus, EventType, Platform, ReloadEvent
from app.storage.db import SCHEMA_VERSION, _migrate_1_to_2, connect, open_db
from app.watcher import QvdWatcher, ReloadContext
from tests.schema_history import SCHEMA_V1

T0 = datetime(2026, 10, 1, 8, 0, 0)
ET = EventType


def ev(et, t=0, rid="R1", **kw) -> ReloadEvent:
    base = dict(reload_id=rid, timestamp=T0 + timedelta(seconds=t),
                source=EventSource.QLIK_LOG, platform=Platform.QLIK_SENSE,
                app_id="guid-ventes", app_name="VENTES", event_type=et,
                status=EventStatus.RUNNING)
    base.update(kw)
    return ReloadEvent(**base)


# ------------------------------------------------------------------ modèle

def test_platform_par_defaut_uniquement_pour_demo():
    e = ev(ET.RELOAD_START, source=EventSource.DEMO, platform=None)
    assert e.platform is Platform.DEMO


@pytest.mark.parametrize("source", [EventSource.QLIK_LOG, EventSource.QVD_WATCHER])
def test_platform_obligatoire_hors_demo(source):
    with pytest.raises(ValidationError, match="platform"):
        ev(ET.RELOAD_START, source=source, platform=None)


@pytest.mark.parametrize("value", ["demo", "qlik_sense", "qlik_view"])
def test_trois_plateformes(value):
    assert ev(ET.RELOAD_START, platform=value).platform.value == value


def test_platform_inconnue_refusee():
    with pytest.raises(ValidationError):
        ev(ET.RELOAD_START, platform="power_bi")


def test_enum_platform():
    assert {p.value for p in Platform} == {"demo", "qlik_sense", "qlik_view"}


# ------------------------------------------------------------------ moteur

def test_reload_qlik_view(tmp_path):
    conn = open_db(tmp_path / "m.db")
    eng = EventEngine(conn)
    eng.ingest(ev(ET.RELOAD_START, platform=Platform.QLIK_VIEW, app_id="VENTES_FINANCE.qvw",
                  app_name="VENTES_FINANCE.qvw"))
    st = eng.get_reload_state("R1")
    assert st.platform == "qlik_view" and st.app_name == "VENTES_FINANCE.qvw"
    assert eng.get_reload_platform("R1") is Platform.QLIK_VIEW
    assert eng.get_reload_platform("absent") is None
    row = conn.execute("SELECT platform FROM events").fetchone()
    assert row["platform"] == "qlik_view"
    conn.close()


def test_contrainte_sql_platform(tmp_path):
    conn = open_db(tmp_path / "m.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO reloads (reload_id, app_id, app_name, source, status,"
                     " started_at, platform) VALUES ('x','a','A','demo','RUNNING','t','tableau')")
    conn.close()


def test_watcher_et_qvd_stable_heritent_de_la_plateforme(tmp_path):
    conn = open_db(tmp_path / "m.db")
    eng = EventEngine(conn, stable_after_measures=2)
    eng.ingest(ev(ET.RELOAD_START))                       # reload Qlik Sense
    qdir = tmp_path / "qvd"
    qdir.mkdir()
    (qdir / "A.qvd").write_bytes(b"\0" * 10)
    events = []
    w = QvdWatcher(eng, [qdir], clock=lambda: T0 + timedelta(seconds=5),
                   on_event=lambda e, r: events.append(e))
    ctx = ReloadContext("R1", "guid-ventes", "VENTES")    # sans plateforme explicite
    w.poll_once(ctx)
    w.poll_once(ctx)
    assert [e.event_type for e in events] == [ET.QVD_SIZE_CHANGE, ET.QVD_STABLE]
    assert {e.platform for e in events} == {Platform.QLIK_SENSE}
    assert {r[0] for r in conn.execute("SELECT platform FROM events")} == {"qlik_sense"}
    conn.close()


def test_watcher_reload_inconnu_sans_plateforme_mesure_seulement(tmp_path):
    conn = open_db(tmp_path / "m.db")
    eng = EventEngine(conn)
    qdir = tmp_path / "qvd"
    qdir.mkdir()
    (qdir / "A.qvd").write_bytes(b"\0")
    obs = QvdWatcher(eng, [qdir]).poll_once(ReloadContext("INCONNU", "a", "A"))
    assert obs[0].measure is not None and obs[0].measure.reload_id is None
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    conn.close()


# --------------------------------------------------------------- migrations

def structure(conn: sqlite3.Connection) -> dict:
    out = {}
    for (table,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        cols = [tuple(r)[1:] for r in conn.execute(f"PRAGMA table_info({table})")]
        idx = sorted(r[1] for r in conn.execute(f"PRAGMA index_list({table})")
                     if not r[1].startswith("sqlite_autoindex"))
        out[table] = (cols, idx)
    return out


def v1_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_V1)
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO reloads (reload_id, app_id, app_name, source, status,"
                 " started_at) VALUES ('R1','a','A','demo','SUCCESS','2026-10-01T10:00:00')")
    for i in range(3):
        conn.execute("INSERT INTO events (seq, event_key, reload_id, timestamp, source,"
                     " app_id, app_name, event_type, status) VALUES (?,?, 'R1', ?, 'demo',"
                     " 'a','A','RELOAD_START','RUNNING')", (5 + i, f"k{i}", f"t{i}"))
    conn.commit()
    conn.close()


def test_base_neuve_et_base_migree_ont_la_meme_structure(tmp_path):
    fresh = open_db(tmp_path / "neuve.db")
    v1_db(tmp_path / "ancienne.db")
    migrated = open_db(tmp_path / "ancienne.db")
    assert structure(migrated) == structure(fresh)
    fresh.close()
    migrated.close()


def test_migration_v2_vers_v3(tmp_path):
    path = tmp_path / "v2.db"
    v1_db(path)
    conn = connect(path)
    _migrate_1_to_2(conn)                       # base au format v2
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    conn.close()

    conn = open_db(path)                        # démarrage : migration 2 -> 3
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3
    assert [tuple(r) for r in conn.execute("SELECT seq, platform FROM events ORDER BY seq")] == [
        (5, "demo"), (6, "demo"), (7, "demo")]
    assert conn.execute("SELECT platform FROM reloads").fetchone()[0] == "demo"
    assert conn.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0] == 0
    conn.close()


def test_migration_echouee_laisse_la_base_intacte(tmp_path, monkeypatch):
    path = tmp_path / "v1.db"
    v1_db(path)
    import app.storage.db as dbmod
    monkeypatch.setattr(dbmod, "_PLATFORM_COLUMN", "platform TEXT NOT NULL")  # invalide
    with pytest.raises(sqlite3.OperationalError):
        open_db(path)
    conn = sqlite3.connect(path)
    # 1 -> 2 a réussi et est conservée ; 2 -> 3 a été annulée entièrement.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reloads)")}
    assert "platform" not in cols
    assert "notification_log" not in {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
