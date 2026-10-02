"""API HTTP en lecture seule : état, historique, événements, mesures QVD, flux SSE.

Toutes les routes sont des GET. L'API ne déclenche, n'arrête ni ne modifie
rien côté Qlik ; elle ne fait que lire la base SQLite du monitor.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import AppConfig
from ..engine import ReloadState
from ..models import EventStatus, Platform, ReloadEvent
from ..storage.db import SCHEMA_VERSION
from .repository import Repository
from .sse import event_stream, resolve_start_seq

API_VERSION = "0.5.0"


# ------------------------------------------------------------------ schémas

class ReloadStateOut(BaseModel):
    reload_id: str
    app_id: str
    app_name: str
    source: str
    platform: Platform
    status: EventStatus
    is_running: bool
    started_at: datetime
    ended_at: Optional[datetime]
    elapsed_ms: int
    current_step: Optional[str]
    current_section: Optional[str]
    current_table: Optional[str]
    current_rows: Optional[int]
    current_qvd: Optional[str]
    current_qvd_size_bytes: Optional[int]
    current_qvd_is_stable: Optional[bool]
    current_qvd_writing: bool
    total_rows: int
    warnings_count: int
    errors_count: int
    last_message: Optional[str]
    last_seq: Optional[int]

    @classmethod
    def of(cls, st: ReloadState) -> "ReloadStateOut":
        return cls.model_validate(st.to_dict())


class ReloadSummary(BaseModel):
    reload_id: str
    app_id: str
    app_name: str
    source: str
    platform: Platform
    status: EventStatus
    started_at: datetime
    ended_at: Optional[datetime]
    duration_ms: Optional[int]
    total_rows: int
    warnings_count: int
    errors_count: int
    qvd_count: int       # QVD stabilisés rattachés au reload
    qvd_bytes: int       # somme de leurs tailles stabilisées


class ReloadPage(BaseModel):
    items: list[ReloadSummary]
    total: int
    limit: int
    offset: int


class QvdMeasureOut(BaseModel):
    id: int
    timestamp: datetime
    reload_id: Optional[str]
    qvd_name: str
    path: str
    size_bytes: int
    delta_bytes: int
    is_stable: bool
    source: str


class NotificationOut(BaseModel):
    id: int
    reload_id: str
    channel: str
    recipient: str
    status: Literal["PENDING", "SENT", "FAILED", "DRY_RUN"]
    attempts: int
    subject: Optional[str]
    created_at: datetime
    sent_at: Optional[datetime]
    error_message: Optional[str]


class NotificationsInfo(BaseModel):
    email_enabled: bool
    email_dry_run: bool


class EmailStatusOut(BaseModel):
    """État de la configuration email, sans secret ni adresse."""
    enabled: bool
    dry_run: bool
    # disabled | dry_run | ready (envoi réel possible) | incomplete (envoi réel impossible)
    readiness: Literal["disabled", "dry_run", "ready", "incomplete"]
    problems: list[str]


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    mode: str
    notifications: NotificationsInfo
    database: str
    schema_version: int
    last_seq: int
    time: datetime


# ------------------------------------------------------------ interface web

class FrontendFiles(StaticFiles):
    """Fichiers de `frontend/dist` avec une politique de cache adaptée.

    - `assets/*` : noms contenant une empreinte (hash Vite) -> cache long, immuable ;
    - le reste (index.html) : revalidé à chaque chargement, pour qu'une nouvelle
      version de l'interface soit prise en compte sans vider le cache du navigateur.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            if path.startswith("assets/"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "no-cache"
        return response


# ---------------------------------------------------------------- application

