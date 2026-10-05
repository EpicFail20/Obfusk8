#!/bin/sh
# Runs the test suite against the code OF THE PRODUCTION IMAGE, in a throwaway
# container, under the enforcing seccomp profile (docs/DECISIONS.md D-016,
# updated in phase 2 bis). The image holds no tests and no test tool (EXT-09):
# app/tests is mounted read-only at /app/tests, and the development tools of
# requirements-dev.txt (installed OUTSIDE the image, see its header) are
# mounted read-only and put on PYTHONPATH. No network, read-only root
# filesystem, tmpfs instead of the real volumes: the real audit log and
# working files are never touched (CLAUDE.md §1, never run pytest inside the
# `app` service container).
#
# Usage (from the repository root):
#   app/run-tests.sh [pytest arguments]
# Environment:
#   OBFUSK8_TEST_IMAGE  image under test (default: the image of docker-compose.yml)
#   OBFUSK8_DEVTOOLS    directory of the installed development tools
#                       (default: ~/.cache/obfusk8-devtools)
set -eu

repo=$(cd "$(dirname "$0")/.." && pwd)
image=${OBFUSK8_TEST_IMAGE:-ghcr.io/epicfail20/obfusk8-app:0.2.0-dev}
devtools=${OBFUSK8_DEVTOOLS:-$HOME/.cache/obfusk8-devtools}

# The image must stay minimal: fail before running anything if pytest or the
# test suite came back into it.
docker run --rm --network none --entrypoint sh "$image" -c \
    'test ! -e /app/tests && ! python -c "import pytest" 2>/dev/null' \
    || { echo "run-tests.sh: $image contains tests or pytest (EXT-09)" >&2; exit 1; }

exec docker run --rm --network none --read-only --memory 1g \
    --security-opt seccomp="$repo/seccomp/app-enforce.json" \
    --security-opt no-new-privileges --cap-drop ALL \
    --tmpfs /tmp --tmpfs /data/tmp:uid=1000,gid=1000 --tmpfs /data/audit:uid=1000,gid=1000 \
    -v "$repo/app/tests:/app/tests:ro" -v "$devtools:/devtools:ro" -w /app \
    -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/devtools:/app \
    --entrypoint python "$image" -m pytest -q -p no:cacheprovider "$@" tests
