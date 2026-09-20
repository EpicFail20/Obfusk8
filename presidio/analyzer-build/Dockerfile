# Presidio Analyzer image with French support added
# (the public image only ships the default English model)

FROM ghcr.io/data-privacy-stack/presidio-analyzer:2.2.364

# Downloads the French spaCy model in addition to the already-present English one
RUN pip install --no-cache-dir spacy && \
    python -m spacy download fr_core_news_md

# Our config files (declare the languages + model mapping)
# Path confirmed via the NLP_CONF_FILE / ANALYZER_CONF_FILE env vars
# of the running container (/app/presidio_analyzer/conf/)
COPY conf/default.yaml /app/presidio_analyzer/conf/default.yaml
COPY conf/default_analyzer.yaml /app/presidio_analyzer/conf/default_analyzer.yaml

# Patch default_recognizers.yaml (declares fr + adds our patient
# identifier recognizer) via a real YAML parser rather than raw
# text insertion — this file's "zero-indent sequence" style proved
# too fragile for sed editing with nested blocks.
COPY patch_recognizers.py /tmp/patch_recognizers.py
RUN pip install --no-cache-dir pyyaml && \
    python3 /tmp/patch_recognizers.py

# Patch spacy_recognizer.py: adds a part-of-speech (POS) tag filter
# for PERSON/ORGANIZATION/LOCATION before accepting a detection.
# Context: Presidio assigns a FIXED score (0.85) to all detections
# from this recognizer, regardless of accuracy — an isolated administrative
# word with no sentence context ("Skills", "Operate"...) gets
# exactly the same score as a real proper noun, impossible to tell apart
# by threshold. The patch requires that at least one token of the span be a
# proper noun (PROPN) according to the spaCy tagger already active in the loaded
# pipeline — zero extra inference cost. See the patched file's docstring
# for the full details. File functionally verified (see the repo's
# unit tests) but NOT yet validated against the real fr_core_news_md model
# under real conditions — to confirm after deployment.
COPY patches/spacy_recognizer.py /app/presidio_analyzer/predefined_recognizers/nlp_engine_recognizers/spacy_recognizer.py
