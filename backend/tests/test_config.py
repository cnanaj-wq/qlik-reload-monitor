from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import load_config
from app.models import RunMode

PROJECT_CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_config_du_projet_valide():
    cfg = load_config(PROJECT_CONFIG)
    assert cfg.mode is RunMode.DEMO
    assert cfg.monitoring.polling_interval_seconds == 1
    assert cfg.database.path.is_absolute()
    assert cfg.server.host == "127.0.0.1"


def test_fichier_vide_donne_valeurs_par_defaut(tmp_path):
    cfg = load_config(write(tmp_path, ""))
    assert cfg.mode is RunMode.DEMO
    assert cfg.qvd.extensions == [".qvd"]


def test_chemins_relatifs_resolus_depuis_dossier_config(tmp_path):
    cfg = load_config(write(tmp_path, 'database:\n  path: "./data/x.db"\n'))
    assert cfg.database.path == (tmp_path / "data" / "x.db").resolve()
    assert cfg.demo.output_dir == (tmp_path / "demo_data").resolve()


def test_extensions_normalisees(tmp_path):
    cfg = load_config(write(tmp_path, "qvd:\n  extensions: [QVD, .Qvd]\n"))
    assert cfg.qvd.extensions == [".qvd", ".qvd"]


def test_fichier_absent(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "absent.yaml")


def test_cle_inconnue_refusee(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, "monitoring:\n  polling_interval: 1\n"))


@pytest.mark.parametrize("valeur", [0, -1, 120])
def test_intervalle_hors_bornes(tmp_path, valeur):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, f"monitoring:\n  polling_interval_seconds: {valeur}\n"))


def test_stabilisation_minimum_deux_mesures(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, "monitoring:\n  stable_after_measures: 1\n"))


def test_mode_inconnu(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, "mode: prod\n"))


def test_mode_live_sans_sources_refuse(tmp_path):
    with pytest.raises(ValidationError, match="log_paths"):
        load_config(write(tmp_path, "mode: live\n"))


def test_mode_live_avec_sources(tmp_path):
    cfg = load_config(write(
        tmp_path,
        f"mode: live\nqlik:\n  log_paths: ['{tmp_path.as_posix()}/logs']\n"
        f"qvd:\n  directories: ['{tmp_path.as_posix()}/qvd']\n",
    ))
    assert cfg.mode is RunMode.LIVE
    # Les dossiers n'ont pas besoin d'exister à ce stade.
    assert not cfg.qvd.directories[0].exists()


def test_racine_non_objet_refusee(tmp_path):
    with pytest.raises(ValueError):
        load_config(write(tmp_path, "- a\n- b\n"))
