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
Detection quality benchmark of the text API (phase 1, step E), through the
full stack, for every theme and without theme.

Three ways of matching a predicted span to an annotated one:
- strict: same [start, end) and an accepted type (ACCEPTED below);
- overlap: the spans overlap and the type is accepted;
- masking (type-agnostic): the annotated value is ENTIRELY covered by the
  union of the predicted spans, whatever their type — what actually matters
  for a leak: a partially covered value leaks its uncovered part.
Precision is computed on predicted types that belong to the annotated
vocabulary; the other predicted types (ORGANIZATION, URL, NRP...) are
reported separately rather than counted as false positives, since the
corpus does not annotate them.

No success threshold (prompt of phase 1): the first run is the baseline.
Writes a timestamped JSON and Markdown report in benchmarks/results/.

Usage: python3 benchmarks/quality/run_quality_bench.py   (environment: see benchmarks/obfusk8_client.py;
       BENCH_STACK_INFO=<file> adds the versions collected by benchmarks/collect_stack_info.sh;
       BENCH_CORPUS=supplementary measures the phase 2 supplementary corpus instead of corpus.jsonl)
"""

import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from obfusk8_client import HTTP_OK, TextApi, login  # noqa: E402

sys.path.insert(0, str(HERE))
from build_supplementary_corpus import build as build_supplementary  # noqa: E402

RESULTS = HERE.parent / "results"
# BENCH_CORPUS: "main" (default, corpus.jsonl, the phase 1 baseline) or
# "supplementary" (build_supplementary_corpus.py, built in memory: families
# EXT-29 to EXT-31 with >= 30 examples each, phase 2).
CORPUS = os.environ.get("BENCH_CORPUS", "main")

# Annotated type -> predicted types that count as a correct detection.
ACCEPTED: dict[str, set[str]] = {
    "PERSON": {"PERSON"},
    "LOCATION": {"LOCATION"},
    "ADDRESS": {"STREET_ADDRESS", "LOCATION"},
    "PHONE": {"PHONE_NUMBER"},
    "EMAIL": {"EMAIL_ADDRESS"},
    "DATE": {"DATE_TIME"},
    "IBAN": {"IBAN_CODE"},
    "NIR": {"FR_NIR"},
    "CARD": {"CREDIT_CARD"},
    "PATIENT_ID": {"PATIENT_ID"},
    "SECRET": {"SECRET", "API_KEY", "AWS_ACCESS_KEY", "PRIVATE_KEY"},
}
IN_VOCABULARY = set().union(*ACCEPTED.values())


def _prf(tp: int, fp: int, fn: int, correct_predictions: int) -> dict[str, Any]:
    """tp/fn count annotated values (recall); correct_predictions/fp count
    predictions (precision) — not the same unit once spans overlap."""
    precision = correct_predictions / (correct_predictions + fp) if correct_predictions + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall > 0
        else (0.0 if precision is not None and recall is not None else None)
    )
    return {"tp": tp, "fp": fp, "fn": fn, "precision": _r(precision), "recall": _r(recall), "f1": _r(f1)}


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def _covered(start: int, end: int, predicted: list[dict[str, Any]]) -> bool:
    covered = [False] * (end - start)
    for p in predicted:
        for i in range(max(start, p["start"]), min(end, p["end"])):
            covered[i - start] = True
    return all(covered)


def _match(p: dict[str, Any], g: dict[str, Any], mode: str) -> bool:
    if p["entity_type"] not in ACCEPTED[g["type"]]:
        return False
    if mode == "strict":
        return bool((p["start"], p["end"]) == (g["start"], g["end"]))
    return bool(p["start"] < g["end"] and g["start"] < p["end"])


def evaluate(docs: list[dict[str, Any]], predictions: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Recall per ANNOTATED type (is each value found?), precision over the
    predictions whose type is in the annotated vocabulary (is each prediction
    an annotated value?). For a given annotated type, its precision counts
    the predictions of its accepted types: a LOCATION prediction is correct
    if it matches any annotated value that accepts LOCATION (LOCATION or
    ADDRESS)."""
    gold_hits: dict[str, dict[str, Counter[str]]] = {m: defaultdict(Counter) for m in ("strict", "overlap")}
    pred_hits: dict[str, list[tuple[str, bool]]] = {"strict": [], "overlap": []}
    masking: dict[str, Counter[str]] = defaultdict(Counter)
    by_variant: dict[str, Counter[str]] = defaultdict(Counter)
    out_of_vocabulary: Counter[str] = Counter()
    leaks: list[dict[str, Any]] = []
    for doc in docs:
        gold, pred = doc["entities"], predictions[doc["id"]]
        out_of_vocabulary.update(p["entity_type"] for p in pred if p["entity_type"] not in IN_VOCABULARY)
        pred_in = [p for p in pred if p["entity_type"] in IN_VOCABULARY]
        for mode in ("strict", "overlap"):
            for g in gold:
                found = any(_match(p, g, mode) for p in pred_in)
                gold_hits[mode][g["type"]]["tp" if found else "fn"] += 1
            for p in pred_in:
                pred_hits[mode].append((p["entity_type"], any(_match(p, g, mode) for g in gold)))
        for g in gold:
            hidden = _covered(g["start"], g["end"], pred)
            masking[g["type"]]["covered" if hidden else "leaked"] += 1
            by_variant[doc["variant"]]["covered" if hidden else "leaked"] += 1
            if not hidden:
                leaks.append(
                    {
                        "id": doc["id"],
                        "variant": doc["variant"],
                        "lang": doc["lang"],
                        "category": doc["category"],
                        "type": g["type"],
                        "partial": any(p["start"] < g["end"] and g["start"] < p["end"] for p in pred),
                    }
                )

    def summarize(mode: str) -> dict[str, Any]:
        per_type = {}
        for t in sorted(gold_hits[mode]):
            own = [ok for ptype, ok in pred_hits[mode] if ptype in ACCEPTED[t]]
            per_type[t] = _prf(
                gold_hits[mode][t]["tp"], len(own) - sum(own), gold_hits[mode][t]["fn"], correct_predictions=sum(own)
            )
        all_ok = [ok for _, ok in pred_hits[mode]]
        tp = sum(c["tp"] for c in gold_hits[mode].values())
        fn = sum(c["fn"] for c in gold_hits[mode].values())
        return {
            "global": _prf(tp, len(all_ok) - sum(all_ok), fn, correct_predictions=sum(all_ok)),
            "per_type": per_type,
        }

    def rate(c: Counter[str]) -> dict[str, Any]:
        total = c["covered"] + c["leaked"]
        return {"covered": c["covered"], "leaked": c["leaked"], "recall": _r(c["covered"] / total if total else None)}

    return {
        "strict": summarize("strict"),
        "overlap": summarize("overlap"),
        "masking": {
            "global": rate(sum(masking.values(), Counter())),
            "per_type": {t: rate(c) for t, c in sorted(masking.items())},
            "per_variant": {v: rate(c) for v, c in sorted(by_variant.items())},
        },
        "out_of_vocabulary_predictions": dict(out_of_vocabulary.most_common()),
        "leaks": leaks,
    }


