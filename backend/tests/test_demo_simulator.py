"""Tests du simulateur DEMO (étape 3) : scénarios complets sur horloge virtuelle."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest

from app.demo import ReloadSimulator, VirtualClock, load_scenario, prepare_demo_dir
from app.demo.scenario import Scenario
from app.engine import EventEngine
from app.models import EventSource, EventStatus, EventType
from app.storage.db import open_db
from app.watcher import QvdWatcher

T0 = datetime(2026, 10, 1, 18, 50, 2)
SCALE = 0.01  # tailles x0.01 : tests rapides, mêmes mécanismes
MIB = 1024 * 1024
ET = EventType


@dataclass
class Run:
    engine: EventEngine
    reload_id: str
    qvd_dir: Path
    clock: VirtualClock
    events: list = field(default_factory=list)

    def types(self):
        return [e.event_type for e, _ in self.events]

    def demo_events(self):
        return [e for e, _ in self.events if e.source == EventSource.DEMO]

    def measures(self, qvd=None):
        sql, params = "SELECT * FROM qvd_measures WHERE reload_id = ?", [self.reload_id]
        if qvd:
            sql += " AND qvd_name = ?"
            params.append(qvd)
        return self.engine._conn.execute(sql + " ORDER BY id", params).fetchall()

    def state(self):
        return self.engine.get_reload_state(self.reload_id)


def run_scenario(tmp_path, scenario, *, speed=1.0, conn=None, stable_after=3) -> Run:
    if isinstance(scenario, str):
        scenario = load_scenario(scenario)
    conn = conn or open_db(tmp_path / "monitor.db")
    clock = VirtualClock(T0, speed)
    engine = EventEngine(conn, stable_after_measures=stable_after, clock=clock.now)
    qvd_dir, _ = prepare_demo_dir(tmp_path / "demo_data")
    events = []
    rec = lambda e, r: events.append((e, r))  # noqa: E731
    watcher = QvdWatcher(engine, [qvd_dir], clock=clock.now, on_event=rec)
    sim = ReloadSimulator(engine, scenario, qvd_dir=qvd_dir, clock=clock, watcher=watcher,
                          polling_interval_seconds=1, stable_after_measures=stable_after,
                          size_scale=SCALE, on_event=rec)
    rid = sim.run()
    return Run(engine, rid, qvd_dir, clock, events)


# --------------------------------------------------------- scénario réussi

def test_scenario_reussi_complet(tmp_path):
    run = run_scenario(tmp_path, "successful")
    st = run.state()
    assert st.status is EventStatus.SUCCESS and not st.is_running
    assert st.warnings_count == 0 and st.errors_count == 0
    assert st.total_rows == 42_318 + 8_426 + 1_842_556
    assert all(r.accepted for _, r in run.events)

    produits = {e for e in ET}
    assert produits <= set(run.types()) | {ET.WARNING, ET.ERROR}

    sections = [e.section for e in run.demo_events() if e.event_type == ET.SECTION_START]
    assert sections == ["PARAMETRES", "DIMENSIONS", "FAITS"]
    stored = [e.qvd for e in run.demo_events() if e.event_type == ET.QVD_WRITE_END]
    assert stored == ["CLIENTS.qvd", "PRODUITS.qvd", "VENTES.qvd"]


def test_tous_les_types_d_evenements_sont_produits(tmp_path):
    types = set()
    for name in ("successful", "slow", "error"):
        types |= set(run_scenario(tmp_path / name, name).types())
    assert types == set(ET)


def test_duree_et_facteur_de_vitesse(tmp_path):
    normal = run_scenario(tmp_path / "x1", "successful", speed=1)
    rapide = run_scenario(tmp_path / "x10", "successful", speed=10)
    d1, d10 = normal.state().elapsed_ms, rapide.state().elapsed_ms
    assert d1 > 50_000                       # ~1 minute à vitesse réelle
    assert d10 == pytest.approx(d1 / 10, abs=5)


# ---------------------------------------------------------- scénario lent

def test_scenario_lent(tmp_path):
    ok = run_scenario(tmp_path / "ok", "successful")
    slow = run_scenario(tmp_path / "slow", "slow")

    def ventes_ms(run):
        evs = {e.event_type: e.timestamp for e in run.demo_events()
               if e.table == "VENTES" and e.event_type in (ET.TABLE_START, ET.TABLE_END)}
        return (evs[ET.TABLE_END] - evs[ET.TABLE_START]).total_seconds() * 1000

    assert ventes_ms(slow) > 2 * ventes_ms(ok)
    assert slow.state().elapsed_ms > ok.state().elapsed_ms + 40_000
    assert slow.state().status is EventStatus.WARNING
    assert slow.state().warnings_count == 1


# ------------------------------------------------------ scénario en erreur

def test_scenario_en_erreur(tmp_path):
    run = run_scenario(tmp_path, "error")
    st = run.state()
    assert st.status is EventStatus.ERROR and st.errors_count == 1
    assert "ORA-00942" in st.last_message
    assert st.current_table == "VENTES"
    # Rien après l'erreur : pas de TABLE_END VENTES, pas d'AGREGATS, pas de VENTES.qvd.
    demo = run.demo_events()
    assert not any(e.table == "VENTES" and e.event_type == ET.TABLE_END for e in demo)
    assert "AGREGATS" not in {e.section for e in demo}
    assert sorted(p.name for p in run.qvd_dir.iterdir()) == ["CLIENTS.qvd"]
    assert run.types()[-2:] == [ET.ERROR, ET.RELOAD_END]


def test_erreur_correctement_propagee(tmp_path):
    run = run_scenario(tmp_path, "error")
    err = [e for e in run.demo_events() if e.event_type == ET.ERROR][0]
    assert err.status is EventStatus.ERROR
    assert (err.section, err.table) == ("FAITS", "VENTES")
    assert err.rows == round(1_842_556 * 2 / 5)  # 40 % atteints avant l'échec
    end = run.demo_events()[-1]
    assert end.event_type == ET.RELOAD_END and end.status is EventStatus.ERROR
    row = run.engine._conn.execute(
        "SELECT status, errors_count FROM reloads WHERE reload_id=?", (run.reload_id,)).fetchone()
    assert tuple(row) == ("ERROR", 1)


def test_warning_correctement_propage(tmp_path):
    run = run_scenario(tmp_path, "slow")
    w = [e for e in run.demo_events() if e.event_type == ET.WARNING]
    assert len(w) == 1
    assert w[0].status is EventStatus.WARNING and "$Syn 1" in w[0].message
    assert (w[0].section, w[0].table) == ("DIMENSIONS", "PRODUITS")
    sec_end = [e for e in run.demo_events()
               if e.event_type == ET.SECTION_END and e.section == "DIMENSIONS"][0]
    assert sec_end.status is EventStatus.WARNING
    assert run.demo_events()[-1].status is EventStatus.WARNING  # RELOAD_END


def test_warning_de_section(tmp_path):
    run = run_scenario(tmp_path, "error")
    w = [e for e in run.demo_events() if e.event_type == ET.WARNING][0]
    assert w.section == "PARAMETRES" and w.table is None


# ------------------------------------------------------ statuts finaux

@pytest.mark.parametrize("name,attendu", [
    ("successful", EventStatus.SUCCESS),
    ("slow", EventStatus.WARNING),
    ("error", EventStatus.ERROR),
])
def test_statut_final(tmp_path, name, attendu):
    assert run_scenario(tmp_path, name).state().status is attendu


# ---------------------------------------------------------- ordre

def test_evenements_dans_le_bon_ordre(tmp_path):
    run = run_scenario(tmp_path, "successful")
    evs = [e for e, _ in run.events]
    ts = [e.timestamp for e in evs]
    assert ts == sorted(ts)                              # horodatages croissants
    seqs = [r.seq for _, r in run.events]
    assert seqs == sorted(seqs)                          # seq croissants
    assert evs[0].event_type == ET.RELOAD_START and evs[-1].event_type == ET.RELOAD_END

    # Imbrication : chaque section est fermée avant la suivante, tables à l'intérieur.
    open_section = None
    for e in run.demo_events():
        if e.event_type == ET.SECTION_START:
            assert open_section is None
            open_section = e.section
        elif e.event_type == ET.SECTION_END:
            assert e.section == open_section
            open_section = None
        elif e.event_type in (ET.TABLE_START, ET.TABLE_END, ET.QVD_WRITE_START):
            assert e.section == open_section
    assert open_section is None

    # Pour chaque QVD : START < SIZE_CHANGE... < END, et après le TABLE_END de sa table.
    for qvd, table in (("CLIENTS.qvd", "CLIENTS"), ("VENTES.qvd", "VENTES")):
        idx = {}
        for i, e in enumerate(evs):
            if e.qvd == qvd or (e.table == table and e.event_type == ET.TABLE_END):
                idx.setdefault(e.event_type, []).append(i)
        assert idx[ET.TABLE_END][0] < idx[ET.QVD_WRITE_START][0]
        assert idx[ET.QVD_WRITE_START][0] < min(idx[ET.QVD_SIZE_CHANGE])
        assert max(idx[ET.QVD_SIZE_CHANGE]) < idx[ET.QVD_WRITE_END][0]


def test_progression_des_lignes_croissante(tmp_path):
    run = run_scenario(tmp_path, "successful")
    rows = [e.rows for e in run.demo_events() if e.table == "VENTES"
            and e.event_type in (ET.TABLE_PROGRESS, ET.TABLE_END)]
    assert len(rows) == 5 and rows == sorted(rows) and rows[-1] == 1_842_556


# ------------------------------------------------------------- fichiers QVD

def test_croissance_reelle_du_fichier(tmp_path):
    run = run_scenario(tmp_path, "successful")
    sizes = [e.qvd_size_bytes for e, _ in run.events
             if e.event_type == ET.QVD_SIZE_CHANGE and e.qvd == "VENTES.qvd"]
    assert len(sizes) == 8 and sizes == sorted(sizes) and len(set(sizes)) == 8
    # La dernière taille observée est celle du fichier réellement présent sur disque.
    assert sizes[-1] == (run.qvd_dir / "VENTES.qvd").stat().st_size
    assert sizes[-1] == int(31.8 * MIB * SCALE)


def test_tailles_constatees_par_le_watcher(tmp_path):
    run = run_scenario(tmp_path, "successful")
    for e, _ in run.events:
        if e.event_type == ET.QVD_SIZE_CHANGE:
            assert e.source is EventSource.QVD_WATCHER
    assert {m["source"] for m in run.measures()} == {"qvd_watcher"}


def test_fichiers_factices_uniquement(tmp_path):
    run = run_scenario(tmp_path, "successful")
    data = (run.qvd_dir / "CLIENTS.qvd").read_bytes()
    assert data and set(data) == {0}  # uniquement des octets nuls


def test_plusieurs_qvd_successifs(tmp_path):
    run = run_scenario(tmp_path, "successful")
    files = sorted(p.name for p in run.qvd_dir.iterdir())
    assert files == ["CLIENTS.qvd", "PRODUITS.qvd", "VENTES.qvd"]
    for name in files:
        ms = run.measures(name)
        assert ms[-1]["is_stable"] == 1
        assert ms[-1]["size_bytes"] == (run.qvd_dir / name).stat().st_size
    # Les écritures ne se chevauchent pas : stabilité d'un QVD avant l'écriture du suivant.
    stable_ts = {name: run.measures(name)[-1]["timestamp"] for name in files}
    start_ts = {e.qvd: e.timestamp.isoformat(timespec="microseconds")
                for e in run.demo_events() if e.event_type == ET.QVD_WRITE_START}
    assert stable_ts["CLIENTS.qvd"] <= start_ts["PRODUITS.qvd"]
    assert stable_ts["PRODUITS.qvd"] <= start_ts["VENTES.qvd"]


def test_delta_bytes_correct(tmp_path):
    run = run_scenario(tmp_path, "successful")
    for name in ("CLIENTS.qvd", "PRODUITS.qvd", "VENTES.qvd"):
        ms = [m for m in run.measures(name) if not m["is_stable"]]
        previous = 0
        for m in ms:
            assert m["delta_bytes"] == m["size_bytes"] - previous
            previous = m["size_bytes"]
        assert sum(m["delta_bytes"] for m in ms) == ms[-1]["size_bytes"]


def test_is_stable_correct(tmp_path):
    run = run_scenario(tmp_path, "successful")
    ms = run.measures("CLIENTS.qvd")
    assert [m["is_stable"] for m in ms] == [0, 0, 0, 0, 1]   # 4 blocs puis stable
    assert ms[-1]["delta_bytes"] == 0
    end_ts = [e.timestamp for e in run.demo_events()
              if e.event_type == ET.QVD_WRITE_END and e.qvd == "CLIENTS.qvd"][0]
    assert ms[-1]["timestamp"] > end_ts.isoformat(timespec="microseconds")


def test_is_stable_depend_du_parametre(tmp_path):
    run = run_scenario(tmp_path, "successful", stable_after=5)
    assert [m["is_stable"] for m in run.measures("PRODUITS.qvd")] == [0, 0, 1]


def test_etat_courant_pendant_la_demo(tmp_path):
    run = run_scenario(tmp_path, "successful")
    st = run.state()
    assert st.current_qvd == "VENTES.qvd"
    assert st.current_qvd_is_stable is True
    assert st.current_qvd_writing is False
    assert st.current_qvd_size_bytes == int(31.8 * MIB * SCALE)


# ---------------------------------------------- relances et nettoyage

def test_deux_runs_meme_base_delta_repart_de_zero(tmp_path):
    conn = open_db(tmp_path / "shared.db")
    first = run_scenario(tmp_path, "successful", conn=conn)
    second = run_scenario(tmp_path, "successful", conn=conn)
    assert first.reload_id != second.reload_id
    m = second.measures("CLIENTS.qvd")[0]
    assert m["delta_bytes"] == m["size_bytes"] > 0  # pas de delta négatif hérité du run 1


def test_nettoyage_avant_scenario(tmp_path):
    run_scenario(tmp_path, "successful")
    qvd_dir = tmp_path / "demo_data" / "qvd"
    assert len(list(qvd_dir.iterdir())) == 3
    run_scenario(tmp_path, "error")
    assert sorted(p.name for p in qvd_dir.iterdir()) == ["CLIENTS.qvd"]


# -------------------------------------------------- scénario sur mesure

def test_scenario_minimal_sans_qvd(tmp_path):
    sc = Scenario.model_validate({
        "name": "mini", "app_id": "a", "app_name": "A",
        "sections": [{"name": "S", "tables": [
            {"name": "T", "rows": 10, "duration_seconds": 1, "progress_steps": 1}]}],
    })
    run = run_scenario(tmp_path, sc)
    assert run.types() == [ET.RELOAD_START, ET.SECTION_START, ET.TABLE_START,
                           ET.TABLE_END, ET.SECTION_END, ET.RELOAD_END]


def test_erreur_des_le_debut(tmp_path):
    sc = Scenario.model_validate({
        "name": "e0", "app_id": "a", "app_name": "A",
        "sections": [{"name": "S", "tables": [
            {"name": "T", "rows": 10, "duration_seconds": 1,
             "error": {"message": "connexion refusée", "at_fraction": 0}}]}],
    })
    run = run_scenario(tmp_path, sc)
    assert run.types() == [ET.RELOAD_START, ET.SECTION_START, ET.TABLE_START,
                           ET.ERROR, ET.RELOAD_END]
    assert run.state().status is EventStatus.ERROR
