"""Agent AI : extraction automatique des métadonnées vers project.yaml.

Client OpenAI-compatible (GPT-4o, Ollama, vLLM, LM Studio…) via le SDK
``openai``. Sans clé API, l'agent écrit un project.yaml minimal (fallback),
ce qui garantit que l'application reste utilisable hors ligne.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from .config import LLMAgentConfig
from .models import GpsCoords, ProjectYaml, STATUTS

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Tu es un expert en ingénierie et architecture chargé d'extraire les "
    "métadonnées d'un dossier de projet. À partir de la liste des fichiers et "
    "des extraits de documents fournis, produis UNIQUEMENT un objet JSON avec "
    "EXACTEMENT ces champs :\n"
    '- "id": identifiant court unique (ex: "PROJ_2026_001"), dérivé du nom du '
    "dossier si absent des documents\n"
    '- "nom_projet": nom complet du projet\n'
    '- "ref_administrative": référence administrative / permis (chaîne vide si inconnue)\n'
    '- "promoteur": nom du promoteur ou client (chaîne vide si inconnu)\n'
    '- "adresse": adresse complète du site (chaîne vide si inconnue)\n'
    '- "coordonnees_gps": objet {"latitude": nombre, "longitude": nombre} avec les '
    'coordonnées réelles si trouvées, sinon {"latitude": 0.0, "longitude": 0.0}\n'
    '- "statut": une valeur parmi "devis", "en_cours", "finalise", "livre" '
    '(défaut "devis")\n'
    '- "etape_actuelle": étape actuelle du projet (ex: "Étude de structure")\n'
    '- "derniere_mise_a_jour": date ISO 8601 (ex: 2026-09-10T10:00:00Z), la date '
    "du jour si inconnue\n"
    "Réponds uniquement avec le JSON, sans texte additionnel ni balises markdown."
)


def derive_id(folder_name: str) -> str:
    """Génère un identifiant à partir du nom de dossier.

    Ex. : PROJ_2026_001_Tour_Alpha → PROJ_2026_001
    """
    parts = [p for p in (folder_name or "").split("_") if p]
    if len(parts) >= 3:
        return "_".join(parts[:3])
    return folder_name or "PROJET"


class AIAgent:
    """Analyse un dossier projet et écrit/actualise son project.yaml."""

    def __init__(self, cfg: LLMAgentConfig, api_key_override: str = "") -> None:
        self.cfg = cfg
        self.api_key = api_key_override or cfg.api_key

    @property
    def enabled(self) -> bool:
        """L'extraction par LLM n'est active que si une clé API est configurée."""
        return bool(self.api_key)

    # ------------------------------------------------------------------ public

    def process_folder(self, folder: Path) -> Path:
        """Analyse ``folder`` et écrit son fichier project.yaml.

        Ne lève jamais : en cas d'échec du LLM, un project.yaml minimal
        (fallback) est écrit pour que le projet reste indexable.
        """
        files = sorted(p for p in folder.iterdir() if p.is_file())
        payload = {
            "dossier": folder.name,
            "fichiers": [
                {"nom": f.name, "taille_octets": f.stat().st_size} for f in files
            ],
            "extraits_documents": self._extract_text_snippets(folder, files),
        }

        meta: Optional[ProjectYaml] = None
        if self.enabled:
            try:
                raw = self._llm_extract(payload)
                meta = self._normalize_meta(raw, folder.name)
            except Exception as exc:  # noqa: BLE001 — l'agent ne doit jamais casser la synchro
                logger.warning(
                    "Extraction IA impossible pour %s : %s — repli sur métadonnées minimales",
                    folder.name, exc,
                )
        if meta is None:
            meta = self._fallback_meta(folder.name)

        yaml_path = folder / "project.yaml"
        yaml_path.write_text(
            yaml.safe_dump(meta.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("project.yaml écrit : %s", yaml_path)
        return yaml_path

    # ------------------------------------------------------------- extraction LLM

    def _llm_extract(self, payload: dict) -> dict:
        from openai import OpenAI  # import différé : l'app fonctionne sans le SDK

        client = OpenAI(base_url=self.cfg.base_url, api_key=self.api_key, timeout=90)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ]
        kwargs = {"model": self.cfg.model, "temperature": self.cfg.temperature, "messages": messages}
        try:
            # Certains serveurs compatibles (Ollama…) n'acceptent pas response_format.
            resp = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
        except Exception:
            resp = client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or "{}"
        return json.loads(content)

    def _normalize_meta(self, data: dict, folder_name: str) -> ProjectYaml:
        """Assainit la réponse du LLM puis la valide avec pydantic."""
        if not isinstance(data, dict):
            raise ValueError("réponse IA non structurée")

        data.setdefault("id", derive_id(folder_name))
        coords = data.get("coordonnees_gps") or {}
        data["coordonnees_gps"] = {
            "latitude": float(coords.get("latitude") or 0.0),
            "longitude": float(coords.get("longitude") or 0.0),
        }
        for key in ("nom_projet", "ref_administrative", "promoteur", "adresse", "etape_actuelle"):
            if not isinstance(data.get(key), str):
                data[key] = ""
        if data.get("statut") not in STATUTS:
            data["statut"] = "devis"
        if not data.get("derniere_mise_a_jour"):
            data["derniere_mise_a_jour"] = datetime.now(timezone.utc).isoformat()
        return ProjectYaml.model_validate(data)

    def _fallback_meta(self, folder_name: str) -> ProjectYaml:
        """Métadonnées minimales écrites sans LLM (GPS 0.0 / 0.0)."""
        return ProjectYaml(
            id=derive_id(folder_name),
            nom_projet=folder_name.replace("_", " ").strip(),
            coordonnees_gps=GpsCoords(),
            derniere_mise_a_jour=datetime.now(timezone.utc),
        )

    # --------------------------------------------------- extraction de texte

    def _extract_text_snippets(self, folder: Path, files: list[Path]) -> list[dict]:
        """Extraits de texte des documents (PDF/Word/texte) pour nourrir le LLM."""
        snippets: list[dict] = []
        budget = 20_000
        used = 0
        for f in files:
            ext = f.suffix.lower()
            try:
                if ext in (".txt", ".md", ".csv", ".log"):
                    text = f.read_text(encoding="utf-8", errors="replace")
                elif ext == ".pdf":
                    text = self._pdf_text(f, max_pages=3)
                elif ext == ".docx":
                    text = self._docx_text(f)
                else:
                    continue  # DWG, images… : seul le nom de fichier est transmis
            except Exception as exc:  # noqa: BLE001
                logger.debug("Extraction texte ignorée pour %s : %s", f.name, exc)
                continue
            text = (text or "").strip()[:8_000]
            if text:
                snippets.append({"fichier": f.name, "extrait": text})
                used += len(text)
            if used >= budget:
                break
        return snippets

    @staticmethod
    def _pdf_text(path: Path, max_pages: int = 3) -> str:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(
            (page.extract_text() or "") for page in reader.pages[:max_pages]
        )

    @staticmethod
    def _docx_text(path: Path) -> str:
        from docx import Document

        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text)