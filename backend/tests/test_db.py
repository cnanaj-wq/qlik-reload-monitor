import sqlite3

import pytest

from app.storage.db import SCHEMA_VERSION, connect, init_db, open_db


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "sub" / "test.db")
    yield c
    c.close()


def tables(c):
    return {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def columns(c, table):
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def add_reload(c, rid="r1"):
    c.execute(
        "INSERT INTO reloads (reload_id, app_id, app_name, source, status, started_at)"
        " VALUES (?, 'app-1', 'Ventes', 'demo', 'RUNNING', '2026-10-01T18:50:02')",
        (rid,),
    )


def add_event(c, key="k1", **kw):
    row = dict(event_key=key, reload_id="r1", timestamp="2026-10-01T18:50:02",
               source="demo", app_id="app-1", app_name="Ventes",
               event_type="RELOAD_START", status="RUNNING")
    row.update(kw)
    cols = ", ".join(row)
    c.execute(f"INSERT INTO events ({cols}) VALUES ({', '.join('?' * len(row))})",
              tuple(row.values()))


def add_measure(c, **kw):
    row = dict(timestamp="2026-10-01T18:50:10", reload_id="r1", qvd_name="CLIENTS.qvd",
               path="/qvd/CLIENTS.qvd", size_bytes=12_000_000, delta_bytes=12_000_000,
               is_stable=0, source="demo")
    row.update(kw)
    cols = ", ".join(row)
    c.execute(f"INSERT INTO qvd_measures ({cols}) VALUES ({', '.join('?' * len(row))})",
              tuple(row.values()))


def test_dossier_parent_cree_et_tables_presentes(tmp_path, conn):
    assert (tmp_path / "sub" / "test.db").exists()
    assert tables(conn) == {"reloads", "events", "qvd_measures", "notification_log"}


def test_version_schema(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_init_idempotent(conn):
    add_reload(conn)
    init_db(conn)
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM reloads").fetchone()[0] == 1


def test_base_plus_recente_refusee(tmp_path):
    c = connect(tmp_path / "x.db")
    c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError):
        init_db(c)
    c.close()


def test_colonnes_demandees(conn):
    assert {"app_id", "app_name", "source"} <= columns(conn, "reloads")
    assert {"seq", "event_key", "source", "app_id", "app_name", "table_name",
            "qvd_size_bytes", "extra_json"} <= columns(conn, "events")
    assert {"timestamp", "qvd_name", "path", "size_bytes", "delta_bytes",
            "reload_id", "is_stable"} <= columns(conn, "qvd_measures")


def test_seq_croissant(conn):
    add_reload(conn)
    add_event(conn, "k1")
    add_event(conn, "k2", event_type="SECTION_START", section="CLIENTS")
    seqs = [r[0] for r in conn.execute("SELECT seq FROM events ORDER BY seq")]
    assert seqs == sorted(seqs) and len(set(seqs)) == 2


def test_doublon_event_key_rejete(conn):
    add_reload(conn)
    add_event(conn, "meme-cle")
    with pytest.raises(sqlite3.IntegrityError):
        add_event(conn, "meme-cle")


def test_doublon_ignore_avec_insert_or_ignore(conn):
    add_reload(conn)
    add_event(conn, "k")
    conn.execute(
        "INSERT OR IGNORE INTO events (event_key, reload_id, timestamp, source, app_id,"
        " app_name, event_type, status) VALUES ('k','r1','t','demo','a','n','RELOAD_START','RUNNING')")
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_cle_etrangere_reload(conn):
    with pytest.raises(sqlite3.IntegrityError):
        add_event(conn, reload_id="inconnu")


@pytest.mark.parametrize("champ,valeur", [
    ("source", "excel"), ("event_type", "BOOM"), ("status", "OK"), ("rows", -5),
])
def test_contraintes_check_events(conn, champ, valeur):
    add_reload(conn)
    with pytest.raises(sqlite3.IntegrityError):
        add_event(conn, **{champ: valeur})


def test_is_stable_distingue_ecriture_et_stabilisation(conn):
    add_reload(conn)
    add_measure(conn, size_bytes=12_000_000, delta_bytes=12_000_000, is_stable=0)
    add_measure(conn, size_bytes=46_000_000, delta_bytes=34_000_000, is_stable=0)
    add_measure(conn, size_bytes=46_000_000, delta_bytes=0, is_stable=1)
    rows = conn.execute(
        "SELECT is_stable, size_bytes FROM qvd_measures ORDER BY id").fetchall()
    assert [r["is_stable"] for r in rows] == [0, 0, 1]
    assert rows[-1]["size_bytes"] == 46_000_000


def test_is_stable_valeurs_autorisees(conn):
    add_reload(conn)
    with pytest.raises(sqlite3.IntegrityError):
        add_measure(conn, is_stable=2)


def test_mesure_sans_reload_autorisee(conn):
    add_measure(conn, reload_id=None)
    assert conn.execute("SELECT COUNT(*) FROM qvd_measures").fetchone()[0] == 1


def test_delta_negatif_autorise(conn):
    add_reload(conn)
    add_measure(conn, delta_bytes=-1_000)


def test_taille_negative_refusee(conn):
    add_reload(conn)
    with pytest.raises(sqlite3.IntegrityError):
        add_measure(conn, size_bytes=-1)
