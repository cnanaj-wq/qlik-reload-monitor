"""Tests du moteur d'événements (étape 2)."""

from datetime import datetime, timedelta

import pytest

from app.engine import EventEngine, derive_reload_status
from app.models import EventSource, EventStatus, EventType, ReloadEvent
from app.storage.db import open_db

T0 = datetime(2026, 10, 1, 18, 50, 2)
MB = 1_000_000


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def ev(event_type, t, *, reload_id="R-VENTES", app_id="app-ventes", app_name="Ventes",
       status=EventStatus.RUNNING, source=EventSource.DEMO, **kw) -> ReloadEvent:
    return ReloadEvent(reload_id=reload_id, timestamp=at(t), source=source,
                       app_id=app_id, app_name=app_name, event_type=event_type,
                       status=status, **kw)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "monitor.db"


@pytest.fixture
def engine(db_path):
    conn = open_db(db_path)
    yield EventEngine(conn, stable_after_measures=3)
    conn.close()


def reload_row(engine, rid="R-VENTES"):
    return engine._conn.execute("SELECT * FROM reloads WHERE reload_id=?", (rid,)).fetchone()


def measures(engine, path=None):
    sql = "SELECT * FROM qvd_measures"
    params = ()
    if path:
        sql += " WHERE path = ?"
        params = (path,)
    return engine._conn.execute(sql + " ORDER BY id", params).fetchall()


def nominal_events(rid="R-VENTES", app_id="app-ventes", app_name="Ventes", offset=0.0):
    o = offset
    k = dict(reload_id=rid, app_id=app_id, app_name=app_name)
    S = EventStatus
    return [
        ev(EventType.RELOAD_START, o + 0, **k),
        ev(EventType.SECTION_START, o + 1, section="PARAMETRES", **k),
        ev(EventType.SECTION_END, o + 2.2, section="PARAMETRES", status=S.SUCCESS, **k),
        ev(EventType.SECTION_START, o + 3, section="CLIENTS", **k),
        ev(EventType.TABLE_START, o + 3, section="CLIENTS", table="CLIENTS", **k),
        ev(EventType.TABLE_END, o + 6, section="CLIENTS", table="CLIENTS",
           rows=42_318, status=S.SUCCESS, **k),
        ev(EventType.QVD_WRITE_START, o + 7, section="CLIENTS", qvd="CLIENTS.qvd", **k),
        ev(EventType.QVD_SIZE_CHANGE, o + 8, qvd="CLIENTS.qvd", qvd_size_bytes=12 * MB, **k),
        ev(EventType.QVD_SIZE_CHANGE, o + 9, qvd="CLIENTS.qvd", qvd_size_bytes=46 * MB, **k),
        ev(EventType.QVD_WRITE_END, o + 10, qvd="CLIENTS.qvd", qvd_size_bytes=46 * MB,
           status=S.SUCCESS, **k),
        ev(EventType.SECTION_END, o + 10, section="CLIENTS", status=S.SUCCESS, **k),
        ev(EventType.RELOAD_END, o + 103, status=S.SUCCESS, **k),
    ]


# ------------------------------------------------------------ règle de statut

@pytest.mark.parametrize("ended,end,w,e,attendu", [
    (False, None, 0, 0, EventStatus.RUNNING),
    (False, None, 2, 0, EventStatus.RUNNING),
    (False, None, 0, 1, EventStatus.ERROR),
    (True, EventStatus.SUCCESS, 0, 0, EventStatus.SUCCESS),
    (True, EventStatus.SUCCESS, 1, 0, EventStatus.WARNING),
    (True, EventStatus.WARNING, 0, 0, EventStatus.WARNING),
    (True, EventStatus.SUCCESS, 0, 1, EventStatus.ERROR),
    (True, EventStatus.ERROR, 0, 0, EventStatus.ERROR),
])
def test_regle_statut(ended, end, w, e, attendu):
    assert derive_reload_status(ended=ended, end_status=end,
                                warnings_count=w, errors_count=e) is attendu


# ------------------------------------------------------------- reload nominal

def test_reload_nominal(engine):
    results = [engine.ingest(e) for e in nominal_events()]
    assert all(r.accepted for r in results)
    seqs = [r.seq for r in results]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    r = reload_row(engine)
    assert r["status"] == "SUCCESS"
    assert r["duration_ms"] == 103_000
    assert r["total_rows"] == 42_318
    assert r["warnings_count"] == 0 and r["errors_count"] == 0

    st = engine.get_reload_state("R-VENTES")
    assert st.is_running is False
    assert st.elapsed_ms == 103_000
    assert st.current_qvd == "CLIENTS.qvd"
    assert st.current_qvd_size_bytes == 46 * MB


