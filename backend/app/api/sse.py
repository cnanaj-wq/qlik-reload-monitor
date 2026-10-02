"""Flux Server-Sent Events des nouveaux événements.

Principe : SQLite est la file d'attente. Chaque client garde son propre
curseur (`last_seq`) et relit périodiquement `events WHERE seq > last_seq
ORDER BY seq`. Conséquences :
- ordre garanti par seq ;
- aucune perte à la reconnexion : le client renvoie le dernier `seq` reçu
  (en-tête standard `Last-Event-ID`, ou `?after_seq=`) et reprend juste après ;
- aucune perte au redémarrage du serveur (rien n'est gardé en mémoire) ;
- plusieurs clients indépendants, sans diffusion à synchroniser.

Format d'un message :
    id: <seq>
    event: reload_event
    data: <ReloadEvent JSON>
Heartbeat (commentaire SSE, ignoré par EventSource) : `: heartbeat <iso>`.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import AsyncIterator, Awaitable, Callable, Optional

from starlette.concurrency import run_in_threadpool

from .repository import Repository

RETRY_MS = 2000  # délai de reconnexion conseillé au navigateur


def format_event(event_json: dict, seq: int) -> str:
    data = json.dumps(event_json, ensure_ascii=False, separators=(",", ":"))
    return f"id: {seq}\nevent: reload_event\ndata: {data}\n\n"


def resolve_start_seq(after_seq: Optional[int], last_event_id: Optional[str],
                      current_max: int) -> int:
    """Point de départ du flux, par ordre de priorité :
    1. `?after_seq=` explicite ;
    2. en-tête `Last-Event-ID` (reconnexion automatique d'EventSource) ;
    3. sinon, seulement les événements à venir (max seq actuel).
    """
    if after_seq is not None:
        return max(0, after_seq)
    if last_event_id:
        try:
            return max(0, int(last_event_id.strip()))
        except ValueError:
            pass
    return current_max


async def event_stream(
    repo: Repository,
    *,
    start_seq: int,
    is_disconnected: Callable[[], Awaitable[bool]],
    reload_id: Optional[str] = None,
    poll_seconds: float = 0.5,
    heartbeat_seconds: float = 15.0,
    batch_size: int = 500,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[str]:
    last_seq = start_seq
    yield f"retry: {RETRY_MS}\n: stream ouvert après seq {last_seq}\n\n"
    last_sent = clock()

    while True:
        if await is_disconnected():
            return
        # Lecture SQLite hors de la boucle asynchrone (ne bloque pas les autres clients).
        events = await run_in_threadpool(repo.events_after, last_seq,
                                         limit=batch_size, reload_id=reload_id)
        for ev in events:
            yield format_event(ev.model_dump(mode="json"), ev.seq)
            last_seq = ev.seq
        if events:
            last_sent = clock()
            if len(events) == batch_size:
                continue  # rattrapage : lot suivant immédiatement

        if clock() - last_sent >= heartbeat_seconds:
            yield f": heartbeat {datetime.now().isoformat(timespec='seconds')}\n\n"
            last_sent = clock()
        await asyncio.sleep(poll_seconds)
