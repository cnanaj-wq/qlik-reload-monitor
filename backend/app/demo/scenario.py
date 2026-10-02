"""Description d'un scénario de démonstration (fichier YAML) et son chargement."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"

# Nom de fichier simple uniquement : empêche toute écriture hors de demo_data/.
_QVD_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoreSpec(_Strict):
    """STORE de la table dans un QVD factice écrit par blocs."""

    qvd: str
    size_mb: float = Field(gt=0, le=2048)
    chunks: int = Field(default=4, ge=1, le=200)
    chunk_interval_seconds: float = Field(default=1.0, ge=0)

    @field_validator("qvd")
    @classmethod
    def _safe_name(cls, v: str) -> str:
        if not v.lower().endswith(".qvd") or not set(v) <= _QVD_NAME_CHARS or v.startswith("."):
            raise ValueError(f"nom de QVD invalide : {v!r} (nom simple attendu, ex. VENTES.qvd)")
        return v


class ErrorSpec(_Strict):
    message: str = Field(min_length=1)
    # Fraction du chargement atteinte avant l'erreur (0 = dès le début).
    at_fraction: float = Field(default=0.5, ge=0, le=1)


class TableSpec(_Strict):
    name: str = Field(min_length=1)
    rows: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    progress_steps: int = Field(default=3, ge=1, le=100)
    warnings: list[str] = Field(default_factory=list)
    error: Optional[ErrorSpec] = None
    store: Optional[StoreSpec] = None


class SectionSpec(_Strict):
    name: str = Field(min_length=1)
    # Temps passé dans la section hors tables (affectations, variables...).
    duration_seconds: float = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    tables: list[TableSpec] = Field(default_factory=list)


class Scenario(_Strict):
    name: str = Field(min_length=1)
    description: str = ""
    app_id: str = Field(min_length=1)
    app_name: str = Field(min_length=1)
    sections: list[SectionSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _coherence(self) -> "Scenario":
        tables = [t for s in self.sections for t in s.tables]
        if sum(t.error is not None for t in tables) > 1:
            raise ValueError("un scénario ne peut contenir qu'une seule erreur")
        qvds = [t.store.qvd.lower() for t in tables if t.store]
        if len(qvds) != len(set(qvds)):
            raise ValueError("chaque QVD ne peut être écrit qu'une fois par scénario")
        return self

    def iter_tables(self):
        for section in self.sections:
            for table in section.tables:
                yield section, table


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in SCENARIOS_DIR.glob("*.yaml"))


def load_scenario(name_or_path: str | Path) -> Scenario:
    """Charge un scénario fourni par son nom (ex. 'slow') ou son chemin."""
    p = Path(name_or_path)
    if not p.suffix:
        p = SCENARIOS_DIR / f"{name_or_path}.yaml"
    if not p.is_file():
        raise FileNotFoundError(
            f"Scénario introuvable : {name_or_path}. Disponibles : {', '.join(list_scenarios())}")
    with p.open(encoding="utf-8") as f:
        return Scenario.model_validate(yaml.safe_load(f) or {})
