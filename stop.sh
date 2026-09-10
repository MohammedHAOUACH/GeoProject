#!/usr/bin/env bash
# SamaConcept · GeoProjects — arrêt (local ou Docker)
# Usage : ./stop.sh [--docker]
set -euo pipefail
cd "$(dirname "$0")"

MODE="local"
if [[ "${1:-}" == "--docker" ]]; then
  MODE="docker"
fi

if [[ "$MODE" == "docker" ]]; then
  echo "→ Arrêt Docker Compose"
  docker compose down
  exit 0
fi

# ---- Mode local ----
# Cible les processus uvicorn de CE projet (main:app) lancés par start.sh.
PIDS=$(pgrep -f "uvicorn main:app" || true)
if [[ -z "$PIDS" ]]; then
  echo "Aucun serveur local en cours (uvicorn main:app)."
  exit 0
fi

echo "→ Arrêt de : $PIDS"
# SIGTERM d'abord (arrêt propre, lifespan → fermeture SQLite),
# SIGKILL seulement si encore vivant après 5 s.
kill $PIDS 2>/dev/null || true
for _ in $(seq 1 10); do
  PIDS=$(pgrep -f "uvicorn main:app" || true)
  [[ -z "$PIDS" ]] && echo "Serveur arrêté." && exit 0
  sleep 0.5
done
echo "Arrêt forcé (SIGKILL)."
kill -9 $PIDS 2>/dev/null || true
