"""Tests du flux SSE (étape 4).

Deux niveaux :
- le générateur `event_stream` testé seul (rapide, déterministe) ;
- un vrai serveur uvicorn démarré dans un thread, avec de vraies connexions
  HTTP : ordre, reprise après reconnexion, événements écrits pendant que le
  flux est ouvert, plusieurs clients, heartbeat.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from datetime import datetime, timedelta

import httpx
import pytest
import uvicorn

from app.api import create_app
from app.api.repository import Repository
from app.api.sse import event_stream, resolve_start_seq
from app.config import AppConfig, DatabaseConfig, ServerConfig
from app.engine import EventEngine
from app.models import EventSource, EventStatus, EventType, ReloadEvent
from app.storage.db import open_db

T0 = datetime(2026, 10, 1, 9, 0, 0)


def ev(i: int, rid="R1", et=EventType.TABLE_PROGRESS) -> ReloadEvent:
    kw = {"section": "S", "table": "T", "rows": i} if et == EventType.TABLE_PROGRESS else {}
    return ReloadEvent(reload_id=rid, timestamp=T0 + timedelta(seconds=i),
                       source=EventSource.DEMO, app_id=f"app-{rid}", app_name=rid,
                       event_type=et, status=EventStatus.RUNNING, **kw)


def seed(db, n, rid="R1"):
    conn = open_db(db)
    eng = EventEngine(conn)
    eng.ingest(ev(0, rid, EventType.RELOAD_START))
    for i in range(1, n):
        eng.ingest(ev(i, rid))
    conn.close()


def parse_sse(chunks: str) -> list[dict]:
    """Découpe un texte SSE en messages {id, event, data, comment}."""
    out = []
    for block in chunks.split("\n\n"):
        if not block.strip():
            continue
        msg = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                msg.setdefault("comment", line[1:].strip())
            elif ":" in line:
                k, v = line.split(":", 1)
                msg[k] = v[1:] if v.startswith(" ") else v
        out.append(msg)
    return out


# ------------------------------------------------- point de départ du flux

@pytest.mark.parametrize("after,header,current,expected", [
    (5, "9", 42, 5),        # after_seq prioritaire
    (None, "9", 42, 9),     # Last-Event-ID
    (None, " 9 ", 42, 9),
    (None, None, 42, 42),   # par défaut : seulement les nouveaux
    (None, "abc", 42, 42),  # en-tête invalide ignoré
    (-3, None, 42, 0),
])
def test_resolve_start_seq(after, header, current, expected):
    assert resolve_start_seq(after, header, current) == expected


# ------------------------------------------------ générateur seul (unitaire)

def collect(repo, *, start, n_events, **kw):
    """Exécute le générateur jusqu'à recevoir n_events messages d'événement."""
    received: list[str] = []

    async def run():
        count = 0

        async def disconnected():
            return count >= n_events

        async for chunk in event_stream(repo, start_seq=start, is_disconnected=disconnected,
                                        poll_seconds=0.01, **kw):
            received.append(chunk)
            if chunk.startswith("id:"):
                count += 1
    asyncio.run(asyncio.wait_for(run(), timeout=10))
    return parse_sse("".join(received))


def test_generateur_ordre_et_format(tmp_path):
    seed(tmp_path / "m.db", 10)
    msgs = collect(Repository(tmp_path / "m.db"), start=0, n_events=10)
    assert msgs[0]["retry"] == "2000"
    events = [m for m in msgs if "id" in m]
    assert [int(m["id"]) for m in events] == list(range(1, 11))
    assert all(m["event"] == "reload_event" for m in events)
    data = json.loads(events[3]["data"])
    assert data["seq"] == 4 and data["rows"] == 3 and data["reload_id"] == "R1"


def test_generateur_lots_sans_perte(tmp_path):
    seed(tmp_path / "m.db", 25)
    msgs = collect(Repository(tmp_path / "m.db"), start=0, n_events=25, batch_size=4)
    assert [int(m["id"]) for m in msgs if "id" in m] == list(range(1, 26))


def test_generateur_filtre_reload(tmp_path):
    db = tmp_path / "m.db"
    seed(db, 3, "R1")
    seed(db, 3, "R2")
    msgs = collect(Repository(db), start=0, n_events=3, reload_id="R2")
    assert {json.loads(m["data"])["reload_id"] for m in msgs if "id" in m} == {"R2"}


def test_generateur_heartbeat(tmp_path):
    repo = Repository(tmp_path / "m.db")  # base vide
    fake = {"t": 0.0}

    def clock():
        fake["t"] += 1.0  # chaque lecture d'horloge avance d'1 s
        return fake["t"]

    chunks = []

    async def run():
        async def disconnected():
            return sum(c.startswith(": heartbeat") for c in chunks) >= 2
        async for c in event_stream(repo, start_seq=0, is_disconnected=disconnected,
                                    poll_seconds=0.001, heartbeat_seconds=3, clock=clock):
            chunks.append(c)
    asyncio.run(asyncio.wait_for(run(), timeout=10))
    assert sum(c.startswith(": heartbeat") for c in chunks) == 2


# ------------------------------------------------------ serveur réel

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, db_path, heartbeat=0.3):
        cfg = AppConfig(database=DatabaseConfig(path=db_path),
                        server=ServerConfig(sse_poll_seconds=0.05,
                                            sse_heartbeat_seconds=heartbeat))
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(
            create_app(cfg), host="127.0.0.1", port=self.port, log_level="warning",
            timeout_graceful_shutdown=1))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            assert time.time() < deadline, "serveur non démarré"
            time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)


class SseClient:
    """Client SSE minimal sur une vraie connexion HTTP."""

    def __init__(self, url, params=None, headers=None):
        self._client = httpx.Client(timeout=httpx.Timeout(5.0))
        self._cm = self._client.stream("GET", url, params=params, headers=headers)
        self.response = self._cm.__enter__()
        self._lines = self.response.iter_lines()
        self.messages: list[dict] = []

    def read_until(self, predicate, timeout=5.0) -> list[dict]:
        deadline = time.time() + timeout
        block: list[str] = []
        while not predicate(self.messages):
            assert time.time() < deadline, f"délai dépassé, reçu : {self.messages}"
            line = next(self._lines)
            if line == "":
                if block:
                    self.messages.extend(parse_sse("\n".join(block) + "\n\n"))
                block = []
            else:
                block.append(line)
        return self.messages

    def events(self):
        return [m for m in self.messages if "id" in m]

    def read_events(self, n, timeout=5.0):
        self.read_until(lambda ms: len([m for m in ms if "id" in m]) >= n, timeout)
        return self.events()

    def close(self):
        self._cm.__exit__(None, None, None)
        self._client.close()


def ids(msgs):
    return [int(m["id"]) for m in msgs]


def test_sse_entetes_et_flux_ordonne(tmp_path):
    seed(tmp_path / "m.db", 8)
    with LiveServer(tmp_path / "m.db") as srv:
        c = SseClient(f"{srv.url}/api/stream", params={"after_seq": 0})
        assert c.response.status_code == 200
        assert c.response.headers["content-type"].startswith("text/event-stream")
        assert c.response.headers["cache-control"] == "no-cache"
        assert ids(c.read_events(8)) == list(range(1, 9))
        c.close()


def test_sse_par_defaut_seulement_les_nouveaux(tmp_path):
    db = tmp_path / "m.db"
    seed(db, 5)
    with LiveServer(db) as srv:
        c = SseClient(f"{srv.url}/api/stream")
        c.read_until(lambda ms: any("retry" in m for m in ms))
        conn = open_db(db)
        EventEngine(conn).ingest(ev(50))
        conn.close()
        assert ids(c.read_events(1)) == [6]  # les 5 anciens ne sont pas renvoyés
        c.close()


def test_sse_evenements_ecrits_pendant_le_flux(tmp_path):
    db = tmp_path / "m.db"
    seed(db, 1)
    with LiveServer(db) as srv:
        c = SseClient(f"{srv.url}/api/stream", params={"after_seq": 0})
        c.read_events(1)
        conn = open_db(db)
        eng = EventEngine(conn)
        for i in range(1, 21):
            eng.ingest(ev(i))
            if i % 5 == 0:
                time.sleep(0.07)  # écritures étalées sur plusieurs lectures du flux
        conn.close()
        assert ids(c.read_events(21)) == list(range(1, 22))
        c.close()


def test_sse_reconnexion_reprise_depuis_seq(tmp_path):
    db = tmp_path / "m.db"
    seed(db, 30)
    with LiveServer(db) as srv:
        first = SseClient(f"{srv.url}/api/stream", params={"after_seq": 0})
        got = ids(first.read_events(12))[:12]
        first.close()  # coupure côté client

        # Événements écrits PENDANT la déconnexion : ils ne doivent pas être perdus.
        conn = open_db(db)
        eng = EventEngine(conn)
        for i in range(30, 35):
            eng.ingest(ev(i))
        conn.close()

        # Reconnexion standard d'EventSource : en-tête Last-Event-ID.
        second = SseClient(f"{srv.url}/api/stream", headers={"Last-Event-ID": str(got[-1])})
        rest = ids(second.read_events(35 - 12))
        second.close()

    assert got + rest == list(range(1, 36))   # ni perte, ni doublon, dans l'ordre


def test_sse_reconnexion_via_after_seq(tmp_path):
    db = tmp_path / "m.db"
    seed(db, 10)
    with LiveServer(db) as srv:
        c = SseClient(f"{srv.url}/api/stream", params={"after_seq": 7})
        assert ids(c.read_events(3)) == [8, 9, 10]
        c.close()


def test_sse_reprise_apres_redemarrage_du_serveur(tmp_path):
    db = tmp_path / "m.db"
    seed(db, 6)
    with LiveServer(db) as srv:
        c = SseClient(f"{srv.url}/api/stream", params={"after_seq": 0})
        got = ids(c.read_events(6))[:6]
        c.close()
    conn = open_db(db)
    EventEngine(conn).ingest(ev(6))
    conn.close()
    with LiveServer(db) as srv2:  # nouveau processus serveur : aucun état en mémoire
        c = SseClient(f"{srv2.url}/api/stream", headers={"Last-Event-ID": str(got[-1])})
        assert ids(c.read_events(1)) == [7]
        c.close()


def test_sse_plusieurs_clients(tmp_path):
    db = tmp_path / "m.db"
    seed(db, 3)
    with LiveServer(db) as srv:
        a = SseClient(f"{srv.url}/api/stream", params={"after_seq": 0})
        b = SseClient(f"{srv.url}/api/stream", params={"after_seq": 2})
        c = SseClient(f"{srv.url}/api/stream", params={"reload_id": "R2", "after_seq": 0})
        a.read_events(3)
        b.read_events(1)

        conn = open_db(db)
        eng = EventEngine(conn)
        eng.ingest(ev(10, "R2", EventType.RELOAD_START))
        eng.ingest(ev(3))
        eng.ingest(ev(11, "R2"))
        conn.close()

        assert ids(a.read_events(6)) == [1, 2, 3, 4, 5, 6]
        assert ids(b.read_events(4)) == [3, 4, 5, 6]          # curseur indépendant
        r2 = c.read_events(2)
        assert ids(r2) == [4, 6]                               # filtre par reload
        assert {json.loads(m["data"])["reload_id"] for m in r2} == {"R2"}

        # La fermeture d'un client n'affecte pas les autres.
        a.close()
        conn = open_db(db)
        EventEngine(conn).ingest(ev(4))
        conn.close()
        assert ids(b.read_events(5))[-1] == 7
        b.close()
        c.close()


def test_sse_heartbeat_periodique(tmp_path):
    with LiveServer(tmp_path / "m.db", heartbeat=0.2) as srv:
        c = SseClient(f"{srv.url}/api/stream")
        msgs = c.read_until(lambda ms: sum("comment" in m and m["comment"].startswith(
            "heartbeat") for m in ms) >= 2, timeout=5)
        assert not any("id" in m for m in msgs)  # base vide : seulement des heartbeats
        c.close()


def test_sse_base_vide(tmp_path):
    db = tmp_path / "vide.db"
    with LiveServer(db) as srv:
        assert httpx.get(f"{srv.url}/health").json()["last_seq"] == 0
        c = SseClient(f"{srv.url}/api/stream")
        c.read_until(lambda ms: any("retry" in m for m in ms))
        seed(db, 2)
        assert ids(c.read_events(2)) == [1, 2]
        c.close()