def test_reload_cree_au_reload_start(engine):
    engine.ingest(ev(EventType.RELOAD_START, 0))
    r = reload_row(engine)
    assert r["status"] == "RUNNING"
    assert r["app_id"] == "app-ventes" and r["app_name"] == "Ventes"
    assert r["ended_at"] is None and r["duration_ms"] is None


def test_etat_courant_en_cours(engine):
    for e in nominal_events()[:6]:
        engine.ingest(e)
    engine.ingest(ev(EventType.SECTION_START, 14, section="VENTES"))
    engine.ingest(ev(EventType.TABLE_START, 15, section="VENTES", table="VENTES"))
    engine.ingest(ev(EventType.TABLE_PROGRESS, 35, section="VENTES", table="VENTES",
                     rows=328_543))

    st = engine.get_reload_state("R-VENTES", now=at(40))
    assert st.is_running and st.status is EventStatus.RUNNING
    assert st.elapsed_ms == 40_000
    assert st.current_section == "VENTES"
    assert st.current_table == "VENTES"
    assert st.current_rows == 328_543
    assert st.current_step == "TABLE_PROGRESS VENTES"
    assert st.total_rows == 42_318  # seules les tables terminées comptent


def test_clock_injectable(db_path):
    conn = open_db(db_path)
    eng = EventEngine(conn, clock=lambda: at(12.5))
    eng.ingest(ev(EventType.RELOAD_START, 0))
    assert eng.get_reload_state("R-VENTES").elapsed_ms == 12_500
    conn.close()


# ------------------------------------------------------------------ doublons

def test_evenement_duplique(engine):
    e = ev(EventType.TABLE_END, 6, section="CLIENTS", table="CLIENTS", rows=42_318,
           status=EventStatus.SUCCESS)
    engine.ingest(ev(EventType.RELOAD_START, 0))
    first = engine.ingest(e)
    second = engine.ingest(e.model_copy())
    assert first.accepted and first.seq is not None
    assert not second.accepted and second.reason == "duplicate" and second.seq is None
    assert engine._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    assert reload_row(engine)["total_rows"] == 42_318  # pas compté deux fois


def test_warning_duplique_compte_une_fois(engine):
    w = ev(EventType.WARNING, 5, message="Champ synthétique créé")
    engine.ingest(ev(EventType.RELOAD_START, 0))
    engine.ingest(w)
    engine.ingest(w)
    assert reload_row(engine)["warnings_count"] == 1


def test_qvd_size_change_duplique_ne_cree_pas_de_mesure(engine):
    e = ev(EventType.QVD_SIZE_CHANGE, 8, qvd="CLIENTS.qvd", qvd_size_bytes=12 * MB)
    engine.ingest(ev(EventType.RELOAD_START, 0))
    engine.ingest(e)
    engine.ingest(e)
    assert len(measures(engine)) == 1


# ---------------------------------------------------------- warning / erreur

def test_warning(engine):
    engine.ingest(ev(EventType.RELOAD_START, 0))
    engine.ingest(ev(EventType.WARNING, 5, message="Clé synthétique $Syn 1"))
    st = engine.get_reload_state("R-VENTES", now=at(6))
    assert st.status is EventStatus.RUNNING  # en cours : warning visible via compteur
    assert st.warnings_count == 1
    assert st.last_message == "Clé synthétique $Syn 1"

    engine.ingest(ev(EventType.RELOAD_END, 20, status=EventStatus.SUCCESS))
    st = engine.get_reload_state("R-VENTES")
    assert st.status is EventStatus.WARNING
    assert st.elapsed_ms == 20_000


def test_erreur(engine):
    engine.ingest(ev(EventType.RELOAD_START, 0))
    engine.ingest(ev(EventType.TABLE_START, 2, section="VENTES", table="VENTES"))
    engine.ingest(ev(EventType.ERROR, 9, section="VENTES", table="VENTES",
                     status=EventStatus.ERROR, message="ORA-01017: invalid username"))
    st = engine.get_reload_state("R-VENTES", now=at(10))
    assert st.status is EventStatus.ERROR and st.is_running
    assert st.errors_count == 1
    assert st.current_step == "TABLE_START VENTES"  # l'erreur ne remplace pas l'étape

    engine.ingest(ev(EventType.RELOAD_END, 11, status=EventStatus.ERROR,
                     message="Reload failed"))
    r = reload_row(engine)
    assert r["status"] == "ERROR" and r["duration_ms"] == 11_000


