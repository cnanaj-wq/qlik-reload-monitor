from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models import EventSource, EventStatus, EventType, ReloadEvent
from app.models.enums import QVD_EVENTS, SECTION_EVENTS, TABLE_EVENTS


def make(**overrides):
    base = dict(
        reload_id="20261001-185002-VENTES",
        timestamp=datetime(2026, 10, 1, 18, 50, 35),
        source=EventSource.DEMO,
        app_id="demo-app-ventes",
        app_name="Ventes",
        event_type=EventType.TABLE_PROGRESS,
        status=EventStatus.RUNNING,
        section="FACTS",
        table="VENTES",
        rows=328543,
    )
    base.update(overrides)
    return ReloadEvent(**base)


# --- enums -------------------------------------------------------------------

def test_event_types_complets():
    attendus = {
        "RELOAD_START", "RELOAD_END", "SECTION_START", "SECTION_END",
        "TABLE_START", "TABLE_PROGRESS", "TABLE_END",
        "QVD_WRITE_START", "QVD_SIZE_CHANGE", "QVD_WRITE_END", "QVD_STABLE",
        "WARNING", "ERROR",
    }
    assert {e.value for e in EventType} == attendus


def test_sources_minimales():
    assert {s.value for s in EventSource} >= {"demo", "qlik_log", "qvd_watcher"}


def test_statuts():
    assert {s.value for s in EventStatus} == {
        "PENDING", "RUNNING", "SUCCESS", "WARNING", "ERROR"}


def test_familles_disjointes():
    assert not (SECTION_EVENTS & TABLE_EVENTS)
    assert not (TABLE_EVENTS & QVD_EVENTS)


# --- modèle ------------------------------------------------------------------

def test_evenement_valide_exemple_du_cahier_des_charges():
    ev = make()
    assert ev.rows == 328543
    assert ev.seq is None
    assert ev.app_id != ev.app_name


def test_serialisation_json_aller_retour():
    ev = make()
    data = ev.model_dump(mode="json")
    assert data["event_type"] == "TABLE_PROGRESS"
    assert data["source"] == "demo"
    assert ReloadEvent.model_validate(data) == ev


def test_valeurs_texte_acceptees():
    ev = make(source="qvd_watcher", platform="qlik_sense", event_type="QVD_SIZE_CHANGE",
              status="RUNNING", table=None, rows=None, qvd="VENTES.qvd", qvd_size_bytes=10)
    assert ev.source is EventSource.QVD_WATCHER


@pytest.mark.parametrize("champ", ["reload_id", "app_id", "app_name"])
def test_identifiants_vides_refuses(champ):
    with pytest.raises(ValidationError):
        make(**{champ: ""})


def test_type_inconnu_refuse():
    with pytest.raises(ValidationError):
        make(event_type="TABLE_EXPLODE")


def test_source_inconnue_refusee():
    with pytest.raises(ValidationError):
        make(source="excel")


def test_champ_inconnu_refuse():
    with pytest.raises(ValidationError):
        make(colonne_inventee=1)


@pytest.mark.parametrize("champ", ["rows", "qvd_size_bytes"])
def test_valeurs_negatives_refusees(champ):
    with pytest.raises(ValidationError):
        make(**{champ: -1})


def test_section_obligatoire_pour_section_start():
    with pytest.raises(ValidationError, match="section"):
        make(event_type=EventType.SECTION_START, section=None)


def test_table_obligatoire_pour_table_end():
    with pytest.raises(ValidationError, match="table"):
        make(event_type=EventType.TABLE_END, table=None)


def test_qvd_obligatoire_pour_evenements_qvd():
    with pytest.raises(ValidationError, match="qvd"):
        make(event_type=EventType.QVD_WRITE_START, qvd=None)


def test_taille_obligatoire_pour_qvd_size_change():
    with pytest.raises(ValidationError, match="qvd_size_bytes"):
        make(event_type=EventType.QVD_SIZE_CHANGE, qvd="VENTES.qvd",
             qvd_size_bytes=None)


@pytest.mark.parametrize("et", [EventType.WARNING, EventType.ERROR])
def test_message_obligatoire_pour_warning_error(et):
    with pytest.raises(ValidationError, match="message"):
        make(event_type=et, message=None)


def test_reload_start_minimal():
    ev = make(event_type=EventType.RELOAD_START, section=None, table=None, rows=None)
    assert ev.event_type is EventType.RELOAD_START


def test_extra_extensible():
    ev = make(extra={"log_line": 1542, "sql_ms": 820})
    assert ev.extra["sql_ms"] == 820


# --- déduplication -----------------------------------------------------------

def test_event_key_deterministe():
    assert make().event_key == make().event_key


def test_event_key_ignore_seq():
    assert make(seq=1).event_key == make(seq=99).event_key


def test_event_key_differe_si_contenu_differe():
    assert make(rows=1).event_key != make(rows=2).event_key
