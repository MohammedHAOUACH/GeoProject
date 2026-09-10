"""Cache SQLite : lecture rapide indexée, synchronisée depuis les project.yaml.

Schéma et index conformes au cahier des charges (§4.2).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    folder_name TEXT UNIQUE,
    nom_projet TEXT,
    ref_administrative TEXT,
    promoteur TEXT,
    adresse TEXT,
    latitude REAL,
    longitude REAL,
    statut TEXT,
    etape_actuelle TEXT,
    derniere_mise_a_jour DATETIME
);

CREATE INDEX IF NOT EXISTS idx_projects_statut ON projects(statut);
CREATE INDEX IF NOT EXISTS idx_projects_promoteur ON projects(promoteur);
CREATE INDEX IF NOT EXISTS idx_projects_coords ON projects(latitude, longitude);
"""

_COLUMNS = (
    "id", "folder_name", "nom_projet", "ref_administrative", "promoteur",
    "adresse", "latitude", "longitude", "statut", "etape_actuelle",
    "derniere_mise_a_jour",
)


class Database:
    """Accès thread-safe au cache SQLite."""

    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ écriture

    def replace_all(self, rows: Iterable[dict]) -> int:
        """Reconstruit entièrement la table depuis les project.yaml valides."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM projects")
            cur.executemany(
                f"INSERT OR REPLACE INTO projects ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                [tuple(r.get(c) for c in _COLUMNS) for r in rows],
            )
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------ lecture

    def list_projects(
        self,
        q: Optional[str] = None,
        statuts: Optional[Iterable[str]] = None,
        promoteur: Optional[str] = None,
        etape: Optional[str] = None,
        has_gps: bool = False,
    ) -> list[dict]:
        """Filtrage multi-critères : ET entre critères, OU au sein d'un même filtre.

        Correspond à la requête SQL dynamique du cahier des charges (§5.2).
        """
        sql = "SELECT * FROM projects WHERE 1=1"
        params: list[Any] = []

        if q:
            sql += " AND (nom_projet LIKE ? OR ref_administrative LIKE ? "
            sql += "OR adresse LIKE ? OR promoteur LIKE ?)"
            like = f"%{q}%"
            params += [like, like, like, like]

        if statuts:
            statuts = list(statuts)
            sql += f" AND statut IN ({','.join('?' * len(statuts))})"
            params += statuts

        if promoteur:
            sql += " AND promoteur = ?"
            params.append(promoteur)

        if etape:
            sql += " AND etape_actuelle LIKE ?"
            params.append(f"%{etape}%")

        if has_gps:
            sql += " AND (latitude != 0.0 AND longitude != 0.0)"

        sql += " ORDER BY derniere_mise_a_jour DESC, nom_projet ASC"

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_project(self, project_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_promoters(self) -> list[str]:
        """Promoteurs uniques pour alimenter le filtre déroulant."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT promoteur FROM projects "
                "WHERE promoteur IS NOT NULL AND promoteur != '' "
                "ORDER BY promoteur COLLATE NOCASE"
            ).fetchall()
        return [r["promoteur"] for r in rows]

    def stats(self) -> dict:
        """Nombre total de projets groupés par statut."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT statut, COUNT(*) AS n FROM projects GROUP BY statut"
            ).fetchall()
            total = self._conn.execute(
                "SELECT COUNT(*) AS n FROM projects"
            ).fetchone()["n"]
        by_statut = {r["statut"]: r["n"] for r in rows}
        return {"total": total, "by_statut": by_statut}

    def close(self) -> None:
        with self._lock:
            self._conn.close()