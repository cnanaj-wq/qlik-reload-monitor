"""Horloges du simulateur.

- RealClock : temps réel, attentes divisées par le facteur de vitesse.
- VirtualClock : temps simulé pour les tests ; `sleep` avance l'horloge
  sans attendre. Même règle de vitesse, donc les durées sont comparables.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

MAX_SPEED = 1000.0


def _check_speed(speed: float) -> float:
    if not (0 < speed <= MAX_SPEED):
        raise ValueError(f"speed doit être dans ]0, {MAX_SPEED:g}]")
    return float(speed)


class RealClock:
    def __init__(self, speed: float = 1.0) -> None:
        self.speed = _check_speed(speed)

    def now(self) -> datetime:
        return datetime.now()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds / self.speed)


class VirtualClock:
    def __init__(self, start: datetime, speed: float = 1.0) -> None:
        self.speed = _check_speed(speed)
        self._t = start

    def now(self) -> datetime:
        return self._t

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._t += timedelta(seconds=seconds / self.speed)
