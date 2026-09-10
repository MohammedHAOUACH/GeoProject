"""Tests : synchronisation project.yaml → SQLite et agent hors ligne."""

import yaml

from app.ai_agent import AIAgent
from app.config import AppConfig, Config, LLMAgentConfig
from app.database import Database
from app.sync import SyncWorker


def make_worker(tmp_path, root: str):
    cfg = Config(
        app=AppConfig(projects_root_dir=str(tmp_path / root)),
        llm_agent=LLMAgentConfig(api_key=""),  # agent LLM désactivé (hors ligne)
    )
    db = Database(str(tmp_path / "cache.db"))
    agent = AIAgent(cfg.llm_agent)
    return SyncWorker(cfg, db, agent), db


def write_project(root, folder, data):
    folder = root / folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_sync_indexe_les_yaml_valides(tmp_path):
    root = tmp_path / "projets"
    write_project(root, "PROJ_A", {
        "id": "PROJ_A", "nom_projet": "Projet A", "statut": "en_cours",
        "coordonnees_gps": {"latitude": 30.0, "longitude": -9.0},
    })
    write_project(root, "PROJ_B", {
        "id": "PROJ_B", "nom_projet": "Projet B", "statut": "devis",
    })
    # Un dossier sans project.yaml → l'agent (hors ligne) écrit un fallback
    (root / "PROJ_C").mkdir()

    worker, db = make_worker(tmp_path, "projets")
    result = worker.sync_once()

    assert result["status"] == "ok"
    assert result["projets_indexes"] == 3
    assert db.stats()["total"] == 3
    assert db.get_project("PROJ_B")["latitude"] == 0.0

    fallback = root / "PROJ_C" / "project.yaml"
    assert fallback.exists()
    data = yaml.safe_load(fallback.read_text(encoding="utf-8"))
    assert data["coordonnees_gps"] == {"latitude": 0.0, "longitude": 0.0}


def test_sync_ignore_yaml_invalide_sans_planter(tmp_path):
    root = tmp_path / "projets"
    write_project(root, "PROJ_OK", {"id": "OK1", "nom_projet": "Valide"})
    bad = root / "PROJ_BAD"
    bad.mkdir()
    (bad / "project.yaml").write_text("statut: valeur_invalide", encoding="utf-8")

    worker, db = make_worker(tmp_path, "projets")
    result = worker.sync_once()

    assert result["status"] == "ok"
    assert result["projets_indexes"] == 1
    assert result["erreurs"] == 1
    assert db.stats()["total"] == 1


def test_needs_ai_ne_reescrit_pas_sans_llm(tmp_path):
    """Hors ligne : un project.yaml existant n'est jamais réécrit."""
    root = tmp_path / "projets"
    folder = root / "PROJ_A"
    folder.mkdir(parents=True)
    path = folder / "project.yaml"
    path.write_text(yaml.safe_dump({"id": "A", "nom_projet": "Original"}), encoding="utf-8")
    # Fichier plus récent que project.yaml
    (folder / "nouveau_doc.txt").write_text("contenu", encoding="utf-8")

    worker, _ = make_worker(tmp_path, "projets")
    assert worker._needs_ai(folder) is False
    worker.sync_once()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["nom_projet"] == "Original"