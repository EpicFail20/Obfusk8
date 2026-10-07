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
Multi-user benchmark (phase 2, step G.4), THROUGH THE FULL STACK, all
accounts from the same IP (the VM), as users behind one NAT would be:

1. per-user rate limiting (D-008): one user exhausts the text router's burst
   (60/min, burst 20) and gets Traefik's 429; another user, at the same
   moment and from the same IP, must still get 200;
2. no starvation: three users send prompts continuously while a fourth has
   documents detected (rounds of DOCUMENTS documents, separated by the job
   expiry: MAX_PENDING_JOBS = 20). Reported per user: latency outside and
   during document processing, and every response that is not a 200 —
   the application's own 429 (text analysis queue full, MAX_TEXT_QUEUE)
   is told apart from Traefik's (rate limit).

Accounts are read at run time from BENCH_ACCOUNTS_FILE ("user, password" per
line, outside the repository, default ~/.obfusk8-test-accounts) and only
ever reported by label (compte-N): no name, password or identifier is
printed or written.

Usage: python3 benchmarks/latency/multi_user_bench.py (environment: see
benchmarks/obfusk8_client.py; ENABLE_EXTENSION_API=true on the stack).
"""

import json
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from run_latency_bench import UPLOAD_PACING_SECONDS, _detect, _release, _stats, _text  # noqa: E402

from obfusk8_client import BASE_URL, HTTP_OK, TEXT_API_HEADERS, login, read_accounts  # noqa: E402

RESULTS = HERE.parent / "results"
ROUNDS = int(os.environ.get("BENCH_MULTI_ROUNDS", "2"))
DOCUMENTS = int(os.environ.get("BENCH_MULTI_DOCUMENTS", "9"))
JOB_TTL_SECONDS = int(os.environ.get("BENCH_JOB_TTL_SECONDS", "600"))
PROMPT_INTERVAL_SECONDS = 1.0  # per user: just under the 60/min average rate
BURST = 20  # text-ratelimit burst (docker-compose.yml)
ANALYZE = f"{BASE_URL}/api/v1/text/analyze"

Sample = tuple[float, float, float, int, str]  # start, end, duration, status, origin of the status


def _post(session: Any, text: str) -> tuple[int, str, float]:
    """One analyze request: status, origin of the status ("app" when the
    body carries the application's request_id, "edge" otherwise, as for
    Traefik's 429), duration."""
    started = time.monotonic()
    response = session.post(ANALYZE, json={"text": text}, headers=TEXT_API_HEADERS, timeout=60)
    elapsed = time.monotonic() - started
    origin = "app" if "request_id" in response.text else "edge"
    return response.status_code, origin, elapsed


def rate_limit_per_user(first: Any, second: Any) -> dict[str, Any]:
    """`first` sends BURST + 10 requests back to back, then `second` sends 5
    at once: separate buckets mean the second user is not throttled by the
    first one's burst."""
    time.sleep(70)  # let every bucket refill after the previous runs
    burst = [_post(first, _text(200))[:2] for _ in range(BURST + 10)]
    other = [_post(second, _text(200))[:2] for _ in range(5)]
    return {
        "first_user": dict(Counter(f"{status} {origin}" for status, origin in burst)),
        "second_user_right_after": dict(Counter(f"{status} {origin}" for status, origin in other)),
    }


def _prompt_flow(stop: threading.Event, session: Any, samples: list[Sample]) -> None:
    while not stop.is_set():
        started = time.monotonic()
        status, origin, elapsed = _post(session, _text(2000))
        ended = time.monotonic()
        samples.append((ended - elapsed, ended, elapsed, status, origin))
        time.sleep(max(0.0, PROMPT_INTERVAL_SECONDS - (ended - started)))


def _round(users: list[tuple[str, Any]], documents_session: Any) -> dict[str, Any]:
    alone = []
    for _ in range(DOCUMENTS):
        time.sleep(UPLOAD_PACING_SECONDS)
        elapsed, page = _detect(documents_session)
        alone.append(elapsed)
        _release(documents_session, page)
    samples: dict[str, list[Sample]] = {label: [] for label, _ in users}
    stop = threading.Event()
    workers = [
        threading.Thread(target=_prompt_flow, args=(stop, session, samples[label]), daemon=True)
        for label, session in users
    ]
    for worker in workers:
        worker.start()
    time.sleep(15)  # prompts only, before the first document
    windows, loaded = [], []
    for _ in range(DOCUMENTS):
        time.sleep(UPLOAD_PACING_SECONDS)
        start = time.monotonic()
        elapsed, page = _detect(documents_session)
        windows.append((start, time.monotonic()))
        loaded.append(elapsed)
        _release(documents_session, page)
    time.sleep(10)
    stop.set()
    for worker in workers:
        worker.join(timeout=60)
    return {"alone": alone, "loaded": loaded, "windows": windows, "samples": samples}


def starvation(users: list[tuple[str, Any]], documents_session: Any) -> dict[str, Any]:
    alone: list[float] = []
    loaded: list[float] = []
    windows: list[tuple[float, float]] = []
    samples: dict[str, list[Sample]] = {label: [] for label, _ in users}
    for index in range(ROUNDS):
        if index:
            time.sleep(JOB_TTL_SECONDS + 90)
        result = _round(users, documents_session)
        alone += result["alone"]
        loaded += result["loaded"]
        windows += result["windows"]
        for label in samples:
            samples[label] += result["samples"][label]

    def during(s: Sample) -> bool:
        return any(s[0] < end and start < s[1] for start, end in windows)

    per_user = {}
    for label, values in samples.items():
        ok = [s for s in values if s[3] == HTTP_OK]
        per_user[label] = {
            "outside_document_processing": _stats([s[2] for s in ok if not during(s)]),
            "overlapping_document_processing": _stats([s[2] for s in ok if during(s)]),
            "statuses": dict(Counter(f"{s[3]} {s[4]}" for s in values)),
        }
    every = [s for values in samples.values() for s in values if s[3] == HTTP_OK]
    return {
        "rounds": ROUNDS,
        "documents_per_round": DOCUMENTS,
        "document_detect": {"alone": _stats(alone), "with_prompt_flows": _stats(loaded)},
        "prompts_all_users": {
            "outside_document_processing": _stats([s[2] for s in every if not during(s)]),
            "overlapping_document_processing": _stats([s[2] for s in every if during(s)]),
        },
        "per_user": per_user,
    }


def markdown(report: dict[str, Any]) -> str:
    rl, st = report["rate_limit_per_user"], report["starvation"]
    lines = [
        f"# Banc d'essai — plusieurs utilisateurs ({report['generated_at']})",
        "",
        f"{report['users']} comptes de test, tous depuis la même IP (la VM). Comptes désignés par libellé seulement. "
        "`app` = réponse de l'application (porte un `request_id`) ; `edge` = réponse de Traefik.",
        "",
        "## Limitation de débit par utilisateur",
        "",
        f"- {rl['first']} envoie {BURST + 10} requêtes d'affilée : {rl['first_user']}",
        f"- {rl['second']} envoie 5 requêtes juste après, même IP : {rl['second_user_right_after']}",
        "",
        "## Absence de famine",
        "",
        f"{', '.join(st['prompt_users'])} envoient chacun un prompt de 2 000 caractères par seconde pendant que "
        f"{st['documents_user']} fait détecter des documents (CSV synthétique) : {st['rounds']} tour(s) de "
        f"{st['documents_per_round']} document(s).",
        "",
        "| Mesure | n | p50 (ms) | p95 (ms) | max (ms) |",
        "|---|---|---|---|---|",
    ]
    rows = [
        ("Détection du document, seul", st["document_detect"]["alone"]),
        ("Détection du document, pendant les flux de prompts", st["document_detect"]["with_prompt_flows"]),
        ("Prompts (tous utilisateurs), hors traitement", st["prompts_all_users"]["outside_document_processing"]),
        (
            "Prompts (tous utilisateurs), pendant un traitement",
            st["prompts_all_users"]["overlapping_document_processing"],
        ),
    ]
    for label, u in st["per_user"].items():
        rows.append((f"{label}, pendant un traitement", u["overlapping_document_processing"]))
    for name, s in rows:
        lines.append(f"| {name} | {s['n']} | {s['p50_ms']} | {s['p95_ms']} | {s['max_ms']} |")
    lines += ["", "Réponses par utilisateur (code et origine) :", ""]
    lines += [f"- {label} : {u['statuses']}" for label, u in st["per_user"].items()]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    accounts_ = read_accounts()
    if len(accounts_) < 4:  # noqa: PLR2004 - three prompt users and one document user
        raise SystemExit("four test accounts are needed")
    sessions = [(label, login(user, password)) for label, user, password in accounts_[:4]]
    report: dict[str, Any] = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "users": len(sessions)}
    rl = rate_limit_per_user(sessions[0][1], sessions[1][1])
    report["rate_limit_per_user"] = {"first": sessions[0][0], "second": sessions[1][0], **rl}
    print(json.dumps(report["rate_limit_per_user"], ensure_ascii=False), flush=True)
    st = starvation(sessions[:3], sessions[3][1])
    report["starvation"] = {
        "prompt_users": [label for label, _ in sessions[:3]],
        "documents_user": sessions[3][0],
        **st,
    }
    RESULTS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    (RESULTS / f"multi-user-{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (RESULTS / f"multi-user-{stamp}.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
