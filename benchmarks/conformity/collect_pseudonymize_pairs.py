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
Phase 3 conformity input (D-054 point 3): for every text of the benchmark
corpora, under every theme, the /analyze AND /pseudonymize answers of the
live server. The extension pseudonymizes locally from /analyze; its
tests/conformity check, on this file, that with no unchecked detection its
result is byte-identical to /pseudonymize.

Corpora: main and supplementary quality corpora, secret corpora (false and
true positives), a few crafted edge cases (emojis, a placeholder already in
the text, overlaps), and the holdout corpus of D-038 when present (outside
the repository, BENCH_HOLDOUT_FILE). Synthetic texts only.

Usage: python3 benchmarks/conformity/collect_pseudonymize_pairs.py OUTPUT.jsonl
       OUTPUT goes OUTSIDE the repository (it repeats the corpus texts).
Runs one worker per test account (BENCH_ACCOUNTS_FILE): the per-user rate
limit of /api/v1/ (60/min) would otherwise make it last an hour.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "quality"), str(HERE.parent / "secret_detection")]
from build_quality_corpus import records as main_records  # noqa: E402
from build_secret_corpus import fp_records, tp_records  # noqa: E402
from build_supplementary_corpus import build as supplementary_records  # noqa: E402

from obfusk8_client import HTTP_OK, TextApi, login, read_accounts  # noqa: E402

# Edge cases of the local port of D-004 (positions, numbering, merge).
CRAFTED = [
    "😀 Camille Martin 👩\u200d💻 appelle Camille Martin au 06 12 34 56 78.",
    "Voir ⟦PERSON_1⟧ et ⟦PERSON_2⟧ : Camille Martin rencontre Jean Dupont.",
    "token=ghp_" + "A" * 36 + " pour camille.martin@exemple.invalid",
    "Patient Élodie Lefèvre, NIR 1 84 12 75 123 456 78, IBAN FR76 3000 6000 0112 3456 7890 189.",
    "𝄞 Musique : Jean Dupont à Lyon, puis Jean Dupont à Lyon 🎵.",
    "Camille Martin, CAMILLE MARTIN et camille martin vivent au 12 rue de la Paix, 75002 Paris.",
]
APP_RETRY_STATUSES = {429, 503}
# Same file and variable as run_quality_bench.py (D-038).
HOLDOUT_FILE = Path(os.environ.get("BENCH_HOLDOUT_FILE", "~/obfusk8-holdout.jsonl")).expanduser()


def corpus() -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = [(f"main/{d['id']}", d["text"]) for d in main_records()]
    docs += [(f"supplementary/{d['id']}", d["text"]) for d in supplementary_records()]
    docs += [(f"secrets-fp/{d['id']}", d["text"]) for d in fp_records()]
    docs += [(f"secrets-tp/{d['id']}", str(d["text"])) for d in tp_records()]
    docs += [(f"crafted/{i}", text) for i, text in enumerate(CRAFTED)]
    if HOLDOUT_FILE.is_file():
        for line in HOLDOUT_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                docs.append((f"holdout/{record['id']}", record["text"]))
    return docs


def call(api: Any, route: str, text: str, theme: str | None) -> dict[str, Any]:
    for _ in range(5):
        status, body, _ = api.call(route, text, theme)
        if status == HTTP_OK:
            return dict(body)
        if status not in APP_RETRY_STATUSES:
            raise RuntimeError(f"{route}: HTTP {status}")
        time.sleep(2)
    raise RuntimeError(f"{route}: still busy")


def main() -> None:
    output = Path(sys.argv[1]).resolve()
    if Path(__file__).resolve().parents[2] in output.parents:
        raise SystemExit("write the output outside the repository")
    apis = []
    for label, user, password in read_accounts():
        try:
            apis.append(TextApi(login(user, password)))
        except RuntimeError as exc:  # an unusable account is reported by label and skipped
            print(f"{label}: {exc}")
    if not apis:
        raise SystemExit("no usable test account")
    themes = [None, *(t["key"] for t in apis[0].version()["themes"])]
    jobs = [(doc_id, text, theme) for doc_id, text in corpus() for theme in themes]
    lines: list[str] = []
    lock = threading.Lock()
    errors: list[str] = []

    def worker(index: int) -> None:
        for doc_id, text, theme in jobs[index :: len(apis)]:
            try:
                analyze = call(apis[index], "analyze", text, theme)
                pseudonymize = call(apis[index], "pseudonymize", text, theme)
            except RuntimeError as exc:
                with lock:
                    errors.append(f"{doc_id} {theme}: {exc}")
                continue
            record = {"id": doc_id, "theme": theme, "text": text, "analyze": analyze, "pseudonymize": pseudonymize}
            with lock:
                lines.append(json.dumps(record, ensure_ascii=False))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(apis))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    output.write_text("".join(f"{line}\n" for line in sorted(lines)), encoding="utf-8")
    print(f"{len(lines)} pairs ({len(jobs)} expected, {len(themes)} themes), {len(errors)} error(s) -> {output}")
    for error in errors:
        print(f"  {error}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
