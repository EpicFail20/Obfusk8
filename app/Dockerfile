# Pinned by digest (EXT-01): python:3.12-slim = Python 3.12.15 on Debian 13
# (trixie), latest 3.12 release (python.org, 2026-09-30), checked on Docker Hub
# on 2026-10-05. 3.12 kept rather than 3.14 (D-040): same series as the
# presidio-analyzer image, no seccomp revalidation of a new interpreter.
FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

WORKDIR /app

# No libgl1 (removed in phase 2 bis, D-040): the PyMuPDF wheel does not link
# it, and no process of the running app mapped it (/proc/<pid>/maps); it only
# pulled mesa, LLVM (which links the system libxml2, EXT-33) and the X11
# libraries into the image.
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
#
# `apt-get upgrade` applies the Debian security updates available at build
# time (EXT-33: the image had a fixable libpcre2 not applied).
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

# pip upgraded first from a hash-pinned file (EXT-24), then removed once the
# dependencies are installed: never used at runtime, and even the latest pip
# vendors copies flagged by trivy (urllib3, msgpack, setuptools).
# requirements.lock: the full transitive set of requirements.txt, hash-pinned
# (EXT-01); `--no-deps` so that nothing outside the lock gets installed.
# Production dependencies only: pytest and the test suite are not in the
# image (EXT-09), the tests are mounted at run time (docs/DECISIONS.md D-016).
COPY requirements-build.txt requirements.lock /tmp/
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements-build.txt \
    && pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock \
    && pip check \
    && rm /tmp/requirements-build.txt /tmp/requirements.lock \
    && python -m pip uninstall -y pip

COPY main.py .
COPY antivirus.py .
COPY metrics.py .
COPY supervision.py .
COPY branding.py .
# Text API for the browser extension (phase 1), only active when
# ENABLE_EXTENSION_API=true — see docs/api-extension.en.md.
COPY text_api.py text_api_models.py ./
# Unicode normalization before detection (phase 2, D-014).
COPY text_normalization.py ./
COPY themes/ ./themes/
COPY i18n/ ./i18n/

# Dedicated non-root user (fixed UID/GID so host-side bind-mount volume
# permissions can be aligned — see deployment). Only the data directories
# belong to it: the code, themes and translations in /app stay root-owned,
# so the runtime user cannot modify them even without the read-only rootfs
# (phase 2 bis; the app never writes under /app).
RUN mkdir -p /data/tmp /data/audit && \
    groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /usr/sbin/nologin -M appuser && \
    chown -R appuser:appuser /data/tmp /data/audit

USER appuser

# Avoids .pyc bytecode writes, useless here and incompatible with
# a read-only root filesystem (see docker-compose.yml).
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
