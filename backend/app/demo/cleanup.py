"""Préparation et nettoyage contrôlé du dossier de démonstration.

Garde-fous (le nettoyage supprime des fichiers, il doit être impossible de le
diriger vers de vrais QVD) :
1. le dossier de démo ne doit ni être, ni contenir, ni se trouver dans un
   dossier Qlik déclaré dans la configuration (QVD ou logs) ;
2. un dossier existant non vide n'est utilisé que s'il porte le fichier
   marqueur créé par le monitor ;
3. seuls les fichiers *.qvd directs de `<demo>/qvd/` sont supprimés
   (pas de récursion, pas de lien symbolique).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

MARKER = ".qlik-reload-monitor-demo"
QVD_SUBDIR = "qvd"


class UnsafeDemoDirError(RuntimeError):
    pass


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def prepare_demo_dir(output_dir: str | Path,
                     forbidden_dirs: Iterable[str | Path] = ()) -> tuple[Path, list[Path]]:
    """Vérifie, crée si besoin et vide le dossier QVD de démo.

    Retourne (dossier_qvd, fichiers_supprimés).
    """
    out = Path(output_dir).resolve()
    for f in forbidden_dirs:
        fr = Path(f).resolve()
        if _overlaps(out, fr):
            raise UnsafeDemoDirError(
                f"Dossier de démo {out} en conflit avec un dossier Qlik configuré : {fr}")

    marker = out / MARKER
    if out.exists():
        if not out.is_dir():
            raise UnsafeDemoDirError(f"{out} existe et n'est pas un dossier")
        if not marker.exists():
            if any(out.iterdir()):
                raise UnsafeDemoDirError(
                    f"{out} n'est pas vide et ne porte pas le marqueur {MARKER} : "
                    "refus de l'utiliser comme dossier de démo")
            marker.write_text("Dossier géré par Qlik Reload Monitor (mode DEMO)\n",
                              encoding="utf-8")
    else:
        out.mkdir(parents=True)
        marker.write_text("Dossier géré par Qlik Reload Monitor (mode DEMO)\n",
                          encoding="utf-8")

    qvd_dir = out / QVD_SUBDIR
    qvd_dir.mkdir(exist_ok=True)

    removed: list[Path] = []
    for p in qvd_dir.iterdir():
        if p.is_symlink() or not p.is_file() or p.suffix.lower() != ".qvd":
            continue
        try:
            p.unlink()
            removed.append(p)
        except OSError as exc:  # fichier verrouillé : on journalise, on continue
            log.warning("demo_cleanup_failed", extra={"path": str(p), "error": repr(exc)})
    return qvd_dir, removed
