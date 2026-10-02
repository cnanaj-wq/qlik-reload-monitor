"""Outils de notification :

    python -m app.notifications test-email [--config chemin]
        Construit un email de test et l'envoie, sauf si dry_run est actif.
        Aucun reload n'est lu, déclenché ni modifié.

    python -m app.notifications preview <reload_id> [--config chemin]
        Affiche l'email d'erreur qui serait généré pour ce reload (rien n'est envoyé).

À lancer depuis le dossier backend/ (environnement virtuel activé).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from ..config import load_config
from ..engine import EventEngine
from ..storage.db import open_db
from ..storage.rows import row_to_event
from .base import DeliveryError, PermanentDeliveryError
from .email_notifier import EmailNotifier
from .messages import build_reload_error_message, build_test_message

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config.yaml"


def _print_config(cfg) -> None:
    e = cfg.notifications.email
    print("Configuration email")
    print(f"  activé        : {'oui' if e.enabled else 'non'}")
    print(f"  dry_run       : {'oui (rien ne sera envoyé)' if e.dry_run else 'non (envoi réel)'}")
    print(f"  destinataires : {', '.join(e.recipients) or 'aucun'}")
    print(f"  expéditeur    : {e.from_address or '(non défini)'}")
    print(f"  serveur SMTP  : {e.smtp.host or '(non défini)'}:{e.smtp.port}"
          f"  {'STARTTLS' if e.smtp.use_tls and not e.smtp.use_ssl else 'SSL' if e.smtp.use_ssl else 'sans chiffrement'}")
    print(f"  mot de passe  : {'défini' if e.smtp.password else 'non défini'}")
    if e.missing_env:
        print(f"  variables d'environnement absentes : {', '.join(e.missing_env)}")
    print()


def _show(message) -> None:
    print("─" * 72)
    print(f"Sujet : {message.subject}")
    print("─" * 72)
    print(message.body)
    print("─" * 72)


def cmd_test_email(cfg) -> int:
    e = cfg.notifications.email
    _print_config(cfg)
    if not e.recipients:
        print("❌ Aucun destinataire configuré (notifications.email.recipients).")
        return 1
    message = build_test_message(datetime.now(), recipients=e.recipients, smtp_host=e.smtp.host)
    _show(message)
    if e.dry_run:
        print("EMAIL DRY RUN : rien n'a été envoyé. Mettre dry_run: false pour un envoi réel.")
        return 0
    notifier = EmailNotifier(e)
    ok = True
    for recipient in e.recipients:
        error = None
        for attempt in range(1, e.max_attempts + 1):
            try:
                notifier.deliver(message, recipient)
                print(f"✅ Envoyé à {recipient} (tentative {attempt})")
                error = None
                break
            except DeliveryError as exc:
                error = str(exc)
                if isinstance(exc, PermanentDeliveryError):
                    break
        if error:
            ok = False
            print(f"❌ Échec pour {recipient} : {error}")
    return 0 if ok else 1


def cmd_preview(cfg, reload_id: str) -> int:
    conn = open_db(cfg.database.path)
    try:
        state = EventEngine(conn).get_reload_state(reload_id)
        if state is None:
            print(f"Reload introuvable : {reload_id}")
            return 1
        err = conn.execute("SELECT * FROM events WHERE reload_id = ? AND event_type = 'ERROR'"
                           " ORDER BY timestamp, seq LIMIT 1", (reload_id,)).fetchone()
        end = conn.execute("SELECT * FROM events WHERE reload_id = ? AND event_type = 'RELOAD_END'"
                           " ORDER BY timestamp DESC, seq DESC LIMIT 1", (reload_id,)).fetchone()
        _show(build_reload_error_message(state, row_to_event(err) if err else None,
                                         row_to_event(end) if end else None))
        if state.status.value != "ERROR" or state.is_running:
            print(f"Remarque : ce reload est {state.status.value}"
                  f"{' (en cours)' if state.is_running else ''} ; aucun email ne serait envoyé.")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.notifications")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("test-email", help="envoyer un email de test (respecte dry_run)")
    prev = sub.add_parser("preview", help="afficher l'email d'erreur d'un reload")
    prev.add_argument("reload_id")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load_config(args.config)
    if args.command == "test-email":
        return cmd_test_email(cfg)
    return cmd_preview(cfg, args.reload_id)


if __name__ == "__main__":
    raise SystemExit(main())
