"""Endpoints REST de l'API (cf. cahier des charges §6)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api")


def _folder_url(request: Request, folder_name: str) -> str:
    """Lien cliquable complet vers le dossier projet.

    - Si ``app.projects_base_url`` est renseigné dans config.yaml (ex. IP d'un
      serveur de fichiers externe), il sert de base.
    - Sinon, les dossiers sont servis par l'application elle-même sous
      ``/projects`` : la base est dérivée automatiquement de l'adresse
      utilisée par le client (localhost, IP locale, nom de domaine…).
    """
    base = (request.app.state.config.app.projects_base_url or "").rstrip("/")
    if not base:
        base = f"{str(request.base_url).rstrip('/')}/projects"
    return f"{base}/{folder_name}/"


@router.get("/projects", summary="Liste des projets filtrés (multi-critères ET)")
def list_projects(
    request: Request,
    q: Optional[str] = Query(default=None, description="Recherche textuelle partielle (nom, réf., adresse, promoteur)"),
    statut: list[str] = Query(default=[], description="Filtre multi-statuts, répétable (logique OU)"),
    promoteur: Optional[str] = Query(default=None, description="Promoteur exact"),
    etape: Optional[str] = Query(default=None, description="Mot-clé dans l'étape actuelle"),
    has_gps: bool = Query(default=False, description="Uniquement les projets géolocalisés (≠ 0,0)"),
) -> list[dict]:
    projects = request.app.state.db.list_projects(
        q=q,
        statuts=statut or None,
        promoteur=promoteur,
        etape=etape,
        has_gps=has_gps,
    )
    for p in projects:
        p["folder_url"] = _folder_url(request, p["folder_name"])
    return projects


@router.get("/projects/{project_id}", summary="Fiche projet + liste des fichiers du dossier")
def get_project(request: Request, project_id: str) -> dict:
    db = request.app.state.db
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Projet introuvable")
    project["folder_url"] = _folder_url(request, project["folder_name"])

    root = Path(request.app.state.config.app.projects_root_dir)
    folder = root / project["folder_name"]
    files: list[dict] = []
    if folder.is_dir():
        entries = [
            p for p in folder.rglob("*") if p.is_file()
        ]
        entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for f in entries:
            stat = f.stat()
            files.append({
                "nom": f.name,
                "chemin": str(f.relative_to(folder)),
                "taille_octets": stat.st_size,
                "modifie_le": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "est_metadata": f.name.lower() == "project.yaml",
            })
    return {"project": project, "files": files}


@router.get("/promoters", summary="Promoteurs uniques (filtre déroulant)")
def promoters(request: Request) -> list[str]:
    return request.app.state.db.list_promoters()


@router.get("/stats", summary="Nombre de projets groupés par statut")
def stats(request: Request) -> dict:
    return request.app.state.db.stats()


@router.post("/sync", summary="Force la resynchronisation project.yaml → SQLite")
async def sync(request: Request) -> dict:
    worker = request.app.state.sync
    # Attend la fin d'une éventuelle synchro de démarrage (jusqu'à 10 s).
    result = await asyncio.to_thread(worker.sync_once, 10.0)
    return result