def test_statut_jamais_adouci_par_un_end_success(engine):
    engine.ingest(ev(EventType.RELOAD_START, 0))
    engine.ingest(ev(EventType.ERROR, 3, status=EventStatus.ERROR, message="boom"))
    engine.ingest(ev(EventType.RELOAD_END, 4, status=EventStatus.SUCCESS))
    assert reload_row(engine)["status"] == "ERROR"


# ------------------------------------------------------------- mesures QVD

def measure(engine, t, size, path="/qvd/VENTES.qvd", rid=None):
    return engine.record_qvd_measure(timestamp=at(t), qvd_name="VENTES.qvd",
                                     path=path, size_bytes=size, reload_id=rid)


def test_qvd_qui_grossit(engine):
    sizes = [80 * MB, 156 * MB, 247 * MB, 318 * MB]
    out = [measure(engine, i, s) for i, s in enumerate(sizes)]
    assert [m.delta_bytes for m in out] == [80 * MB, 76 * MB, 91 * MB, 71 * MB]
    assert all(m.is_stable is False for m in out)
    rows = measures(engine)
    assert [r["size_bytes"] for r in rows] == sizes
    assert all(r["is_stable"] == 0 for r in rows)


def test_qvd_stabilise_apres_n_mesures_identiques(engine):
    assert measure(engine, 0, 318 * MB) is not None          # 1re observation
    assert measure(engine, 1, 318 * MB) is None              # 2 identiques
    stable = measure(engine, 2, 318 * MB)                    # 3 identiques -> stable
    assert stable.is_stable is True and stable.delta_bytes == 0
    assert measure(engine, 3, 318 * MB) is None              # pas de doublon de stabilité
    rows = measures(engine)
    assert [(r["size_bytes"], r["is_stable"]) for r in rows] == [(318 * MB, 0), (318 * MB, 1)]


def test_qvd_repart_en_ecriture_apres_stabilisation(engine):
    for t in range(3):
        measure(engine, t, 10 * MB)
    m = measure(engine, 5, 12 * MB)
    assert m.is_stable is False and m.delta_bytes == 2 * MB


def test_qvd_qui_retrecit(engine):
    measure(engine, 0, 291 * MB)
    m = measure(engine, 1, 5 * MB)  # QVD réécrit depuis zéro
    assert m.delta_bytes == -286 * MB and m.is_stable is False
    assert measures(engine)[-1]["delta_bytes"] == -286 * MB


def test_min_delta_ignore_les_micro_variations(db_path):
    conn = open_db(db_path)
    eng = EventEngine(conn, stable_after_measures=2, min_delta_bytes=1024)
    eng.record_qvd_measure(timestamp=at(0), qvd_name="A.qvd", path="A", size_bytes=10_000)
    assert eng.record_qvd_measure(timestamp=at(1), qvd_name="A.qvd", path="A",
                                  size_bytes=10_100).is_stable is True
    conn.close()


def test_suivi_independant_par_fichier(engine):
    measure(engine, 0, 1 * MB, path="/qvd/A.qvd")
    measure(engine, 0, 9 * MB, path="/qvd/B.qvd")
    m = measure(engine, 1, 2 * MB, path="/qvd/A.qvd")
    assert m.delta_bytes == 1 * MB


def test_taille_negative_refusee(engine):
    with pytest.raises(ValueError):
        measure(engine, 0, -1)


def test_mesure_rattachee_a_reload_inconnu_est_detachee(engine):
    m = measure(engine, 0, 1 * MB, rid="INCONNU")
    assert m.reload_id is None


def test_etat_courant_utilise_la_derniere_mesure(engine):
    engine.ingest(ev(EventType.RELOAD_START, 0))
    engine.ingest(ev(EventType.QVD_WRITE_START, 1, qvd="VENTES.qvd",
                     qvd_path="/qvd/VENTES.qvd"))
    engine.ingest(ev(EventType.QVD_SIZE_CHANGE, 2, qvd="VENTES.qvd",
                     qvd_path="/qvd/VENTES.qvd", qvd_size_bytes=80 * MB))
    measure(engine, 3, 156 * MB, rid="R-VENTES")  # mesure venant du watcher
    st = engine.get_reload_state("R-VENTES", now=at(3))
    assert st.current_qvd == "VENTES.qvd"
    assert st.current_qvd_size_bytes == 156 * MB
    assert st.current_qvd_is_stable is False


