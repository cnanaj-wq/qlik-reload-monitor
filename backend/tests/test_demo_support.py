"""Tests du nettoyage sécurisé et du chargement des scénarios."""

import pytest
from pydantic import ValidationError

from app.demo import UnsafeDemoDirError, load_scenario, list_scenarios, prepare_demo_dir
from app.demo.cleanup import MARKER
from app.demo.clock import RealClock, VirtualClock
from app.demo.scenario import Scenario


# ------------------------------------------------------------- nettoyage

def test_creation_avec_marqueur(tmp_path):
    qvd_dir, removed = prepare_demo_dir(tmp_path / "demo_data")
    assert qvd_dir == (tmp_path / "demo_data" / "qvd").resolve()
    assert (tmp_path / "demo_data" / MARKER).is_file()
    assert removed == []


def test_nettoyage_ne_supprime_que_les_qvd_du_dossier_demo(tmp_path):
    qvd_dir, _ = prepare_demo_dir(tmp_path / "demo_data")
    (qvd_dir / "A.qvd").write_bytes(b"\0")
    (qvd_dir / "B.QVD").write_bytes(b"\0")
    (qvd_dir / "garder.txt").write_text("x")
    (qvd_dir / "sous").mkdir()
    (qvd_dir / "sous" / "C.qvd").write_bytes(b"\0")
    _, removed = prepare_demo_dir(tmp_path / "demo_data")
    assert sorted(p.name for p in removed) == ["A.qvd", "B.QVD"]
    assert (qvd_dir / "garder.txt").exists()
    assert (qvd_dir / "sous" / "C.qvd").exists()  # pas de récursion


def test_refus_dossier_non_vide_sans_marqueur(tmp_path):
    d = tmp_path / "Documents"
    d.mkdir()
    (d / "important.qvd").write_bytes(b"\0")
    with pytest.raises(UnsafeDemoDirError, match="marqueur"):
        prepare_demo_dir(d)
    assert (d / "important.qvd").exists()


def test_dossier_vide_existant_accepte(tmp_path):
    d = tmp_path / "vide"
    d.mkdir()
    prepare_demo_dir(d)
    assert (d / MARKER).exists()


@pytest.mark.parametrize("relation", ["egal", "dedans", "contient"])
def test_refus_conflit_avec_dossier_qlik(tmp_path, relation):
    qlik = tmp_path / "Qlik" / "QVD"
    qlik.mkdir(parents=True)
    demo = {"egal": qlik, "dedans": qlik / "demo", "contient": tmp_path / "Qlik"}[relation]
    with pytest.raises(UnsafeDemoDirError, match="conflit"):
        prepare_demo_dir(demo, forbidden_dirs=[qlik])


def test_refus_si_le_chemin_est_un_fichier(tmp_path):
    f = tmp_path / "demo_data"
    f.write_text("x")
    with pytest.raises(UnsafeDemoDirError):
        prepare_demo_dir(f)


# ------------------------------------------------------------- scénarios

def test_trois_scenarios_fournis():
    assert list_scenarios() == ["error", "slow", "successful"]
    for name in list_scenarios():
        assert load_scenario(name).name == name


def test_scenario_inconnu():
    with pytest.raises(FileNotFoundError, match="successful"):
        load_scenario("inexistant")


BASE = {"name": "x", "app_id": "a", "app_name": "A"}


def table(**kw):
    return {"name": "T", "rows": 1, "duration_seconds": 1, **kw}


@pytest.mark.parametrize("qvd", ["../evil.qvd", "C:/Qlik/VENTES.qvd", "a/b.qvd",
                                 "VENTES.txt", ".cache.qvd", ""])
def test_nom_de_qvd_dangereux_refuse(qvd):
    with pytest.raises(ValidationError):
        Scenario.model_validate({**BASE, "sections": [{"name": "S", "tables": [
            table(store={"qvd": qvd, "size_mb": 1})]}]})


def test_une_seule_erreur_par_scenario():
    err = {"message": "x"}
    with pytest.raises(ValidationError, match="une seule erreur"):
        Scenario.model_validate({**BASE, "sections": [{"name": "S", "tables": [
            table(error=err), table(name="U", error=err)]}]})


def test_qvd_unique_par_scenario():
    st = {"qvd": "A.qvd", "size_mb": 1}
    with pytest.raises(ValidationError, match="qu'une fois"):
        Scenario.model_validate({**BASE, "sections": [{"name": "S", "tables": [
            table(store=st), table(name="U", store=st)]}]})


def test_scenario_sans_section_refuse():
    with pytest.raises(ValidationError):
        Scenario.model_validate({**BASE, "sections": []})


def test_cle_inconnue_refusee():
    with pytest.raises(ValidationError):
        Scenario.model_validate({**BASE, "sections": [{"name": "S", "duree": 3}]})


# ---------------------------------------------------------------- horloges

@pytest.mark.parametrize("speed", [0, -1, 5000])
def test_vitesse_invalide(speed):
    with pytest.raises(ValueError):
        RealClock(speed)
    with pytest.raises(ValueError):
        VirtualClock(None, speed)


def test_horloge_virtuelle_applique_la_vitesse():
    from datetime import datetime
    c = VirtualClock(datetime(2026, 1, 1), speed=4)
    c.sleep(10)
    assert c.now() == datetime(2026, 1, 1, 0, 0, 2, 500000)
