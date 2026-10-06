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
Turns the report of a pip dry run into the body of a hash-locked
requirements file (docs/maintenance-dependances.md, step 3): one
"name==version" entry per resolved distribution, with the SHA-256 of the
wheel pip chose, sorted by name. The report comes from

  pip install --dry-run --ignore-installed --only-binary=:all: --report report.json \
      -r <requirements> -c constraints.txt

run inside the image base (so that the wheels are the ones the image
installs). Fails on anything that is not a wheel with a known SHA-256.

Usage: python3 lock_from_report.py report.json > body.txt
"""

import json
import sys
from typing import Any


def lock_lines(report: dict[str, Any]) -> list[str]:
    entries: list[tuple[str, str]] = []
    for item in report["install"]:
        name, version = item["metadata"]["name"], item["metadata"]["version"]
        info = item["download_info"]
        digest = info.get("archive_info", {}).get("hashes", {}).get("sha256")
        if not info["url"].endswith(".whl") or not digest:
            raise SystemExit(f"lock_from_report: {name} is not a wheel with a known SHA-256")
        entries.append((name.lower(), f"{name}=={version} \\\n    --hash=sha256:{digest}"))
    return [line for _, line in sorted(entries)]


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as report_file:
        print("\n".join(lock_lines(json.load(report_file))))
