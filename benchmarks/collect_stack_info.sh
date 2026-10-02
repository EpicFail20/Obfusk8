#!/bin/sh
# Collects the versions the benchmark reports must carry (engine, models,
# themes, images), as JSON on stdout. Run on the Docker host of the stack;
# read-only (docker inspect / exec of version queries only).
# Usage: sh benchmarks/collect_stack_info.sh > /tmp/stack.json
#        BENCH_STACK_INFO=/tmp/stack.json python3 benchmarks/quality/run_quality_bench.py
set -eu
PROJECT=${COMPOSE_PROJECT_NAME:-obfusk8}
img() { docker inspect "$PROJECT-$1-1" --format '{{.Config.Image}}@{{.Image}}'; }
analyzer=$(docker exec "$PROJECT-presidio-analyzer-1" python -c '
import importlib.metadata as m, re, json
toml = open("/app/pyproject.toml", encoding="utf-8").read()
v = re.search(r"^version = \"([^\"]+)\"", toml, re.M).group(1)
print(json.dumps({"presidio_analyzer": v, "spacy": m.version("spacy"),
                  "fr_core_news_md": m.version("fr_core_news_md"), "en_core_web_lg": m.version("en_core_web_lg"),
                  "regex": m.version("regex")}))')
app=$(docker exec "$PROJECT-app-1" python -c '
import importlib.metadata as m, json, platform
print(json.dumps({"python": platform.python_version(), **{p: m.version(p) for p in ("fastapi", "starlette", "pydantic", "anyio", "uvicorn", "requests")}}))')
themes=$(cd "$(dirname "$0")/.." && sha256sum app/themes/*.json app/themes/extension/*.json | awk '{printf "%s\"%s\": \"%s\"", (NR>1?", ":""), $2, substr($1,1,16)}')
commit=$(cd "$(dirname "$0")/.." && git rev-parse --short HEAD)
dirty=$(cd "$(dirname "$0")/.." && git status --porcelain -- app | wc -l)
printf '{"git_commit": "%s", "app_files_modified_since_commit": %s, "images": {"app": "%s", "presidio_analyzer": "%s", "traefik": "%s"}, "analyzer": %s, "app": %s, "theme_files_sha256_16": {%s}, "seccomp": "%s"}\n' \
  "$commit" "$dirty" "$(img app)" "$(img presidio-analyzer)" "$(img traefik)" "$analyzer" "$app" "$themes" \
  "$(docker exec "$PROJECT-app-1" grep Seccomp: /proc/1/status | awk '{print ($2==2?"filter (app-enforce.json)":$2)}')"
