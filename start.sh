#!/usr/bin/env bash
# SamaConcept · GeoProjects — démarrage (local ou Docker)
# Usage : ./start.sh [--docker]
set -euo pipefail
cd "$(dirname "$0")"

MODE="local"
if [[ "${1:-}" == "--docker" ]]; then
  MODE="docker"
fi

if [[ "$MODE" == "docker" ]]; then
  echo "→ Démarrage Docker Compose (http://localhost:8000)"
  docker compose up --build -d
  docker compose logs -f
  exit 0
fi

# ---- Mode local ----
VENV=".venv"
if [[ ! -d "$VENV" ]]; then
  echo "→ Création du venv + installation des dépendances (dev)"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r requirements-dev.txt
fi

CONFIG_FILE="${CONFIG_PATH:-config.local.yaml}"
export CONFIG_PATH="$CONFIG_FILE"

echo "→ Application disponible sur : http://localhost:8000  (config : $CONFIG_FILE)"
exec "$VENV/bin/uvicorn" main:app --host 0.0.0.0 --port 8000 --reload
