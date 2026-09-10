"""Tests : parsing robuste des project.yaml (cahier des charges §9.1)."""

import yaml

from app.ai_agent import derive_id
from app.sync import parse_project_yaml

VALID_YAML = """
id: PROJ_2026_001
nom_projet: Résidence Tour Alpha
ref_administrative: PERMIS-BUILD-2026-88
promoteur: Groupe Immobilier Horizon
adresse: 12 Avenue Hassan II, Tanger
coordonnees_gps:
  latitude: 35.7796
  longitude: -5.8037
statut: en_cours
etape_actuelle: Étude de structure
derniere_mise_a_jour: "2026-09-10T10:00:00Z"
"""


def test_parse_valide(tmp_path):
    path = tmp_path / "project.yaml"
    path.write_text(VALID_YAML, encoding="utf-8")
    model = parse_project_yaml(path)
    assert model is not None
    assert model.id == "PROJ_2026_001"
    assert model.nom_projet == "Résidence Tour Alpha"
    assert model.coordonnees_gps.latitude == 35.7796
    assert model.coordonnees_gps.longitude == -5.8037
    assert model.statut == "en_cours"
    assert model.a_des_gps


def test_parse_statut_invalide_ignore(tmp_path):
    """Statut hors valeurs strictes → fichier ignoré sans planter."""
    path = tmp_path / "project.yaml"
    path.write_text(
        VALID_YAML.replace("statut: en_cours", "statut: en_attente"),
        encoding="utf-8",
    )
    assert parse_project_yaml(path) is None


def test_parse_manquant_id_ignore(tmp_path):
    path = tmp_path / "project.yaml"
    path.write_text(
        yaml.safe_dump({"nom_projet": "Sans id", "coordonnees_gps": {}}),
        encoding="utf-8",
    )
    assert parse_project_yaml(path) is None


def test_parse_yaml_malforme_ignore(tmp_path):
    path = tmp_path / "project.yaml"
    path.write_text("nom_projet: [un: yaml: cassé", encoding="utf-8")
    assert parse_project_yaml(path) is None


def test_parse_coordonnees_manquantes_defaut_zero(tmp_path):
    """GPS absent → fallback 0.0 / 0.0."""
    path = tmp_path / "project.yaml"
    path.write_text(
        yaml.safe_dump({"id": "P1", "nom_projet": "Projet"}), encoding="utf-8"
    )
    model = parse_project_yaml(path)
    assert model is not None
    assert model.coordonnees_gps.latitude == 0.0
    assert model.coordonnees_gps.longitude == 0.0
    assert not model.a_des_gps


def test_derive_id():
    assert derive_id("PROJ_2026_001_Tour_Alpha") == "PROJ_2026_001"
    assert derive_id("Dossier simple") == "Dossier simple"