# ----------------------------------------------------------- ordre perturbé

def test_qvd_writing_pendant_et_apres_ecriture(engine):
    events = nominal_events()
    for e in events[:8]:   # ... QVD_WRITE_START, 1re taille
        engine.ingest(e)
    st = engine.get_reload_state("R-VENTES", now=at(8))
    assert st.current_qvd == "CLIENTS.qvd" and st.current_qvd_writing is True

    for e in events[8:11]:  # ... QVD_WRITE_END
        engine.ingest(e)
    engine.ingest(ev(EventType.TABLE_START, 15, section="VENTES", table="VENTES"))
    st = engine.get_reload_state("R-VENTES", now=at(16))
    assert st.current_qvd == "CLIENTS.qvd"        # dernier QVD connu...
    assert st.current_qvd_writing is False          # ...mais plus en écriture


def test_qvd_writing_faux_apres_fin_de_reload(engine):
    engine.ingest(ev(EventType.RELOAD_START, 0))
    engine.ingest(ev(EventType.QVD_WRITE_START, 1, qvd="X.qvd"))
    engine.ingest(ev(EventType.RELOAD_END, 2, status=EventStatus.ERROR, message="stop"))
    assert engine.get_reload_state("R-VENTES").current_qvd_writing is False


def test_evenements_dans_le_mauvais_ordre(engine):
    events = nominal_events()
    reordered = [events[5], events[-1], events[3]] + events[:3] + events[4:5] + events[6:-1]
    for e in reordered:
        assert engine.ingest(e).accepted

    r = reload_row(engine)
    assert r["started_at"] == at(0).isoformat(timespec="microseconds")
    assert r["duration_ms"] == 103_000
    assert r["status"] == "SUCCESS"
    assert r["total_rows"] == 42_318


def test_meme_resultat_quel_que_soit_l_ordre(tmp_path):
    def run(events, name):
        conn = open_db(tmp_path / name)
        eng = EventEngine(conn)
        for e in events:
            eng.ingest(e)
        row = dict(eng._conn.execute(
            "SELECT status, started_at, ended_at, duration_ms, total_rows,"
            " warnings_count, errors_count FROM reloads").fetchone())
        st = eng.get_reload_state("R-VENTES").to_dict()
        conn.close()
        st.pop("last_seq")
        return row, st

    events = nominal_events() + [ev(EventType.WARNING, 50, message="w")]
    assert run(events, "a.db") == run(list(reversed(events)), "b.db")


def test_evenement_avant_reload_start(engine):
    engine.ingest(ev(EventType.SECTION_START, 5, section="CLIENTS"))
    assert reload_row(engine)["started_at"] == at(5).isoformat(timespec="microseconds")
    engine.ingest(ev(EventType.RELOAD_START, 0))
    assert reload_row(engine)["started_at"] == at(0).isoformat(timespec="microseconds")


def test_evenement_tardif_apres_reload_end(engine):
    for e in nominal_events():
        engine.ingest(e)
    engine.ingest(ev(EventType.WARNING, 50, message="log arrivé en retard"))
    assert reload_row(engine)["status"] == "WARNING"


# --------------------------------------------------------------- redémarrage

def test_redemarrage_reconstruction_depuis_sqlite(db_path):
    conn = open_db(db_path)
    eng = EventEngine(conn, stable_after_measures=3)
    events = nominal_events()[:-1]  # reload encore en cours
    for e in events:
        eng.ingest(e)
    eng.record_qvd_measure(timestamp=at(11), qvd_name="CLIENTS.qvd",
                           path="CLIENTS.qvd", size_bytes=46 * MB, reload_id="R-VENTES")
    before = eng.get_reload_state("R-VENTES", now=at(60)).to_dict()
    conn.close()

    conn2 = open_db(db_path)
    eng2 = EventEngine(conn2, stable_after_measures=3)
    after = eng2.get_reload_state("R-VENTES", now=at(60)).to_dict()
    assert after == before

    # La déduplication survit au redémarrage (relecture d'un log déjà traité).
    assert all(not eng2.ingest(e).accepted for e in events)

    # Suivi QVD : seule la dernière mesure STOCKÉE survit au redémarrage.
    # Les observations identiques non stockées sont perdues : le compteur repart
    # à 1. Choix prudent : la stabilité peut être retardée, jamais anticipée.
    def obs(t):
        return eng2.record_qvd_measure(timestamp=at(t), qvd_name="CLIENTS.qvd",
                                       path="CLIENTS.qvd", size_bytes=46 * MB)
    assert obs(12) is None
    stable = obs(13)
    assert stable.is_stable is True and stable.delta_bytes == 0
    grow = eng2.record_qvd_measure(timestamp=at(14), qvd_name="CLIENTS.qvd",
                                   path="CLIENTS.qvd", size_bytes=47 * MB)
    assert grow.delta_bytes == 1 * MB  # delta calculé depuis la taille d'avant redémarrage

    # Et la fin du reload est correctement prise en compte.
    eng2.ingest(nominal_events()[-1])
    assert eng2.get_reload_state("R-VENTES").status is EventStatus.SUCCESS
    conn2.close()


