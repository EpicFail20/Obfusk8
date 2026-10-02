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
Secrets detector benchmark (phase 1, step D), through the full stack:
- false positives: SECRET spans found in ordinary text and code with no
  secret (fp_corpus.jsonl), per text and per 1,000 characters;
- recall: share of the fictitious secrets of tp_corpus.jsonl entirely
  covered by a SECRET span.
Run for every theme and without theme (secrets are active whatever the theme).
No success threshold: the first run sets the baseline. Writes a timestamped
JSON and Markdown report in benchmarks/results/.

Usage: python3 benchmarks/secret_detection/run_secrets_bench.py   (see obfusk8_client.py for the environment)
"""

import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from obfusk8_client import HTTP_OK, TextApi, login  # noqa: E402

RESULTS = HERE.parent / "results"


def _load(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (HERE / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def _covered(entities: list[dict[str, Any]], text: str, value: str) -> bool:
    start = text.index(value)
    end = start + len(value)
    return any(e["entity_type"] == "SECRET" and e["start"] <= start and e["end"] >= end for e in entities)


def run() -> dict[str, Any]:
    api = TextApi(login())
    version = api.version()
    fp_corpus, tp_corpus = _load("fp_corpus.jsonl"), _load("tp_corpus.jsonl")
    themes = [None] + [t["key"] for t in version["themes"]]
    per_theme = {}
    for theme in themes:
        fp_spans: list[dict[str, Any]] = []
        fp_texts = 0
        other_types: Counter[str] = Counter()
        for doc in fp_corpus:
            status, body, _ = api.call("analyze", doc["text"], theme)
            if status != HTTP_OK:
                raise RuntimeError(f"unexpected HTTP {status}: {body}")
            secrets = [e for e in body["entities"] if e["entity_type"] == "SECRET"]
            other_types.update(e["entity_type"] for e in body["entities"] if e["entity_type"] != "SECRET")
            fp_texts += bool(secrets)
            fp_spans += [{"id": doc["id"], "kind": doc["kind"], "chars": e["end"] - e["start"]} for e in secrets]
        chars = sum(len(d["text"]) for d in fp_corpus)
        by_family: dict[str, list[bool]] = {}
        for doc in tp_corpus:
            status, body, _ = api.call("analyze", doc["text"], theme)
            if status != HTTP_OK:
                raise RuntimeError(f"unexpected HTTP {status}: {body}")
            for value in doc["secrets"]:
                by_family.setdefault(doc["family"], []).append(_covered(body["entities"], doc["text"], value))
        found = sum(sum(v) for v in by_family.values())
        total = sum(len(v) for v in by_family.values())
        per_theme[theme or "aucun"] = {
            "false_positives": {
                "texts_without_secret": len(fp_corpus),
                "texts_with_a_false_secret": fp_texts,
                "secret_spans": len(fp_spans),
                "spans_per_1000_chars": round(len(fp_spans) * 1000 / chars, 3),
                "spans": fp_spans,
                "other_entity_types_on_these_texts": dict(sorted(other_types.items())),
            },
            "recall": {
                "secrets": total,
                "detected": found,
                "recall": round(found / total, 3),
                "missed_families": sorted({f for f, v in by_family.items() if not all(v)}),
            },
        }
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "api": {
            k: version[k] for k in ("api_version", "detection_config", "analyzer_language", "analyzer_recognizers")
        },
        "corpus": {
            "fp_texts": len(fp_corpus),
            "fp_chars": sum(len(d["text"]) for d in fp_corpus),
            "tp_texts": len(tp_corpus),
        },
        "results": per_theme,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Banc d'essai — détection des secrets ({report['generated_at']})",
        "",
        f"Configuration de détection `{report['api']['detection_config']}`, reconnaisseurs de l'analyseur "
        f"`{report['api']['analyzer_recognizers']}`, langue `{report['api']['analyzer_language']}`. "
        f"Corpus : {report['corpus']['fp_texts']} textes sans secret ({report['corpus']['fp_chars']} caractères), "
        f"{report['corpus']['tp_texts']} textes avec secret. Pas de seuil de réussite : première mesure = référence.",
        "",
        "| Thème | Textes sans secret avec un faux SECRET | Faux SECRET | Pour 1 000 caractères "
        "| Secrets détectés | Rappel | Familles manquées |",
        "|---|---|---|---|---|---|---|",
    ]
    for theme, r in report["results"].items():
        fp, rc = r["false_positives"], r["recall"]
        lines.append(
            f"| {theme} | {fp['texts_with_a_false_secret']}/{fp['texts_without_secret']} | {fp['secret_spans']} | "
            f"{fp['spans_per_1000_chars']} | {rc['detected']}/{rc['secrets']} | {rc['recall']} | "
            f"{', '.join(rc['missed_families']) or '—'} |"
        )
    lines += ["", "Faux positifs (sans thème) : identifiant et nature du texte, longueur du passage marqué.", ""]
    for span in report["results"]["aucun"]["false_positives"]["spans"]:
        lines.append(f"- `{span['id']}` ({span['kind']}), {span['chars']} caractères")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    result = run()
    RESULTS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    (RESULTS / f"secrets-{stamp}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (RESULTS / f"secrets-{stamp}.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))
