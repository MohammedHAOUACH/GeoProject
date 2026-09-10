"""Tests : agent AI — parsing JSON robuste et client LLM configurable.

Ces tests sont hors ligne (aucun serveur requis). Le test optionnel
``test_live_lmstudio_extraction`` cible un LM Studio local ; il est
exécuté seulement si ``RUN_LMSTUDIO_TESTS=1`` est défini.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from app.ai_agent import AIAgent, parse_json_content
from app.config import Config, load_config

# ------------------------------------------------------------------ parsing JSON


def test_parse_json_simple():
    assert parse_json_content('{"id": "P1"}') == {"id": "P1"}


def test_parse_json_avec_cloture_markdown():
    content = '```json\n{"id": "P1", "nom_projet": "Tour"}\n```'
    assert parse_json_content(content) == {"id": "P1", "nom_projet": "Tour"}


def test_parse_json_avec_texte_parasite():
    """Les modèles locaux raisonnent parfois avant/après le JSON."""
    content = 'Voici le résultat :\n{"id": "P1"}\nCordialement.'
    assert parse_json_content(content) == {"id": "P1"}


def test_parse_json_sans_objet_leve():
    with pytest.raises(Exception):
        parse_json_content("pas de json ici")


def test_parse_json_non_dict_leve():
    with pytest.raises(Exception):
        parse_json_content('["liste", "pas", "objet"]')


# ------------------------------------------------------- configuration LLM


def test_config_lmstudio(tmp_path):
    """Le client pointe vers LM Studio avec timeout étendu et sans réessais."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        yaml.safe_dump({
            "llm_agent": {
                "base_url": "http://localhost:1234/v1",
                "api_key": "lm-studio",
                "model": "prism-ml/bonsai-27b",
                "timeout_seconds": 600,
                "max_retries": 0,
            },
        }),
        encoding="utf-8",
    )
    cfg = load_config(str(cfg_file))
    assert cfg.llm_agent.base_url == "http://localhost:1234/v1"
    assert cfg.llm_agent.timeout_seconds == 600
    assert cfg.llm_agent.max_retries == 0
    agent = AIAgent(cfg.llm_agent, api_key_override=cfg.effective_api_key)
    assert agent.enabled


def test_config_defauts_compatibles():
    """Les valeurs par défaut restent OpenAI, timeout/retries renseignés."""
    cfg = Config()
    assert cfg.llm_agent.base_url == "https://api.openai.com/v1"
    assert cfg.llm_agent.timeout_seconds > 0
    assert cfg.llm_agent.max_retries >= 0
    assert not AIAgent(cfg.llm_agent).enabled  # pas de clé → hors ligne


# --------------------------------------------------- extraction avec faux LLM


