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
Latency benchmark of the text API (phase 1, step E), measured from the
client THROUGH THE FULL STACK (Traefik, oauth2-proxy, app under the
enforcing seccomp profile, presidio-analyzer):

1. p50/p95 per prompt size (analyze; pseudonymize on two sizes);
2. a document (synthetic CSV) detected alone, then while a sustained flow of
   prompts runs — does the text API slow the document flow down?
3. the same prompt flow observed while documents are processed — the
   document flow blocks the single event loop (EXT-07).

Limits of this run: one test account, so the prompt flow is bounded by the
per-user Traefik rate limit (60/min, burst 20) — it measures one heavy user,
not many. No success threshold: the first run is the baseline.

Usage: python3 benchmarks/latency/run_latency_bench.py   (environment: see benchmarks/obfusk8_client.py)
"""

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from obfusk8_client import BASE_URL, HTTP_OK, TextApi, login  # noqa: E402

RESULTS = HERE.parent / "results"
SIZES = [200, 1000, 5000, 10000, 20000]
SAMPLES = int(os.environ.get("BENCH_LATENCY_SAMPLES", "20"))
# Existing upload rate limit (docker-compose.yml, upload-ratelimit): 5/min.
UPLOAD_PACING_SECONDS = 13
PROMPT = (
    "Bonjour, je suis Camille Martin, née le 12/04/1985 à Rennes. Mon collègue Paul Durand "
    "(paul.durand@example.com, 06 39 98 12 34) habite 12 rue des Lilas, 35000 Rennes. "
    "Peux-tu reformuler ce message pour le service client ? "
)


def _text(size: int) -> str:
    return (PROMPT * (size // len(PROMPT) + 1))[:size]


def _pct(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile, in milliseconds."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, round(q / 100 * len(ordered) + 0.5))
    return round(ordered[min(rank, len(ordered)) - 1] * 1000, 1)


def _stats(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50_ms": _pct(values, 50),
        "p95_ms": _pct(values, 95),
        "max_ms": round(max(values) * 1000, 1) if values else None,
    }


def per_size(api: TextApi) -> dict[str, Any]:
    out: dict[str, Any] = {"analyze": {}, "pseudonymize": {}}
    for size in SIZES:
        times = []
        for _ in range(SAMPLES):
            status, body, elapsed = api.call("analyze", _text(size))
            if status != HTTP_OK:
                raise RuntimeError(f"analyze {size}: HTTP {status} {body}")
            times.append(elapsed)
        out["analyze"][str(size)] = _stats(times)
    for size in (1000, 10000):
        times = []
        for _ in range(max(5, SAMPLES // 2)):
            status, body, elapsed = api.call("pseudonymize", _text(size))
            if status != HTTP_OK:
                raise RuntimeError(f"pseudonymize {size}: HTTP {status} {body}")
            times.append(elapsed)
        out["pseudonymize"][str(size)] = _stats(times)
    return out


def _csv_document() -> bytes:
    """Synthetic CSV, ~1,200 cells: several analyzer batches, so a slowdown
    is measurable (names from fictitious pools, phone numbers in the ARCEP
    fiction range)."""
    first = ["Camille", "Lucas", "Léa", "Hugo", "Chloé", "Louis", "Manon", "Gabriel"]
    last = ["Martin", "Durand", "Leroy", "Moreau", "Girard", "Mercier", "Roux", "Blanc"]
    rows = ["Nom;Ville;Commentaire"]
    for i in range(400):
        rows.append(
            f"{first[i % 8]} {last[(i * 3) % 8]};Rennes;Rappeler au 06 39 98 {i % 100:02d} {(i * 7) % 100:02d} "
            f"au sujet du dossier {1000 + i}, client depuis {2000 + i % 25}."
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def _detect(session: Any) -> float:
    started = time.monotonic()
    response = session.post(
        f"{BASE_URL}/api/detect", files={"file": ("bench.csv", _csv_document())}, data={"theme": ""}, timeout=180
    )
    elapsed = time.monotonic() - started
    if response.status_code != HTTP_OK or not re.search(r'name="job_id"', response.text):
        raise RuntimeError(f"detect: HTTP {response.status_code}")
    return elapsed


def contention(documents: int = 3) -> dict[str, Any]:
    doc_session, prompt_api = login(), TextApi(login(), min_interval=1.0)
    alone = []
    for _ in range(documents):
        time.sleep(UPLOAD_PACING_SECONDS)
        alone.append(_detect(doc_session))

    samples: list[tuple[float, float, float]] = []  # (start, end, duration) of each prompt request
    stop = threading.Event()

    def prompt_flow() -> None:
        while not stop.is_set():
            started = time.monotonic()
            status, _, elapsed = prompt_api.call("analyze", _text(2000))
            if status == HTTP_OK:
                samples.append((started, started + elapsed, elapsed))

    worker = threading.Thread(target=prompt_flow, daemon=True)
    worker.start()
    time.sleep(15)  # prompt-only baseline before the first document
    windows, loaded = [], []
    for _ in range(documents):
        time.sleep(UPLOAD_PACING_SECONDS)
        start = time.monotonic()
        loaded.append(_detect(doc_session))
        windows.append((start, time.monotonic()))
    time.sleep(10)
    stop.set()
    worker.join(timeout=60)

    def during(s: tuple[float, float, float]) -> bool:
        return any(s[0] < end and start < s[1] for start, end in windows)

    return {
        "document_detect": {
            "alone": _stats(alone),
            "with_prompt_flow": _stats(loaded),
            "prompt_flow_rate_per_s": round(len(samples) / max(1.0, samples[-1][1] - samples[0][0]), 2)
            if samples
            else None,
        },
        "prompts_2000_chars": {
            "outside_document_processing": _stats([s[2] for s in samples if not during(s)]),
            "overlapping_document_processing": _stats([s[2] for s in samples if during(s)]),
        },
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Banc d'essai — latence de l'API texte ({report['generated_at']})",
        "",
        "Mesures côté client, à travers toute la pile (Traefik compris). Un seul compte de test : "
        "le flux de prompts est borné par la limitation de débit par utilisateur (60/min). "
        "Pas de seuil de réussite : première mesure = référence.",
        "",
        "## Latence par taille de prompt",
        "",
        "| Route | Caractères | n | p50 (ms) | p95 (ms) | max (ms) |",
        "|---|---|---|---|---|---|",
    ]
    for route, sizes in report["per_size"].items():
        for size, s in sizes.items():
            lines.append(f"| {route} | {size} | {s['n']} | {s['p50_ms']} | {s['p95_ms']} | {s['max_ms']} |")
    c = report["contention"]
    lines += [
        "",
        "## Document (CSV synthétique, ~1 200 cellules) et flux de prompts",
        "",
        "| Mesure | n | p50 (ms) | p95 (ms) | max (ms) |",
        "|---|---|---|---|---|",
    ]
    for label, s in (
        ("Détection du document, seul", c["document_detect"]["alone"]),
        ("Détection du document, pendant le flux de prompts", c["document_detect"]["with_prompt_flow"]),
        ("Prompt 2 000 car., hors traitement de document", c["prompts_2000_chars"]["outside_document_processing"]),
        (
            "Prompt 2 000 car., pendant un traitement de document",
            c["prompts_2000_chars"]["overlapping_document_processing"],
        ),
    ):
        lines.append(f"| {label} | {s['n']} | {s['p50_ms']} | {s['p95_ms']} | {s['max_ms']} |")
    lines += [
        "",
        f"Débit effectif du flux de prompts : {c['document_detect']['prompt_flow_rate_per_s']} requête(s) par seconde.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    api = TextApi(login())
    version = api.version()
    stack: dict[str, Any] = {}
    if os.environ.get("BENCH_STACK_INFO"):
        stack = json.loads(Path(os.environ["BENCH_STACK_INFO"]).read_text(encoding="utf-8"))
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "api": {k: version[k] for k in ("api_version", "detection_config", "analyzer_recognizers")},
        "stack": stack,
        "per_size": per_size(api),
        "contention": contention(),
    }
    RESULTS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    (RESULTS / f"latency-{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (RESULTS / f"latency-{stamp}.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
