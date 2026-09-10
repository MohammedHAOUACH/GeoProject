"""Modèles de validation des fichiers project.yaml (pydantic).

Conformément au cahier des charges : si un fichier project.yaml est
malformé, il est ignoré et consigné dans les logs sans interrompre
l'application.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

#: Valeurs strictes du champ ``statut`` (cf. cahier des charges §3.3).
STATUTS: tuple[str, ...] = ("devis", "en_cours", "finalise", "livre")


class GpsCoords(BaseModel):
    """Coordonnées GPS d'un projet. Fallback 0.0 / 0.0."""

    latitude: float = 0.0
    longitude: float = 0.0


class ProjectYaml(BaseModel):
    """Schéma du fichier project.yaml (version 1)."""

    id: str
    nom_projet: str = ""
    ref_administrative: str = ""
    promoteur: str = ""
    adresse: str = ""
    coordonnees_gps: GpsCoords = Field(default_factory=GpsCoords)
    statut: Literal["devis", "en_cours", "finalise", "livre"] = "devis"
    etape_actuelle: str = ""
    derniere_mise_a_jour: Optional[datetime] = None

    @field_validator("id")
    @classmethod
    def _id_non_vide(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("le champ 'id' est obligatoire")
        return value

    @property
    def a_des_gps(self) -> bool:
        """Vrai si le projet possède des coordonnées réelles (≠ 0,0)."""
        return self.coordonnees_gps.latitude != 0.0 or self.coordonnees_gps.longitude != 0.0