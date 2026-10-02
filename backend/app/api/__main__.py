"""Lancement du serveur : python -m app.api [--config chemin] [--host h] [--port p]

À lancer depuis le dossier backend/ (environnement virtuel activé).
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import sys
from pathlib import Path

import uvicorn

from ..config import load_config
from .app import create_app

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config.yaml"


_EMAIL_LABELS = {
    "disabled": "désactivé",
    "dry_run": "dry-run (messages construits, rien n'est envoyé)",
    "ready": "envoi réel actif",
    "incomplete": "envoi réel demandé mais configuration incomplète",
}


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.api",
                                     description="API de supervision (lecture seule).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--host", help="remplace server.host de la configuration")
    parser.add_argument("--port", type=int, help="remplace server.port de la configuration")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    cfg = load_config(args.config)
    host = args.host or cfg.server.host
    port = args.port or cfg.server.port
    if not _is_loopback(host):
        print(f"ATTENTION : écoute sur {host}. L'API n'a pas d'authentification ; "
              "elle sera accessible depuis le réseau.", file=sys.stderr)

    print(f"Qlik Reload Monitor API — http://{host}:{port}  (docs : /docs)")
    print(f"Base SQLite : {cfg.database.path}")
    dist = cfg.server.frontend_dist
    if (dist / "index.html").is_file():
        print(f"Interface   : http://{host}:{port}/  (servie depuis {dist})")
    else:
        print(f"Interface   : non servie — {dist / 'index.html'} introuvable "
              "(lancer « npm run build » dans frontend/)", file=sys.stderr)
    email = cfg.notifications.email
    print(f"Email       : {_EMAIL_LABELS[email.readiness]}")
    for problem in email.delivery_problems():
        print(f"ATTENTION email : {problem}", file=sys.stderr)
    # Les flux SSE ne se terminent jamais d'eux-mêmes : sans délai maximal, un arrêt
    # (Ctrl+C) attendrait indéfiniment la fermeture des navigateurs connectés.
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="info",
                timeout_graceful_shutdown=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
