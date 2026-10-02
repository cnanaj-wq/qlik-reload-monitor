"""Formats d'affichage partagés (console de démo, emails)."""

from __future__ import annotations

KIB = 1024
MIB = 1024 * 1024
GIB = 1024 * 1024 * 1024


def fmt_int(n: int) -> str:
    """1842556 -> '1 842 556'."""
    return f"{n:,}".replace(",", " ")


def fmt_size(n: int) -> str:
    """Taille en unités binaires (comme l'Explorateur Windows) : 838 KB, 4.6 MB, 1.4 GB."""
    if n >= GIB:
        return f"{n / GIB:.1f} GB"
    if n >= MIB:
        return f"{n / MIB:.1f} MB"
    if n >= KIB:
        return f"{round(n / KIB)} KB"
    return f"{n} B"


def fmt_duration(ms: int) -> str:
    """832 -> '0.8 s' ; 47800 -> '47.8 s' ; 79000 -> '1 min 19 s' ; 3723000 -> '1 h 02 min 03 s'."""
    s = ms / 1000
    if s < 60:
        return f"{s:.1f} s"
    total = round(s)
    h, rest = divmod(total, 3600)
    m, sec = divmod(rest, 60)
    if h:
        return f"{h} h {m:02d} min {sec:02d} s"
    return f"{m} min {sec:02d} s"
