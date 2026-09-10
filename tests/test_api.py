"""Tests des endpoints REST (cahier des charges §6)."""

import yaml

import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture()
def client(tmp_path):
    root = tmp_path / "projets"
    for name, meta in {
        "PROJ_2026_001_Tour_Alpha": {
            "id": "PROJ_2026_001", "nom_projet": "Tour Alpha",
            "ref_administrative": "PERMIS-88", "promoteur": "Groupe Horizon",
            "adresse": "12 Avenue Hassan II, Tanger",
            "coordonnees_gps": {"latitude": 35.7796, "longitude": -5.8037},
            "statut": "en_cours", "etape_actuelle": "Étude de structure",
            "derniere_mise_a_jour": "2026-09-10T10:00:00Z",
        },
        "PROJ_2026_002_Residence_Oasis": {
            "id": "PROJ_2026_002", "nom_projet": "Résidence Oasis",
            "ref_administrative": "PERMIS-112", "promoteur": "Groupe Horizon",
            "adresse": "45 Avenue des FAR, Rabat",
            "coordonnees_gps": {"latitude": 34.0209, "longitude": -6.8416},
            "statut": "devis", "etape_actuelle": "Étude préliminaire",
            "derniere_mise_a_jour": "2026-09-08T09:30:00Z",
        },
    }.items():
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "project.yaml").write_text(yaml.safe_dump(meta), encoding="utf-8")
        (folder / "plan.dwg").write_text("faux dwg", encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({
        "app": {
            "projects_root_dir": str(root),
            "db_path": str(tmp_path / "cache.db"),
            "projects_base_url": "http://192.168.1.50:8080/data/projects",
        },
        "llm_agent": {"api_key": ""},
    }), encoding="utf-8")

    app = create_app(str(cfg))
    with TestClient(app) as c:
        yield c


def test_projects_sans_filtre(client):
    r = client.get("/api/projects")
    assert r.status_code == 200
    projects = r.json()
    assert len(projects) == 2
    # Lien cliquable du dossier (config projects_base_url)
    assert projects[0]["folder_url"] == \
        "http://192.168.1.50:8080/data/projects/PROJ_2026_001_Tour_Alpha/"


def test_filtres_multi_criteres(client):
    r = client.get("/api/projects", params=[
        ("statut", "devis"), ("promoteur", "Groupe Horizon"),
    ])
    assert [p["id"] for p in r.json()] == ["PROJ_2026_002"]

    r = client.get("/api/projects", params={"q": "Tour", "has_gps": "true"})
    assert [p["id"] for p in r.json()] == ["PROJ_2026_001"]


def test_detail_projet_avec_fichiers(client):
    r = client.get("/api/projects/PROJ_2026_001")
    assert r.status_code == 200
    body = r.json()
    assert body["project"]["nom_projet"] == "Tour Alpha"
    assert body["project"]["folder_url"] == \
        "http://192.168.1.50:8080/data/projects/PROJ_2026_001_Tour_Alpha/"
    noms = {f["nom"] for f in body["files"]}
    assert noms == {"project.yaml", "plan.dwg"}


def test_detail_inconnu_404(client):
    assert client.get("/api/projects/NOPE").status_code == 404


def test_promoteurs(client):
    r = client.get("/api/promoters")
    assert r.json() == ["Groupe Horizon"]


def test_stats(client):
    r = client.get("/api/stats")
    body = r.json()
    assert body["total"] == 2
    assert body["by_statut"] == {"en_cours": 1, "devis": 1}


def test_sync_force(client):
    r = client.post("/api/sync")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert client.get("/api/stats").json()["total"] == 2


def test_sans_base_url_lien_auto(tmp_path):
    """projects_base_url vide → lien auto-dérivé de l'adresse du client (/projects)."""
    root = tmp_path / "projets"
    folder = root / "PROJ_A"
    folder.mkdir(parents=True)
    (folder / "project.yaml").write_text(yaml.safe_dump({"id": "PROJ_A", "nom_projet": "A"}), encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({
        "app": {"projects_root_dir": str(root), "db_path": str(tmp_path / "cache.db")},
        "llm_agent": {"api_key": ""},
    }), encoding="utf-8")
    app = create_app(str(cfg))
    with TestClient(app) as c:
        assert c.get("/api/projects").json()[0]["folder_url"] == \
            "http://testserver/projects/PROJ_A/"


def test_dossiers_servis_en_http(client):
    """Les dossiers projets sont accessibles en lecture seule via /projects."""
    r = client.get("/projects/PROJ_2026_001_Tour_Alpha/plan.dwg")
    assert r.status_code == 200
    assert r.text == "faux dwg"

    # Listing HTML du dossier
    r = client.get("/projects/PROJ_2026_001_Tour_Alpha/")
    assert r.status_code == 200
    assert "plan.dwg" in r.text
    assert "project.yaml" in r.text

    # Échappement hors racine interdit
    assert client.get("/projects/../config.yaml").status_code == 404
    assert client.get("/projects/%2e%2e/config.yaml").status_code == 404


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}