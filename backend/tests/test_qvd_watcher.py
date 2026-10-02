"""Tests du watcher QVD (lecture seule, os.stat)."""

import builtins
from datetime import datetime, timedelta

import pytest

from app.engine import EventEngine
from app.models import EventSource, EventType
from app.storage.db import open_db
from app.watcher import QvdWatcher, ReloadContext

T0 = datetime(2026, 10, 1, 18, 50, 2)
CTX = ReloadContext("R1", "app-1", "Ventes")


class Tick:
    def __init__(self):
        self.t = T0

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture
def env(tmp_path):
    conn = open_db(tmp_path / "m.db")
    engine = EventEngine(conn, stable_after_measures=3)
    qdir = tmp_path / "qvd"
    qdir.mkdir()
    events = []
    w = QvdWatcher(engine, [qdir], clock=Tick(), on_event=lambda e, r: events.append(e))
    from app.models import EventStatus, ReloadEvent
    engine.ingest(ReloadEvent(reload_id="R1", timestamp=T0, source=EventSource.DEMO,
                              app_id="app-1", app_name="Ventes",
                              event_type=EventType.RELOAD_START, status=EventStatus.RUNNING))
    yield engine, w, qdir, events
    conn.close()


def grow(path, n):
    with open(path, "ab") as f:
        f.write(b"\0" * n)


def test_croissance_emet_qvd_size_change(env):
    engine, w, qdir, events = env
    f = qdir / "VENTES.qvd"
    for n in (100, 200, 300):
        grow(f, n)
        w.poll_once(CTX)
    assert [e.qvd_size_bytes for e in events] == [100, 300, 600]
    assert all(e.event_type == EventType.QVD_SIZE_CHANGE for e in events)
    assert all(e.source == EventSource.QVD_WATCHER for e in events)
    deltas = [r[0] for r in engine._conn.execute(
        "SELECT delta_bytes FROM qvd_measures ORDER BY id")]
    assert deltas == [100, 200, 300]


def test_taille_identique_puis_stable(env):
    engine, w, qdir, events = env
    grow(qdir / "A.qvd", 50)
    obs = [w.poll_once(CTX)[0] for _ in range(4)]
    assert [o.changed for o in obs] == [True, False, False, False]
    assert obs[2].measure.is_stable is True
    assert obs[3].measure is None
    # Mesures identiques : pas de QVD_SIZE_CHANGE, mais un QVD_STABLE à la stabilisation.
    assert [e.event_type for e in events] == [EventType.QVD_SIZE_CHANGE, EventType.QVD_STABLE]


def test_ignore_les_autres_extensions_et_dossiers(env):
    engine, w, qdir, events = env
    (qdir / "notes.txt").write_text("x")
    (qdir / "sous.qvd").mkdir()
    grow(qdir / "OK.QVD", 10)
    assert [o.qvd_name for o in w.poll_once(CTX)] == ["OK.QVD"]


def test_sans_contexte_mesure_sans_evenement(env):
    engine, w, qdir, events = env
    grow(qdir / "A.qvd", 10)
    obs = w.poll_once(None)
    assert events == [] and obs[0].measure.reload_id is None


def test_lecture_seule_aucun_open(env, monkeypatch):
    engine, w, qdir, events = env
    grow(qdir / "A.qvd", 10)

    def interdit(*a, **k):
        raise AssertionError("le watcher ne doit jamais ouvrir un fichier")
    monkeypatch.setattr(builtins, "open", interdit)
    w.poll_once(CTX)  # ne lève pas


def test_dossier_inexistant_ne_plante_pas(tmp_path, env):
    engine, _, qdir, _ = env
    grow(qdir / "A.qvd", 10)
    w = QvdWatcher(engine, [tmp_path / "absent", qdir], clock=Tick())
    assert [o.qvd_name for o in w.poll_once(CTX)] == ["A.qvd"]


def test_fichier_supprime_puis_recree(env):
    engine, w, qdir, events = env
    f = qdir / "A.qvd"
    grow(f, 500)
    w.poll_once(CTX)
    f.unlink()
    assert w.poll_once(CTX) == []
    grow(f, 20)
    obs = w.poll_once(CTX)[0]
    assert obs.changed and obs.previous_size is None


def test_stat_en_echec_ignore(env, monkeypatch):
    engine, w, qdir, events = env
    grow(qdir / "A.qvd", 10)
    grow(qdir / "B.qvd", 10)
    import os
    real_stat = os.stat

    def stat(p, *a, **k):
        if str(p).endswith("A.qvd"):
            raise PermissionError("verrouillé")
        return real_stat(p, *a, **k)
    monkeypatch.setattr("app.watcher.qvd_watcher.os.stat", stat)
    assert [o.qvd_name for o in w.poll_once(CTX)] == ["B.qvd"]
