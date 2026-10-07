# Presidio Analyzer image, built on our own Python base (Python phase, D-052;
# closes EXT-50 and completes EXT-16): no dependency on the image published by
# the Presidio project any more. The library comes from its hash-pinned PyPI
# wheel (requirements.lock: same files as the code of the upstream image
# 2.2.364), its REST server files from server/ (unmodified upstream copies),
# French support and our configuration from conf/ and patches/.

# Same digest-pinned base as app/Dockerfile: python:3.12-slim = Python 3.12.15
# on Debian 13 (trixie), checked on Docker Hub on 2026-10-06.
FROM python:3.12-slim@sha256:ddb0207ae1f0356c2b724d740769b0c5f5f51cc54a0525178f721825f78fe74c

# Debian security updates available at build time (EXT-33). No curl: the
# health check below uses Python (D-052 point 2), one package less to patch.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Same runtime user as the upstream image (UID/GID 1001, home /home/presidio):
# docker-compose.yml mounts a tmpfs on /home/presidio/.gunicorn for gunicorn's
# control socket. Everything else in the image stays root-owned: the runtime
# user cannot modify the code, the models or the configuration (the upstream
# image gave it /app; read_only rootfs as well).
RUN groupadd -g 1001 presidio \
    && useradd -u 1001 -g presidio -m -s /usr/sbin/nologin presidio

# Full Python environment from requirements.lock (D-043 point 3): every
# distribution of the image, presidio-analyzer included, declared with its
# SHA-256 and installed with --no-deps, every entry hash-checked (the base
# brings no package of its own but pip). pip comes first from
# requirements-build.txt (pip only accepts --hash inside a requirements file).
# `pip check` fails the build on an inconsistent environment. pip itself is
# then removed: never used at runtime, and even the latest release vendors
# copies flagged by trivy (urllib3, msgpack, setuptools in pip/_vendor).
# check_lock.py runs last and fails the build unless the installed
# distributions are exactly the declared ones (versions, and SHA-256 of the
# spaCy models), and unless the requirements of presidio-analyzer[server]
# hold, which `pip check` ignores (EXT-60: gunicorn<26.0.0).
COPY requirements-build.txt requirements.lock check_lock.py /tmp/
RUN pip install --no-cache-dir --root-user-action=ignore --require-hashes --no-deps -r /tmp/requirements-build.txt \
    && pip install --no-cache-dir --root-user-action=ignore --require-hashes --no-deps -r /tmp/requirements.lock \
    && pip check \
    && python -m pip uninstall -y pip \
    && python3 /tmp/check_lock.py /tmp/requirements.lock 'presidio-analyzer[server]' \
    && rm /tmp/requirements-build.txt /tmp/requirements.lock /tmp/check_lock.py

WORKDIR /app

# REST server of the upstream image (app.py, logging.ini next to it,
# entrypoint.sh running gunicorn), unmodified copies of tag 2.2.364 with their
# MIT license: see server/README.md.
COPY server/app.py server/logging.ini server/entrypoint.sh /app/
COPY server/LICENSE /app/LICENSE.presidio

# Effective configuration in /app/conf (paths in the environment below):
# our NLP engine and analyzer files (declare French and the model mapping),
# and the package's default_recognizers.yaml, copied then patched by
# patch_recognizers.py through a real YAML parser (declares fr + adds our
# patient identifier recognizer) — this file's "zero-indent sequence" style
# proved too fragile for sed editing with nested blocks. The SHA-256 checks
# fail the build if a Presidio update changed the upstream file our patch is
# built on (it must then be reviewed: docs/maintenance-dependances.md).
COPY conf/default.yaml conf/default_analyzer.yaml /app/conf/
COPY patch_recognizers.py /tmp/patch_recognizers.py
RUN pkg=$(python3 -c 'import importlib.util; print(importlib.util.find_spec("presidio_analyzer").submodule_search_locations[0])') \
    && echo "764417c2f7d39ae236d07f7c2eb404597732c454ecc52e1c117e5377a9af4c78  $pkg/conf/default_recognizers.yaml" | sha256sum -c - \
    && cp "$pkg/conf/default_recognizers.yaml" /app/conf/default_recognizers.yaml \
    && python3 /tmp/patch_recognizers.py \
    && rm /tmp/patch_recognizers.py

# Patch spacy_recognizer.py: adds a part-of-speech (POS) tag filter
# for PERSON/ORGANIZATION/LOCATION before accepting a detection.
# Context: Presidio assigns a FIXED score (0.85) to all detections
# from this recognizer, regardless of accuracy — an isolated administrative
# word with no sentence context ("Skills", "Operate"...) gets
# exactly the same score as a real proper noun, impossible to tell apart
# by threshold. The patch requires that at least one token of the span be a
# proper noun (PROPN) according to the spaCy tagger already active in the loaded
# pipeline — zero extra inference cost. See the patched file's docstring
# for the full details. Measured in phase 2 (EXT-18, D-027): kept for now,
# final decision in docs/BACKLOG-detection.md.
# The patched file replaces the upstream one as a whole (EXT-16): the build
# fails unless the file it replaces is the one of presidio-analyzer 2.2.364,
# then its bytecode is recompiled (the wheel's .pyc is of the upstream file).
COPY patches/spacy_recognizer.py /tmp/spacy_recognizer.py
RUN pkg=$(python3 -c 'import importlib.util; print(importlib.util.find_spec("presidio_analyzer").submodule_search_locations[0])') \
    && target="$pkg/predefined_recognizers/nlp_engine_recognizers/spacy_recognizer.py" \
    && echo "f5fd4400927278350af7e8d3fcb3e4fa9924590ec00f4eb71ebd19fde83a0564  $target" | sha256sum -c - \
    && cp /tmp/spacy_recognizer.py "$target" \
    && python3 -m compileall -q "$target" \
    && rm /tmp/spacy_recognizer.py

# versions.json declares the presidio-analyzer, spaCy, French model and Python
# versions; app/analyzer_versions.json is its copy, part of the
# detection_config fingerprint of /api/v1/version (phase 2 bis step G; Python
# since D-052 point 4: the interpreter's Unicode database shapes detection).
# The build fails if what is installed differs: a base image or model change
# cannot slip in without updating the declaration (and so the fingerprint).
COPY versions.json /tmp/versions.json
RUN python3 -c 'import json, platform, importlib.metadata as m; \
d = json.load(open("/tmp/versions.json")); \
found = {"presidio_analyzer": m.version("presidio_analyzer"), "spacy": m.version("spacy"), \
"fr_core_news_md": m.version("fr_core_news_md"), "python": platform.python_version()}; \
assert found == d, f"installed {found} != versions.json {d}"' \
    && rm /tmp/versions.json

# Same settings as the upstream image (port, a single gunicorn worker), with
# the configuration files above.
ENV PORT=3000 \
    WORKERS=1 \
    ANALYZER_CONF_FILE=/app/conf/default_analyzer.yaml \
    NLP_CONF_FILE=/app/conf/default.yaml \
    RECOGNIZER_REGISTRY_CONF_FILE=/app/conf/default_recognizers.yaml

USER 1001
EXPOSE 3000

# Same probe, intervals and retries as the upstream image, in Python rather
# than curl (D-052 point 2): any error or non-2xx status exits non-zero.
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python3", "-c", "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.environ['PORT'], timeout=3)"]

CMD ["./entrypoint.sh"]
