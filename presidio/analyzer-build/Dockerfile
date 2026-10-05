# Presidio Analyzer image with French support added
# (the public image only ships the default English model)

# Pinned by digest (EXT-01, EXT-16). presidio-analyzer 2.2.364, latest stable
# release (github.com/data-privacy-stack/presidio, the former microsoft/presidio,
# same repository id); registry ghcr.io/data-privacy-stack, the only one still
# updated according to the upstream installation guide. Checked 2026-10-05.
# The base already ships spacy 3.8.13 and PyYAML 6.0.3 from its uv.lock: the
# former unpinned `pip install spacy` / `pip install pyyaml` were no-ops.
FROM ghcr.io/data-privacy-stack/presidio-analyzer:2.2.364@sha256:ae8f6f111ac2f04e3fec552f7f80edd0dcbfa2dd69ee1b9e030475be31669885

# Build steps run as root, then the image goes back to the upstream non-root
# user (UID 1001). Installed files stay root-owned: the runtime user cannot
# modify the code, the model or the configuration (read_only rootfs as well).
USER root

# Debian security updates available at build time (EXT-33 and trivy on the
# previous image: openssl, perl-base, util-linux... fixed in Debian 13).
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Hash-pinned additions, pip itself included (requirements-build.txt: pip
# only accepts --hash inside a requirements file). `uv` (Rust binary used only
# to install the upstream lock, never at runtime) is removed: it carried
# quinn-proto and rustls-webpki advisories. `pip check` fails the build on an
# inconsistent environment. pip itself is then removed: never used at runtime,
# and even the latest release vendors copies flagged by trivy (urllib3 2.7.0,
# msgpack 1.1.2, setuptools 70.3.0 in pip/_vendor/vendor.txt of pip 26.2.1).
COPY requirements-build.txt /tmp/requirements-build.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements-build.txt \
    && pip uninstall -y uv \
    && pip check \
    && rm /tmp/requirements-build.txt \
    && python -m pip uninstall -y pip

# Our config files (declare the languages + model mapping)
# Path confirmed via the NLP_CONF_FILE / ANALYZER_CONF_FILE env vars
# of the running container (/app/presidio_analyzer/conf/)
COPY conf/default.yaml /app/presidio_analyzer/conf/default.yaml
COPY conf/default_analyzer.yaml /app/presidio_analyzer/conf/default_analyzer.yaml

# Patch default_recognizers.yaml (declares fr + adds our patient
# identifier recognizer) via a real YAML parser rather than raw
# text insertion — this file's "zero-indent sequence" style proved
# too fragile for sed editing with nested blocks. PyYAML comes from the base.
COPY patch_recognizers.py /tmp/patch_recognizers.py
RUN python3 /tmp/patch_recognizers.py && rm /tmp/patch_recognizers.py

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
COPY patches/spacy_recognizer.py /app/presidio_analyzer/predefined_recognizers/nlp_engine_recognizers/spacy_recognizer.py

USER 1001
