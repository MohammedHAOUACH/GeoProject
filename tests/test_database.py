"""Tests : cache SQLite et filtrage multi-critères (logique ET/OU, §5)."""

from app.database import Database

ROWS = [
    {
        "id": "P1", "folder_name": "P1", "nom_projet": "Tour Alpha",
        "ref_administrative": "PERMIS-88", "promoteur": "Groupe Horizon",
        "adresse": "12 Avenue Hassan II, Tanger", "latitude": 35.7796,
        "longitude": -5.8037, "statut": "en_cours", "etape_actuelle": "Étude de structure",
        "derniere_mise_a_jour": "2026-09-10T10:00:00Z",
    },
    {
        "id": "P2", "folder_name": "P2", "nom_projet": "Résidence Oasis",
        "ref_administrative": "PERMIS-112", "promoteur": "Groupe Horizon",
        "adresse": "45 Avenue des FAR, Rabat", "latitude": 34.0209,
        "longitude": -6.8416, "statut": "devis", "etape_actuelle": "Étude préliminaire",
        "derniere_mise_a_jour": "2026-09-08T09:30:00Z",
    },
    {
        "id": "P3", "folder_name": "P3", "nom_projet": "Marina Bay Center",
        "ref_administrative": "", "promoteur": "Horizon Atlantique",
        "adresse": "Boulevard de la Corniche, Casablanca", "latitude": 33.6037,
        "longitude": -7.6091, "statut": "finalise", "etape_actuelle": "Livraison",
        "derniere_mise_a_jour": "2026-06-15T14:00:00Z",
    },
    {
        "id": "P4", "folder_name": "P4", "nom_projet": "Atelier Picard",
        "ref_administrative": "", "promoteur": "SARL Picard",
        "adresse": "Zone industrielle, Fès", "latitude": 0.0,
        "longitude": 0.0, "statut": "livre", "etape_actuelle": "Clôture",
        "derniere_mise_a_jour": "2025-03-20T08:00:00Z",
    },
]


def make_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.replace_all(ROWS)
    return db


def test_recherche_globale(tmp_path):
    db = make_db(tmp_path)
    # « horizon » matche « Groupe Horizon » ×2 + « Horizon Atlantique »
    assert len(db.list_projects(q="horizon")) == 3  # promoteur
    assert len(db.list_projects(q="groupe")) == 2   # promoteur
    assert len(db.list_projects(q="PERMIS")) == 2   # réf. administrative
    assert len(db.list_projects(q="Casablanca")) == 1  # adresse
    assert len(db.list_projects(q="tour alpha")) == 1  # nom


def test_statuts_multi_ou(tmp_path):
    db = make_db(tmp_path)
    # OU au sein du filtre statut
    result = db.list_projects(statuts=["devis", "finalise"])
    assert {r["id"] for r in result} == {"P2", "P3"}


def test_criteres_et(tmp_path):
    db = make_db(tmp_path)
    # promoteur ET statut
    result = db.list_projects(promoteur="Groupe Horizon", statuts=["en_cours", "devis"])
    assert {r["id"] for r in result} == {"P1", "P2"}
    # recherche ET statut incompatible → vide
    assert db.list_projects(q="Marina", statuts=["devis"]) == []


def test_filtre_etape(tmp_path):
    db = make_db(tmp_path)
    result = db.list_projects(etape="structure")
    assert {r["id"] for r in result} == {"P1"}
    assert db.list_projects(etape="inconnu") == []


def test_has_gps(tmp_path):
    db = make_db(tmp_path)
    result = db.list_projects(has_gps=True)
    assert {r["id"] for r in result} == {"P1", "P2", "P3"}
    assert all(r["latitude"] != 0.0 or r["longitude"] != 0.0 for r in result)


def test_ordre_tri(tmp_path):
    db = make_db(tmp_path)
    result = db.list_projects()
    dates = [r["derniere_mise_a_jour"] for r in result]
    assert dates == sorted(dates, reverse=True)


def test_promoteurs_et_stats(tmp_path):
    db = make_db(tmp_path)
    assert set(db.list_promoters()) == {"Groupe Horizon", "Horizon Atlantique", "SARL Picard"}
    stats = db.stats()
    assert stats["total"] == 4
    assert stats["by_statut"] == {"en_cours": 1, "devis": 1, "finalise": 1, "livre": 1}