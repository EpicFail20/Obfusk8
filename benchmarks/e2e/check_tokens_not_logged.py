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
Proves that no token reached a log (D-057, phase "option 2"): searches the
logs of EVERY container of the stack and both audit logs for the token
segments written by `e2e_extension_oidc.py --fragments`, plus the JWT
prefix `eyJ` in the audit logs.

Read-only: `docker logs` and `docker exec <app> cat` of the audit files.
Run on the Docker host, right after the end-to-end run; delete the
fragments file afterwards.

Usage: python3 benchmarks/e2e/check_tokens_not_logged.py FRAGMENTS --since 2026-10-12T13:00:00
Exit code 1 if anything is found (the report says where, never what).
"""

import argparse
import subprocess
import sys
from pathlib import Path

AUDIT_FILES = ("/data/audit/audit.log", "/data/audit/audit-extension.log")


def containers() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],  # noqa: S607 - docker from PATH on the host
        check=True,
        capture_output=True,
        text=True,
    )
    return [name for name in out.stdout.split() if name.startswith("obfusk8-")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fragments", type=Path)
    parser.add_argument("--since", required=True)
    args = parser.parse_args()
    fragments = [line for line in args.fragments.read_text(encoding="ascii").splitlines() if len(line) >= 16]
    sources: dict[str, str] = {}
    for name in containers():
        logs = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            ["docker", "logs", "--since", args.since, name],  # noqa: S607 - docker from PATH on the host
            check=False,  # a container that stopped meanwhile: its logs are still searched
            capture_output=True,
            text=True,
            errors="replace",
        )
        sources[f"journal de {name}"] = logs.stdout + logs.stderr
    app = next(name for name in sources if "app-1" in name).removeprefix("journal de ")
    for path in AUDIT_FILES:
        audit = subprocess.run(  # noqa: S603
            ["docker", "exec", app, "cat", path],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        sources[f"audit {path}"] = audit.stdout
    found = 0
    for where, text in sources.items():
        hits = sum(1 for fragment in fragments if fragment in text)
        jwt_prefix = text.count("eyJ") if where.startswith("audit") else 0
        print(
            f"{where} : {hits} fragment(s) de jeton" + (f", {jwt_prefix} « eyJ »" if where.startswith("audit") else "")
        )
        found += hits + jwt_prefix
    print(f"{len(fragments)} fragment(s) cherché(s) dans {len(sources)} source(s) : {found} trouvé(s)")
    return 1 if found or not fragments else 0


if __name__ == "__main__":
    sys.exit(main())