class _FakeCompletions:
    """Renvoie une réponse figée, en enregistrant les kwargs de l'appel."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs: Any):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeClient:
    """Se substitue au SDK openai pour capter timeout / max_retries."""

    def __init__(self, completions: _FakeCompletions, **client_kwargs: Any) -> None:
        self.client_kwargs = client_kwargs
        self.chat = SimpleNamespace(completions=completions)


@pytest.fixture()
def fake_llm_factory(monkeypatch):
    """Remplace openai.OpenAI ; renvoie le faux ``completions`` créé.

    La sonde de joignabilité est neutralisée (serveur simulé toujours up) :
    ces tests ciblent la mécanique d'appel, pas le réseau.
    """
    import app.ai_agent as mod

    monkeypatch.setattr(mod.AIAgent, "_server_reachable", lambda self: True)

    created: dict[str, Any] = {"clients": []}

    def _install(content: str) -> _FakeCompletions:
        completions = _FakeCompletions(content)

        def _factory(**client_kwargs: Any) -> _FakeClient:
            client = _FakeClient(completions, **client_kwargs)
            created["clients"].append(client)
            return client

        # ``from openai import OpenAI`` est fait dans la fonction → patch du paquet.
        monkeypatch.setattr("openai.OpenAI", _factory)
        return completions

    _install.clients = created["clients"]  # type: ignore[attr-defined]
    return _install


def _make_agent(timeout: float = 10.0) -> AIAgent:
    cfg = SimpleNamespace(
        base_url="http://localhost:1234/v1", api_key="x", model="m",
        temperature=0.1, timeout_seconds=timeout, max_retries=0,
    )
    return AIAgent(cfg, api_key_override="x")


def _write_doc(folder: Path, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "note_synthese.txt").write_text(text, encoding="utf-8")


def test_extraction_ecrit_yaml_complet(tmp_path, fake_llm_factory):
    """Réponse LLM valide → project.yaml complet écrit et validé."""
    fake_llm_factory(
        '{"id": "PROJ_2026_003", "nom_projet": "Résidence Al Manzah", '
        '"promoteur": "Al Omr Immobilier", "statut": "en_cours", '
        '"coordonnees_gps": {"latitude": 33.5731, "longitude": -7.5898}}'
    )
    agent = _make_agent()
    folder = tmp_path / "PROJ_2026_003_Residence_Al_Manzah"
    _write_doc(folder, "Promoteur : Al Omr Immobilier. Casablanca.")

    agent.process_folder(folder)
    data = yaml.safe_load((folder / "project.yaml").read_text(encoding="utf-8"))
    assert data["nom_projet"] == "Résidence Al Manzah"
    assert data["promoteur"] == "Al Omr Immobilier"
    assert data["statut"] == "en_cours"
    assert data["coordonnees_gps"] == {"latitude": 33.5731, "longitude": -7.5898}


def test_extraction_passe_timeout_et_retries_au_client(tmp_path, fake_llm_factory):
    """Config locale lente (LM Studio) → timeout du client et 0 réessai."""
    completions = fake_llm_factory('{"id": "P1"}')
    agent = _make_agent(timeout=600.0)
    folder = tmp_path / "PROJ_X"
    _write_doc(folder, "doc")

    agent.process_folder(folder)

    client = fake_llm_factory.clients[0]
    assert client.client_kwargs["timeout"] == 600.0
    assert client.client_kwargs["max_retries"] == 0
    assert client.client_kwargs["base_url"] == "http://localhost:1234/v1"
    call_kwargs = completions.calls[0]
    assert call_kwargs["model"] == "m"
    assert call_kwargs["temperature"] == 0.1
    # Le prompt système demande bien le JSON strict
    assert call_kwargs["messages"][0]["role"] == "system"
    assert "JSON" in call_kwargs["messages"][0]["content"]


def test_reponse_garbage_repli_fallback(tmp_path, fake_llm_factory):
    """Réponse LLM inexploitable, sans project.yaml → fallback minimal."""
    fake_llm_factory("je ne sais pas produire de json, désolé")
    agent = _make_agent()
    folder = tmp_path / "PROJ_2026_005_Complexe_Atlassia"
    _write_doc(folder, "Promoteur : Groupe Atlassia Développement. Agadir.")

    agent.process_folder(folder)  # ne lève pas
    data = yaml.safe_load((folder / "project.yaml").read_text(encoding="utf-8"))
    assert data["id"] == "PROJ_2026_005"  # dérivé du dossier
    assert data["coordonnees_gps"] == {"latitude": 0.0, "longitude": 0.0}


# --------------------------------------------- serveur IA en échec (hors ligne)


def test_serveur_down_yaml_existant_conserve(tmp_path, monkeypatch):
    """LM Studio arrêté + project.yaml existant → fichier JAMAIS écrasé."""
    import app.ai_agent as mod

    def _refuse(*args: Any, **kwargs: Any):
        raise OSError("connexion refusée")

    monkeypatch.setattr(mod.socket, "create_connection", _refuse)
    agent = _make_agent()
    folder = tmp_path / "PROJ_X"
    _write_doc(folder, "Promoteur : SARL Test.")
    yaml_path = folder / "project.yaml"
    yaml_path.write_text(
        yaml.safe_dump({
            "id": "PROJ_X", "nom_projet": "Métadonnées réelles",
            "promoteur": "SARL Test",
            "coordonnees_gps": {"latitude": 31.5, "longitude": -7.9},
        }),
        encoding="utf-8",
    )

    agent.process_folder(folder)  # ne lève pas
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert data["nom_projet"] == "Métadonnées réelles"  # conservé
    assert data["coordonnees_gps"] == {"latitude": 31.5, "longitude": -7.9}
    assert data["promoteur"] == "SARL Test"


def test_serveur_down_sans_yaml_fallback_minimal(tmp_path, monkeypatch):
    """LM Studio arrêté + aucun project.yaml → fallback minimal écrit."""
    import app.ai_agent as mod

    def _refuse(*args: Any, **kwargs: Any):
        raise OSError("connexion refusée")

    monkeypatch.setattr(mod.socket, "create_connection", _refuse)
    agent = _make_agent()
    folder = tmp_path / "PROJ_2026_006_Sans_Yaml"
    _write_doc(folder, "Contenu quelconque.")

    agent.process_folder(folder)  # ne lève pas
    data = yaml.safe_load((folder / "project.yaml").read_text(encoding="utf-8"))
    assert data["id"] == "PROJ_2026_006"
    assert data["coordonnees_gps"] == {"latitude": 0.0, "longitude": 0.0}


def test_circuit_breaker_une_seule_sonde(tmp_path, monkeypatch):
    """Serveur down : la sonde réseau n'est pas répétée à chaque dossier."""
    import app.ai_agent as mod

    calls = {"n": 0}

    def _refuse(*args: Any, **kwargs: Any):
        calls["n"] += 1
        raise OSError("connexion refusée")

    monkeypatch.setattr(mod.socket, "create_connection", _refuse)
    agent = _make_agent()
    for i in range(3):
        folder = tmp_path / f"PROJ_D{i}"
        _write_doc(folder, "doc")
        agent.process_folder(folder)

    assert calls["n"] == 1  # 1re sonde puis circuit breaker (60 s)


# ------------------------------------------------- test live LM Studio (optionnel)


@pytest.mark.skipif(
    os.environ.get("RUN_LMSTUDIO_TESTS") != "1",
    reason="Test live : nécessite LM Studio lancé (RUN_LMSTUDIO_TESTS=1)",
)
def test_live_lmstudio_extraction(tmp_path):
    """Agent complet contre LM Studio réel : dossier → project.yaml exploitable."""
    cfg = load_config("config.local.yaml")
    agent = AIAgent(cfg.llm_agent, api_key_override=cfg.effective_api_key)
    assert agent.enabled

    folder = tmp_path / "PROJ_2026_004_Immobiliere_Yasmine"
    _write_doc(
        folder,
        "Dossier technique — Résidence Yasmine. Promoteur : Immobilière Yasmine SARL. "
        "Adresse : Route de Fès, km 3, Marrakech. Permis n° 2026/0789/MRK. "
        "GPS : 31.6542 N, -7.9865 W. Statut : devis.",
    )

    yaml_path = agent.process_folder(folder)
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert data["id"] == "PROJ_2026_004"
    # Le LLM a bien alimenté les métadonnées (pas le fallback vide)
    assert data["promoteur"] == "Immobilière Yasmine SARL"
    assert data["coordonnees_gps"]["latitude"] == pytest.approx(31.6542)
