# Copyright (C) 2026 CARROLAGGI Xavier
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
"""
EXT-18 measurement: what the part-of-speech filter of the patched spaCy
recognizer (presidio/analyzer-build/patches/spacy_recognizer.py, PERSON /
LOCATION / ORGANIZATION dropped without a PROPN token) costs in false
negatives and saves in false positives. Reports, does not decide: it is a
false positive / false negative trade-off, a human decision.

The detection chain is the application's own (main._analyze_normalized:
common normalization, common recognizers, DEFAULT_SCORE_THRESHOLD, no
theme), pointed at ONE analyzer given by PRESIDIO_ANALYZER_URL: run it once
against an analyzer with the filter (the image in service) and once
against the measurement image without it, both as throwaway containers on
a dedicated internal network — never the stack's analyzer. Corpora: the
main corpus and the supplementary one (both built in memory).

  docker run --rm --network <measurement network> -e PRESIDIO_ANALYZER_URL=http://analyzer:3000 \\
    -e PROPN_LABEL=avec_filtre -v "$PWD/app:/app-src:ro" -v "$PWD/benchmarks:/bench" -w /bench \\
    -e PYTHONPATH=/app-src --tmpfs /data/tmp:uid=1000,gid=1000 --tmpfs /data/audit:uid=1000,gid=1000 \\
    --entrypoint python ghcr.io/epicfail20/obfusk8-app:main quality/propn_filter_bench.py

Writes benchmarks/results/propn-<label>-<timestamp>.json; compare two runs
with --compare A.json B.json (Markdown report on stdout and next to B).
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_quality_corpus import records as build_main  # noqa: E402
from build_supplementary_corpus import build as build_supplementary  # noqa: E402
from run_quality_bench import evaluate  # noqa: E402

RESULTS = HERE.parent / "results"


def _predictions(docs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    import main  # noqa: PLC0415 - imported here so --compare runs without the app

    out: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        entities = main._analyze_normalized(doc["text"])
        out[doc["id"]] = [{"entity_type": e["entity_type"], "start": e["start"], "end": e["end"]} for e in entities]
    return out


def run(label: str) -> dict[str, Any]:
    main_corpus = build_main()
    results = {}
    for name, docs in (("main", main_corpus), ("supplementary", build_supplementary())):
        results[name] = evaluate(docs, _predictions(docs))
    return {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "label": label, "results": results}


def _summary(r: dict[str, Any]) -> tuple[float, int, float, int]:
    masking, overlap = r["masking"], r["overlap"]["global"]
    return masking["global"]["recall"], masking["global"]["leaked"], overlap["precision"], overlap["fp"]


def compare(a: dict[str, Any], b: dict[str, Any]) -> str:
    lines = [
        f"# EXT-18 — filtre PROPN : {a['label']} contre {b['label']} ({b['generated_at']})",
        "",
        "Même chaîne que l'application (normalisation, reconnaisseurs communs, seuil par défaut, sans thème), "
        "seul l'analyseur change. Masquage : valeur entièrement couverte ; précision : chevauchement. "
        "**Rapport, pas de décision** : arbitrage faux positifs contre faux négatifs.",
        "",
        "| Corpus | Analyseur | Masquage | Valeurs exposées | Précision | Faux positifs "
        "| PERSON exposés | LOCATION exposés |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for corpus in ("main", "supplementary"):
        for run_ in (a, b):
            r = run_["results"][corpus]
            recall, leaked, precision, fp = _summary(r)
            per_type = r["masking"]["per_type"]
            lines.append(
                f"| {corpus} | {run_['label']} | {recall:.3f} | {leaked} | {precision:.3f} | {fp} | "
                f"{per_type.get('PERSON', {}).get('leaked', 0)} | {per_type.get('LOCATION', {}).get('leaked', 0)} |"
            )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if sys.argv[1:2] == ["--compare"] and len(sys.argv[2:]) == len(("A", "B")):
        first, second = (json.loads(Path(p).read_text(encoding="utf-8")) for p in sys.argv[2:])
        report = compare(first, second)
        Path(sys.argv[3]).with_suffix(".md").write_text(report, encoding="utf-8")
        print(report)
        sys.exit(0)
    label = os.environ.get("PROPN_LABEL", "analyseur")
    out = run(label)
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"propn-{label}-{time.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(path)
