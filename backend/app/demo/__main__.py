"""Point d'entrée : python -m app.demo <scénario> [--speed N] [--config chemin]

À lancer depuis le dossier backend/ (environnement virtuel activé).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..config import load_config
from ..engine import EventEngine
from ..models import RunMode
from ..notifications import build_service
from ..storage.db import open_db
from ..watcher import QvdWatcher
from .cleanup import UnsafeDemoDirError, prepare_demo_dir
from .clock import MAX_SPEED, RealClock
from .console import ConsoleReporter
from .scenario import list_scenarios, load_scenario
from .simulator import ReloadSimulator

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config.yaml"


def _speed(value: str) -> float:
    v = float(value)
    if not (0 < v <= MAX_SPEED):
        raise argparse.ArgumentTypeError(f"doit être dans ]0, {MAX_SPEED:g}]")
    return v


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.demo",
                                     description="Simule un reload Qlik (mode DEMO).")
    parser.add_argument("scenario", choices=list_scenarios())
    parser.add_argument("--speed", type=_speed, default=1.0,
                        help="facteur d'accélération (ex. 10 = 10x plus rapide)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):  # émojis lisibles dans la console Windows
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    cfg = load_config(args.config)
    if cfg.mode != RunMode.DEMO:
        print("Refus : la configuration n'est pas en mode demo (mode: demo attendu).",
              file=sys.stderr)
        return 2

    try:
        qvd_dir, removed = prepare_demo_dir(
            cfg.demo.output_dir, [*cfg.qvd.directories, *cfg.qlik.log_paths])
    except UnsafeDemoDirError as exc:
        print(f"Refus : {exc}", file=sys.stderr)
        return 2

    scenario = load_scenario(args.scenario)
    clock = RealClock(args.speed)
    conn = open_db(cfg.database.path)
    reporter = ConsoleReporter()
    engine = EventEngine(conn,
                         stable_after_measures=cfg.monitoring.stable_after_measures,
                         min_delta_bytes=cfg.monitoring.min_delta_bytes,
                         clock=clock.now)
    # Notifications : abonnées au moteur, qui ignore tout de l'email.
    notifications = build_service(cfg, on_result=reporter.on_notification)
    engine.add_listener(notifications.on_event)
    watcher = QvdWatcher(engine, [qvd_dir], extensions=cfg.qvd.extensions,
                         clock=clock.now, on_event=reporter.on_event,
                         on_measure=reporter.on_measure)
    sim = ReloadSimulator(engine, scenario, qvd_dir=qvd_dir, clock=clock, watcher=watcher,
                          polling_interval_seconds=cfg.monitoring.polling_interval_seconds,
                          stable_after_measures=cfg.monitoring.stable_after_measures,
                          on_event=reporter.on_event)

    print(f"Scénario « {scenario.name} » — vitesse x{args.speed:g} — "
          f"{len(removed)} QVD de démo supprimé(s) dans {qvd_dir}\n")
    try:
        reload_id = sim.run()
        reporter.summary(engine.get_reload_state(reload_id), str(cfg.database.path))
    except KeyboardInterrupt:
        print("\nSimulation interrompue : le reload reste « en cours » dans la base.")
        return 130
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