def test_redemarrage_qvd_stable_reste_stable(db_path):
    conn = open_db(db_path)
    eng = EventEngine(conn, stable_after_measures=2)
    for t in range(2):
        eng.record_qvd_measure(timestamp=at(t), qvd_name="A.qvd", path="A", size_bytes=5)
    conn.close()

    conn2 = open_db(db_path)
    eng2 = EventEngine(conn2, stable_after_measures=2)
    assert eng2.record_qvd_measure(timestamp=at(5), qvd_name="A.qvd", path="A",
                                   size_bytes=5) is None
    m = eng2.record_qvd_measure(timestamp=at(6), qvd_name="A.qvd", path="A", size_bytes=8)
    assert m.delta_bytes == 3
    conn2.close()


# ------------------------------------------------- reloads simultanés

def test_deux_reloads_simultanes(engine):
    a = nominal_events("R-VENTES", "app-ventes", "Ventes", offset=0)
    b = nominal_events("R-STOCKS", "app-stocks", "Stocks", offset=1.5)
    # Interclassement des deux flux, comme dans la réalité.
    for e in sorted(a[:-1] + b[:6], key=lambda x: x.timestamp):
        assert engine.ingest(e).accepted

    actifs = engine.get_active_states(now=at(30))
    assert {s.reload_id for s in actifs} == {"R-VENTES", "R-STOCKS"}
    assert actifs[0].reload_id == "R-STOCKS"  # le plus récent d'abord

    ventes = engine.get_reload_state("R-VENTES", now=at(30))
    stocks = engine.get_reload_state("R-STOCKS", now=at(30))
    assert ventes.current_qvd == "CLIENTS.qvd" and stocks.current_qvd is None
    assert ventes.elapsed_ms == 30_000 and stocks.elapsed_ms == 28_500
    assert engine.get_current_state(app_id="app-ventes", now=at(30)).reload_id == "R-VENTES"

    engine.ingest(a[-1])
    assert [s.reload_id for s in engine.get_active_states(now=at(200))] == ["R-STOCKS"]
    assert engine.get_current_state(now=at(200)).reload_id == "R-STOCKS"


def test_meme_qvd_deux_reloads_isoles_par_reload_id(engine):
    engine.ingest(ev(EventType.RELOAD_START, 0, reload_id="R1", app_id="a1"))
    engine.ingest(ev(EventType.RELOAD_START, 0, reload_id="R2", app_id="a2"))
    for rid, size in (("R1", 10 * MB), ("R2", 20 * MB)):
        engine.ingest(ev(EventType.QVD_SIZE_CHANGE, 1, reload_id=rid, app_id=rid,
                         qvd="SHARED.qvd", qvd_path=f"/{rid}/SHARED.qvd",
                         qvd_size_bytes=size))
    assert engine.get_reload_state("R1").current_qvd_size_bytes == 10 * MB
    assert engine.get_reload_state("R2").current_qvd_size_bytes == 20 * MB


# ---------------------------------------------------------------- divers

def test_etat_reload_inconnu(engine):
    assert engine.get_reload_state("absent") is None
    assert engine.get_current_state() is None


def test_courant_sans_reload_actif_retourne_le_dernier(engine):
    for e in nominal_events():
        engine.ingest(e)
    st = engine.get_current_state()
    assert st.reload_id == "R-VENTES" and st.is_running is False


def test_extra_conserve(engine):
    engine.ingest(ev(EventType.RELOAD_START, 0, extra={"log_line": 12}))
    raw = engine._conn.execute("SELECT extra_json FROM events").fetchone()[0]
    assert '"log_line": 12' in raw


def test_parametre_stabilite_invalide(db_path):
    conn = open_db(db_path)
    with pytest.raises(ValueError):
        EventEngine(conn, stable_after_measures=1)
    conn.close()
