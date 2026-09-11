FROM python:3.11-slim

WORKDIR /app

# Moteur OCR pour les PDF scannés (texte français et anglais).
RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		tesseract-ocr \
		tesseract-ocr-fra \
		tesseract-ocr-eng \
	&& rm -rf /var/lib/apt/lists/*

# Dépendances Python (cache de construction séparé)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code de l'application
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]