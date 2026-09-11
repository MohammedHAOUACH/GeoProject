"""Agent AI : extraction automatique des métadonnées vers project.yaml.

Client OpenAI-compatible (GPT-4o, Ollama, vLLM, LM Studio…) via le SDK
``openai``. Sans clé API, l'agent écrit un project.yaml minimal (fallback),
ce qui garantit que l'application reste utilisable hors ligne.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

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


def parse_json_content(content: str) -> dict:
    """Parse la réponse du LLM en objet JSON.

    Tolère les clôtures markdown (```` ```json … ``` ````) et le texte
    parasite autour de l'objet — comportement fréquent des modèles locaux.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("réponse IA non structurée")
    return data


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

    # Le serveur IA (LM Studio, Ollama…) est interrogé au plus toutes les
    # PROBE_INTERVAL secondes quand il est indisponible (circuit breaker) :
    # la synchronisation reste instantanée au lieu d'attendre le timeout LLM
    # sur chaque dossier.
    PROBE_INTERVAL = 60.0

    def __init__(self, cfg: LLMAgentConfig, api_key_override: str = "") -> None:
        self.cfg = cfg
        self.api_key = api_key_override or cfg.api_key
        # Timestamp du dernier échec de sonde (None = jamais échoué). None est
        # indispensable : time.monotonic() peut être proche de 0 après un boot,
        # un 0.0 sentinelle bloquerait toute sonde pendant PROBE_INTERVAL.
        self._last_failure_ts: Optional[float] = None
        # Certains serveurs (LM Studio récent, Ollama…) rejettent
        # response_format json_object : après un 400, ne plus le proposer.
        self._json_object_supported: bool = True

    @property
    def enabled(self) -> bool:
        """L'extraction par LLM n'est active que si une clé API est configurée."""
        return bool(self.api_key)

    def _server_reachable(self) -> bool:
        """Sonde rapide (< 3 s) du serveur compatible OpenAI.

        Évite d'attendre le timeout complet du LLM sur chaque dossier quand le
        serveur (LM Studio…) est arrêté : après un échec, on ne retente une
        vraie requête que toutes les PROBE_INTERVAL secondes.
        """
        if (
            self._last_failure_ts is not None
            and time.monotonic() - self._last_failure_ts < self.PROBE_INTERVAL
        ):
            return False  # récemment injoignable → échec immédiat, pas d'attente
        target = self._host_port()
        if target is None:
            return True  # base_url non parsable → laisser le SDK tenter l'appel
        host, port = target
        try:
            with socket.create_connection((host, port), timeout=3.0):
                return True
        except OSError:
            self._last_failure_ts = time.monotonic()
            return False

    def _host_port(self) -> Optional[tuple[str, int]]:
        """Extrait (host, port) de cfg.base_url (http://localhost:1234/v1)."""
        parsed = urlparse(self.cfg.base_url)
        if parsed.hostname:
            return parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        return None

    # ------------------------------------------------------------------ public

    def process_folder(self, folder: Path, use_llm: bool = False) -> Path:
        """Analyse ``folder`` et écrit/actualise son fichier project.yaml.

        ``use_llm=False`` (défaut) : **indexation seule** — écrit un fallback
        minimal uniquement si le dossier n'a pas encore de project.yaml. Les
        métadonnées existantes ne sont jamais réécrites : l'indexation reste
        instantanée (le serveur web n'est jamais ralenti par le LLM).

        ``use_llm=True`` : extraction complète par le LLM (appel manuel,
        très gourmand —cf. bouton 🤖 de l'interface). Ne lève jamais :
        - LLM indisponible ou réponse inexploitable et project.yaml existant
          → le fichier existant est **conservé** (jamais dégradé par un
          fallback vide) ;
        - LLM indisponible sans project.yaml existant → écriture d'un
          project.yaml minimal pour que le projet reste indexable.
        """
        existing_yaml = folder / "project.yaml"
        existing_content: Optional[str] = None
        if existing_yaml.is_file():
            try:
                existing_content = existing_yaml.read_text(encoding="utf-8", errors="replace")
            except OSError:
                existing_content = None

        if not use_llm:
            # Indexation seule : ne touche jamais à un project.yaml existant.
            if existing_content is not None:
                return existing_yaml
            meta = self._fallback_meta(folder.name)
            yaml_path = existing_yaml
            yaml_path.write_text(
                yaml.safe_dump(meta.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            logger.info("project.yaml minimal écrit (indexation) : %s", yaml_path)
            return yaml_path

        files = sorted(p for p in folder.rglob("*") if p.is_file())
        if not self.enabled:
            local_meta = self._local_extract(folder, files, existing_content)
            if local_meta is not None:
                yaml_path = folder / "project.yaml"
                yaml_path.write_text(
                    yaml.safe_dump(local_meta.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )
                logger.info("Métadonnées extraites localement : %s", yaml_path)
            return existing_yaml if existing_content is not None and local_meta is None else yaml_path
        payload = {
            "dossier": folder.name,
            "fichiers": [
                {"nom": f.name, "taille_octets": f.stat().st_size} for f in files
            ],
            "extraits_documents": self._extract_text_snippets(folder, files),
        }

        meta: Optional[ProjectYaml] = None
        server_ok = self._server_reachable()
        if self.enabled and server_ok:
            logger.info("Extraction LLM du dossier : %s", folder.name)
            try:
                raw = self._llm_extract(payload)
                meta = self._normalize_meta(raw, folder.name)
                self._last_failure_ts = None  # succès → sonde à nouveau autorisée
            except Exception as exc:  # noqa: BLE001 — l'agent ne doit jamais casser la synchro
                logger.warning(
                    "Extraction IA impossible pour %s : %s",
                    folder.name, exc,
                )
        elif self.enabled and not server_ok:
            logger.warning(
                "Serveur IA injoignable (%s) — extraction LLM ignorée pour %s",
                self.cfg.base_url, folder.name,
            )
        if meta is None:
            if existing_content is not None:
                # Serveur IA en échec ou réponse inexploitable : on ne dégrade
                # jamais des métadonnées existantes par un fallback vide.
                logger.info("project.yaml existant conservé : %s", existing_yaml)
                return existing_yaml
            meta = self._fallback_meta(folder.name)

        yaml_path = folder / "project.yaml"
        yaml_path.write_text(
            yaml.safe_dump(meta.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("project.yaml écrit : %s", yaml_path)
        return yaml_path

    def _local_extract(
        self, folder: Path, files: list[Path], existing_content: Optional[str]
    ) -> Optional[ProjectYaml]:
        """Extrait des métadonnées sans LLM à partir des documents lisibles."""
        snippets = self._extract_text_snippets(folder, files)
        text = "\n".join(item["extrait"] for item in snippets)
        if not text and existing_content is not None:
            return None

        base = yaml.safe_load(existing_content) if existing_content else {}
        if not isinstance(base, dict):
            base = {}
        provided_fields = set(base)
        base.setdefault("id", derive_id(folder.name))
        base.setdefault("nom_projet", folder.name.replace("_", " ").strip())
        base.setdefault("adresse", "")
        base.setdefault("coordonnees_gps", {"latitude": 0.0, "longitude": 0.0})
        base.setdefault("statut", "devis")
        base.setdefault("derniere_mise_a_jour", datetime.now(timezone.utc).isoformat())

        patterns = {
            "nom_projet": r"(?:projet|project|nom)\s*:\s*(.+)",
            "adresse": r"(?:adresse|site|lieu|situation)\s*:\s*(.+)",
            "promoteur": r"(?:promoteur|client|ma[iî]tre\s+d['’]ouvrage)\s*:\s*(.+)",
            "ref_administrative": r"(?:r[ée]f(?:[ée]rence)?|permis)\s*:\s*(.+)",
            "etape_actuelle": r"(?:[ée]tape|phase)\s*:\s*(.+)",
        }
        for field, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            generated_name = folder.name.replace("_", " ").strip()
            can_fill = field not in provided_fields or not base.get(field)
            if field == "nom_projet" and base.get(field) == generated_name:
                can_fill = True
            if match and can_fill:
                base[field] = match.group(1).strip()

        gps = base.get("coordonnees_gps") or {}
        coordinate_match = re.search(
            r"latitude\s*[:=]\s*(-?\d+(?:\.\d+)?)\D+longitude\s*[:=]\s*(-?\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if coordinate_match and not (gps.get("latitude") or gps.get("longitude")):
            gps = {
                "latitude": float(coordinate_match.group(1)),
                "longitude": float(coordinate_match.group(2)),
            }
        base["coordonnees_gps"] = gps
        return self._normalize_meta(base, folder.name)

    # ------------------------------------------------------------- extraction LLM

    def _llm_extract(self, payload: dict) -> dict:
        from openai import OpenAI  # import différé : l'app fonctionne sans le SDK

        client = OpenAI(
            base_url=self.cfg.base_url,
            api_key=self.api_key,
            timeout=self.cfg.timeout_seconds,
            max_retries=self.cfg.max_retries,
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ]
        kwargs = {"model": self.cfg.model, "temperature": self.cfg.temperature, "messages": messages}
        reasoning_effort = getattr(self.cfg, "reasoning_effort", "")
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        if self._json_object_supported:
            try:
                resp = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
            except Exception:
                # Serveur sans json_object (répond 400) → mémoriser et réessayer
                # sans ce champ (le prompt système exige déjà un JSON strict).
                self._json_object_supported = False
                resp = client.chat.completions.create(**kwargs)
        else:
            resp = client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or "{}"
        return parse_json_content(content)

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
                elif ext == ".dxf":
                    text = self._dxf_text(f)
                else:
                    continue  # DWG binaire, images… non lisibles sans convertisseur
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
        text = "\n".join(
            (page.extract_text() or "") for page in reader.pages[:max_pages]
        )
        if len(text.strip()) >= 20:
            return text
        return AIAgent._pdf_ocr(path, max_pages=max_pages)

    @staticmethod
    def _pdf_ocr(path: Path, max_pages: int = 3) -> str:
        """OCR optionnel des PDF scannés, page par page."""
        try:
            import fitz
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            logger.warning("OCR indisponible pour %s : dépendance manquante (%s)", path.name, exc)
            return ""

        document = fitz.open(str(path))
        texts: list[str] = []
        try:
            for page in document[:max_pages]:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
                try:
                    texts.append(pytesseract.image_to_string(image, lang="fra+eng"))
                except pytesseract.TesseractNotFoundError as exc:
                    logger.warning("Moteur Tesseract absent pour %s : %s", path.name, exc)
                    return ""
                except pytesseract.TesseractError:
                    texts.append(pytesseract.image_to_string(image, lang="eng"))
        finally:
            document.close()
        return "\n".join(texts)

    @staticmethod
    def _docx_text(path: Path) -> str:
        from docx import Document

        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text)

    @staticmethod
    def _dxf_text(path: Path) -> str:
        """Lit les textes et attributs d'un plan DXF avec ezdxf."""
        import ezdxf

        doc = ezdxf.readfile(str(path))
        texts: list[str] = []
        for entity in doc.modelspace().query("TEXT MTEXT ATTRIB ATTDEF"):
            value = getattr(entity.dxf, "text", "") or getattr(entity.dxf, "default", "")
            if value:
                texts.append(str(value))
        return "\n".join(texts)