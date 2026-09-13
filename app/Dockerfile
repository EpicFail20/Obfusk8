FROM python:3.12-slim

WORKDIR /app

# libgl1 requis par certaines dépendances PyMuPDF selon la distro de base
#
# tesseract-ocr + tesseract-ocr-fra : moteur OCR pour le support image
# (PNG/JPEG) et ses données linguistiques françaises — indispensable, ce
# projet traite du français ; sans ce paquet de données, l'OCR utiliserait
# l'anglais par défaut et produirait un texte dégradé sur les accents,
# donc une détection PII dégradée.
#
# ATTENTION - risque résiduel documenté (même esprit que la remarque sur
# python-icap "Alpha" dans antivirus.py) : la version packagée par Debian
# trixie au moment de l'écriture (5.5.0-1+b1) a plusieurs CVE connues et
# encore ouvertes en amont au moment de l'écriture (CVE-2026-73066,
# CVE-2026-73067 - corrigées en 5.5.3 mais pas encore republiées par Debian -
# et CVE-2026-88047 à CVE-2026-88054, sans correctif amont disponible à ce
# jour). Toutes nécessitent un fichier .traineddata (modèle de langue)
# forgé pour être exploitées ; l'application ne charge QUE les paquets de
# données linguistiques installés ici par apt (fra/eng), jamais un fichier
# fourni par l'utilisateur — l'image PNG/JPEG uploadée n'est jamais
# interprétée comme un .traineddata. Le vecteur d'exploitation documenté par
# ces CVE ne s'applique donc pas au flux d'upload de ce projet. À réévaluer
# si Debian publie un correctif, ou si un jour une source de .traineddata
# externe/utilisateur était introduite.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    tesseract-ocr \
    tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY antivirus.py .
COPY metrics.py .
COPY supervision.py .
COPY themes/ ./themes/
COPY tests/ ./tests/

# Le cache pytest doit écrire ailleurs que la racine /app en lecture seule
# (voir read_only: true dans docker-compose.yml) — /tmp est monté en tmpfs
# donc writable.
RUN printf '[pytest]\ncache_dir = /tmp/.pytest_cache\n' > pytest.ini

# Utilisateur non-root dédié (UID/GID fixes pour pouvoir aligner les
# permissions des volumes bind-mount côté hôte — voir déploiement).
RUN mkdir -p /data/tmp /data/audit && \
    groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /usr/sbin/nologin -M appuser && \
    chown -R appuser:appuser /app /data/tmp /data/audit

USER appuser

# Évite les écritures de bytecode .pyc, inutiles ici et incompatibles avec
# un système de fichiers racine en lecture seule (voir docker-compose.yml).
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
