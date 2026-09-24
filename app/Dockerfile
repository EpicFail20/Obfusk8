FROM python:3.12-slim

WORKDIR /app

# libgl1 required by some PyMuPDF dependencies depending on the base distro
#
# tesseract-ocr + tesseract-ocr-fra: OCR engine for image support
# (PNG/JPEG) and its French language data — essential, this
# project handles French; without this data package, OCR would default
# to English and produce degraded text on accented characters,
# hence degraded PII detection.
#
# WARNING - documented residual risk (same spirit as the note on
# python-icap "Alpha" in antivirus.py): the version packaged by Debian
# trixie at time of writing (5.5.0-1+b1) has several known CVEs
# still open upstream at time of writing (CVE-2026-73066,
# CVE-2026-73067 - fixed in 5.5.3 but not yet republished by Debian -
# and CVE-2026-88047 to CVE-2026-88054, with no upstream fix available at this
# time). All require a forged .traineddata (language model)
# file to be exploited; the application ONLY loads the
# language data packages installed here via apt (fra/eng), never a file
# supplied by the user — the uploaded PNG/JPEG image is never
# interpreted as a .traineddata. The exploitation vector documented by
# these CVEs therefore does not apply to this project's upload flow. To be reassessed
# if Debian publishes a fix, or if a user/external source of .traineddata
# were ever introduced.
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
COPY branding.py .
COPY themes/ ./themes/
COPY i18n/ ./i18n/
COPY tests/ ./tests/

# The pytest cache must write somewhere other than the read-only /app root
# (see read_only: true in docker-compose.yml) — /tmp is mounted as tmpfs
# so it's writable.
RUN printf '[pytest]\ncache_dir = /tmp/.pytest_cache\n' > pytest.ini

# Dedicated non-root user (fixed UID/GID so host-side bind-mount volume
# permissions can be aligned — see deployment).
RUN mkdir -p /data/tmp /data/audit && \
    groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /usr/sbin/nologin -M appuser && \
    chown -R appuser:appuser /app /data/tmp /data/audit

USER appuser

# Avoids .pyc bytecode writes, useless here and incompatible with
# a read-only root filesystem (see docker-compose.yml).
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
