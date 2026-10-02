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
Proves that no submitted content reached a log (CLAUDE.md §5): after the
benchmarks and end-to-end tests have run, searches the logs of EVERY
container of the stack and both audit logs for:
- every annotated value of the quality corpus (8 characters or more, to
  avoid coincidental matches of short common words),
- every secret of the secrets corpus,
- the canary values and placeholder delimiter of e2e_text_api.py.

Read-only: `docker logs` and `docker exec <app> cat` of the audit files.
Run on the Docker host of the stack, after the runs to check.

Usage: python3 benchmarks/e2e/check_no_content_in_logs.py --since 2026-10-02T07:30:00
Exit code 1 if anything is found (the report says where, never what).
"""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "obfusk8"
SERVICES = ["app", "presidio-analyzer", "presidio-anonymizer", "traefik", "oauth2-proxy", "keycloak"]
AUDIT_FILES = ["/data/audit/audit.log", "/data/audit/audit-extension.log"]
CANARIES = ["Zébulon", "Zebulon", "Canarihaut", "canarihaut", "06 12 34 56 78", "⟦"]
MIN_VALUE_CHARS = 8


def _values() -> set[str]:
    values = set(CANARIES)
    for line in (ROOT / "quality" / "corpus.jsonl").read_text(encoding="utf-8").splitlines():
        doc = json.loads(line)
        for e in doc["entities"]:
            value = doc["text"][e["start"] : e["end"]]
            if len(value) >= MIN_VALUE_CHARS:
                values.add(value)
    for line in (ROOT / "secret_detection" / "tp_corpus.jsonl").read_text(encoding="utf-8").splitlines():
        values.update(s for s in json.loads(line)["secrets"] if len(s) >= MIN_VALUE_CHARS)
    return values


def _sources(since: str) -> dict[str, str]:
    sources = {}
    for service in SERVICES:
        result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            ["docker", "logs", "--since", since, f"{PROJECT}-{service}-1"],  # noqa: S607 - docker from PATH on the host
            capture_output=True,
            text=True,
            check=True,
        )
        sources[f"docker logs {service}"] = result.stdout + result.stderr
    for path in AUDIT_FILES:
        result = subprocess.run(  # noqa: S603
            ["docker", "exec", f"{PROJECT}-app-1", "cat", path],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        sources[f"audit {path}"] = result.stdout
    return sources


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True, help="start of the runs to check (docker logs --since)")
    args = parser.parse_args()
    values = _values()
    found = 0
    for name, content in _sources(args.since).items():
        hits = sum(1 for v in values if v in content)
        found += hits
        print(f"{name}: {len(content.splitlines())} ligne(s), {hits} valeur(s) soumise(s) retrouvée(s)")
    print(f"{len(values)} valeurs recherchées, {found} trouvée(s)")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
