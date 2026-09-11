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

# ---- OCR système -----------------------------------------------------------
install_ocr_engine() {
  if command -v tesseract >/dev/null 2>&1; then
    return 0
  fi

  echo "→ Tesseract absent : installation automatique de l'OCR…"
  case "$(uname -s)" in
    Darwin)
      if ! command -v brew >/dev/null 2>&1; then
        echo "✖ Homebrew est requis sur macOS : https://brew.sh" >&2
        return 1
      fi
      brew install tesseract tesseract-lang
      ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        if [[ "$(id -u)" -eq 0 ]]; then
          apt-get update && apt-get install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng
        else
          sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng
        fi
      elif command -v dnf >/dev/null 2>&1; then
        if [[ "$(id -u)" -eq 0 ]]; then
          dnf install -y tesseract tesseract-langpack-fra
        else
          sudo dnf install -y tesseract tesseract-langpack-fra
        fi
      elif command -v pacman >/dev/null 2>&1; then
        if [[ "$(id -u)" -eq 0 ]]; then
          pacman -Sy --noconfirm tesseract tesseract-data-fra
        else
          sudo pacman -Sy --noconfirm tesseract tesseract-data-fra
        fi
      else
        echo "✖ Gestionnaire de paquets Linux non reconnu (apt, dnf ou pacman)." >&2
        return 1
      fi
      ;;
    MINGW*|MSYS*|CYGWIN*)
      if command -v winget.exe >/dev/null 2>&1; then
        winget.exe install --id UB-Mannheim.TesseractOCR --exact --accept-source-agreements --accept-package-agreements
      elif command -v choco.exe >/dev/null 2>&1; then
        choco.exe install tesseract -y
      else
        echo "✖ Windows nécessite winget ou Chocolatey pour installer Tesseract." >&2
        return 1
      fi
      ;;
    *)
      echo "✖ Système non reconnu : installez Tesseract manuellement." >&2
      return 1
      ;;
  esac

  if ! command -v tesseract >/dev/null 2>&1; then
    echo "⚠ Tesseract a été installé mais n'est pas encore dans le PATH." >&2
  fi
}

install_ocr_engine || echo "⚠ L'application démarre sans OCR des PDF scannés." >&2

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
