FROM python:3.12-slim

WORKDIR /app

# libgl1 requis par certaines dépendances PyMuPDF selon la distro de base
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
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