def create_app(config: AppConfig) -> FastAPI:
    repo = Repository(config.database.path,
                      stable_after_measures=config.monitoring.stable_after_measures)
    srv = config.server

    app = FastAPI(
        title="Qlik Reload Monitor API",
        version=API_VERSION,
        description="Supervision en lecture seule des rechargements Qlik Sense.",
    )
    app.state.repo = repo
    app.state.config = config

    def _state_or_404(reload_id: str) -> ReloadState:
        st = repo.reload_state(reload_id)
        if st is None:
            raise HTTPException(status_code=404, detail=f"Reload introuvable : {reload_id}")
        return st

    @app.get("/health", response_model=Health, tags=["système"])
    def health() -> Health:
        email = config.notifications.email
        return Health(status="ok", version=API_VERSION, mode=config.mode.value,
                      notifications=NotificationsInfo(email_enabled=email.enabled,
                                                      email_dry_run=email.dry_run),
                      database=str(repo.db_path), schema_version=repo.schema_version(),
                      last_seq=repo.max_seq(), time=datetime.now())

    @app.get("/api/notifications/status", response_model=EmailStatusOut,
             tags=["notifications"],
             summary="Configuration email : prête, dry-run, désactivée ou incomplète (sans secret)")
    def notifications_status() -> EmailStatusOut:
        email = config.notifications.email
        return EmailStatusOut(enabled=email.enabled, dry_run=email.dry_run,
                              readiness=email.readiness, problems=email.delivery_problems())

    @app.get("/api/reloads/current", response_model=Optional[ReloadStateOut],
             tags=["reloads"],
             summary="Reload en cours le plus récent, sinon le dernier (null si base vide)")
    def current(app_id: Optional[str] = Query(None, description="Restreindre à une application")):
        st = repo.current_state(app_id=app_id)
        return ReloadStateOut.of(st) if st else None

    @app.get("/api/reloads/active", response_model=list[ReloadStateOut], tags=["reloads"],
             summary="Reloads non terminés, du plus récent au plus ancien")
    def active():
        return [ReloadStateOut.of(s) for s in repo.active_states()]

    @app.get("/api/reloads", response_model=ReloadPage, tags=["reloads"],
             summary="Historique paginé, du plus récent au plus ancien")
    def history(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                app_id: Optional[str] = None, status: Optional[EventStatus] = None,
                platform: Optional[Platform] = None,
                app: Optional[str] = Query(None, max_length=200,
                                           description="Recherche partielle (nom ou id)"),
                date_from: Optional[date] = Query(None, description="Début >= cette date"),
                date_to: Optional[date] = Query(None, description="Début <= cette date")):
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=422, detail="date_from doit précéder date_to")
        items, total = repo.list_reloads(
            limit=limit, offset=offset, app_id=app_id,
            status=status.value if status else None,
            platform=platform.value if platform else None,
            app=app.strip() if app else None, date_from=date_from, date_to=date_to)
        return ReloadPage(items=[ReloadSummary.model_validate(i) for i in items],
                          total=total, limit=limit, offset=offset)

    @app.get("/api/reloads/{reload_id}", response_model=ReloadStateOut, tags=["reloads"])
    def reload_detail(reload_id: str):
        return ReloadStateOut.of(_state_or_404(reload_id))

    @app.get("/api/reloads/{reload_id}/events", response_model=list[ReloadEvent],
             tags=["reloads"],
             summary="Événements d'un reload (ordre seq par défaut, ou chronologique)")
    def reload_events(reload_id: str, after_seq: int = Query(0, ge=0),
                      limit: int = Query(1000, ge=1, le=10_000),
                      order: Literal["seq", "timestamp"] = "seq"):
        if not repo.reload_exists(reload_id):
            raise HTTPException(status_code=404, detail=f"Reload introuvable : {reload_id}")
        return repo.events(reload_id, after_seq=after_seq, limit=limit, order=order)

    @app.get("/api/reloads/{reload_id}/qvd-measures", response_model=list[QvdMeasureOut],
             tags=["qvd"])
    def reload_measures(reload_id: str, qvd_name: Optional[str] = None):
        if not repo.reload_exists(reload_id):
            raise HTTPException(status_code=404, detail=f"Reload introuvable : {reload_id}")
        return repo.qvd_measures(reload_id, qvd_name=qvd_name)

    @app.get("/api/reloads/{reload_id}/notifications", response_model=list[NotificationOut],
             tags=["notifications"],
             summary="Notifications émises pour ce reload (sans aucun secret)")
    def reload_notifications(reload_id: str):
        if not repo.reload_exists(reload_id):
            raise HTTPException(status_code=404, detail=f"Reload introuvable : {reload_id}")
        return repo.notifications(reload_id)

    @app.get("/api/stream", tags=["temps réel"],
             summary="Flux SSE des nouveaux événements (reprise via Last-Event-ID ou after_seq)",
             response_class=StreamingResponse)
    async def stream(request: Request,
                     after_seq: Optional[int] = Query(None, ge=0),
                     reload_id: Optional[str] = None,
                     last_event_id: Optional[str] = Header(None, alias="Last-Event-ID")):
        start = resolve_start_seq(after_seq, last_event_id, repo.max_seq())
        gen = event_stream(repo, start_seq=start, is_disconnected=request.is_disconnected,
                           reload_id=reload_id, poll_seconds=srv.sse_poll_seconds,
                           heartbeat_seconds=srv.sse_heartbeat_seconds,
                           batch_size=srv.sse_batch_size)
        return StreamingResponse(gen, media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    # Interface compilée (frontend/dist) servie à la racine, APRÈS les routes API.
    dist = Path(srv.frontend_dist)
    if (dist / "index.html").is_file():
        app.mount("/", FrontendFiles(directory=dist, html=True), name="frontend")
    app.state.frontend_served = (dist / "index.html").is_file()

    return app


def app_from_config_file(path: str | Path) -> FastAPI:
    from ..config import load_config
    return create_app(load_config(path))