def _corpus() -> list[dict[str, Any]]:
    if CORPUS == "supplementary":
        return build_supplementary()
    if CORPUS != "main":
        raise SystemExit(f"unknown BENCH_CORPUS: {CORPUS}")
    return [json.loads(line) for line in (HERE / "corpus.jsonl").read_text(encoding="utf-8").splitlines() if line]


def run() -> dict[str, Any]:
    api = TextApi(login())
    version = api.version()
    docs = _corpus()
    results = {}
    for theme in [None, *(t["key"] for t in version["themes"])]:
        predictions = {}
        for doc in docs:
            status, body, _ = api.call("analyze", doc["text"], theme)
            if status != HTTP_OK:
                raise RuntimeError(f"{doc['id']}: HTTP {status}")
            predictions[doc["id"]] = body["entities"]
        results[theme or "aucun"] = evaluate(docs, predictions)
    stack: dict[str, Any] = {}
    if os.environ.get("BENCH_STACK_INFO"):
        stack = json.loads(Path(os.environ["BENCH_STACK_INFO"]).read_text(encoding="utf-8"))
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "api": {
            k: version[k] for k in ("api_version", "detection_config", "analyzer_language", "analyzer_recognizers")
        },
        "themes": [t["key"] for t in version["themes"]],
        "stack": stack,
        "corpus": {
            "name": CORPUS,
            "prompts": len(docs),
            "entities": sum(len(d["entities"]) for d in docs),
            "variants": dict(Counter(d["variant"] for d in docs)),
        },
        "results": results,
    }


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:.3f}"


def markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Banc d'essai — qualité de détection de l'API texte ({report['generated_at']})",
        "",
        f"Configuration `{report['api']['detection_config']}`, reconnaisseurs de l'analyseur "
        f"`{report['api']['analyzer_recognizers']}`, langue `{report['api']['analyzer_language']}`, "
        f"thèmes {', '.join(report['themes'])}. Corpus {report['corpus'].get('name', 'main')} : "
        f"{report['corpus']['prompts']} prompts, "
        f"{report['corpus']['entities']} entités annotées. Versions de la pile : voir le JSON (`stack`).",
        "",
        "Pas de seuil de réussite : cette exécution fixe la référence.",
        "",
        "## Vue d'ensemble",
        "",
        "| Thème | Strict P / R / F1 | Chevauchement P / R / F1 | Masquage (rappel, sans type) | Valeurs exposées |",
        "|---|---|---|---|---|",
    ]
    for theme, r in report["results"].items():
        s, o, m = r["strict"]["global"], r["overlap"]["global"], r["masking"]["global"]
        lines.append(
            f"| {theme} | {_fmt(s['precision'])} / {_fmt(s['recall'])} / {_fmt(s['f1'])} | "
            f"{_fmt(o['precision'])} / {_fmt(o['recall'])} / {_fmt(o['f1'])} | {_fmt(m['recall'])} | {m['leaked']} |"
        )
    for theme, r in report["results"].items():
        lines += [
            "",
            f"## Thème : {theme}",
            "",
            "| Type | Strict P / R / F1 | Chevauchement P / R / F1 | Masquage (couvertes / exposées) |",
            "|---|---|---|---|",
        ]
        for t, s in r["strict"]["per_type"].items():
            o, m = r["overlap"]["per_type"][t], r["masking"]["per_type"].get(t, {"covered": 0, "leaked": 0})
            lines.append(
                f"| {t} | {_fmt(s['precision'])} / {_fmt(s['recall'])} / {_fmt(s['f1'])} | "
                f"{_fmt(o['precision'])} / {_fmt(o['recall'])} / {_fmt(o['f1'])} | {m['covered']} / {m['leaked']} |"
            )
        lines += ["", "| Variante | Masquage (rappel) | Exposées |", "|---|---|---|"]
        for v, m in r["masking"]["per_variant"].items():
            lines.append(f"| {v} | {_fmt(m['recall'])} | {m['leaked']} |")
        oov = ", ".join(f"{k} {v}" for k, v in r["out_of_vocabulary_predictions"].items()) or "—"
        lines += ["", f"Types prédits hors référentiel (non comptés comme faux positifs) : {oov}."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    report = run()
    RESULTS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    if CORPUS != "main":
        stamp = f"{CORPUS}-{stamp}"
    (RESULTS / f"quality-{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (RESULTS / f"quality-{stamp}.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
