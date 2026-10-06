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
Dependency inventory at the source (docs/maintenance-dependances.md, step 1;
CLAUDE.md §6): for each "<group> <package> <installed version>" line read on
stdin, queries the PyPI JSON API for the highest release that is neither a
pre-release, a dev release nor yanked (and its first upload date), and the
OSV API for the advisories affecting the INSTALLED version. One tab-separated
line per package on stdout:

  group  package  installed  latest  date  OUTDATED|""  advisories|-

Runs on the host, outside every image; needs `packaging` (installed with the
development tools: PYTHONPATH=~/.cache/obfusk8-devtools). Network: pypi.org
and api.osv.dev only. Nothing is written anywhere.
"""

import json
import sys
import urllib.request
from typing import Any

from packaging.version import InvalidVersion, Version

_TIMEOUT = 30


def _get(url: str, payload: dict[str, Any] | None = None) -> Any:
    # Only the fixed https URLs of this module: any other scheme (file:,
    # custom) is refused before urllib sees it (S310 / B310 below).
    if not url.startswith("https://"):
        raise ValueError(f"refused URL scheme: {url}")
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data, {"Content-Type": "application/json"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310  # nosec B310
        return json.load(response)


def latest_release(name: str) -> tuple[Version, str]:
    releases: dict[str, list[dict[str, Any]]] = _get(f"https://pypi.org/pypi/{name}/json")["releases"]
    best: tuple[Version, str] | None = None
    for raw, files in releases.items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if version.is_prerelease or version.is_devrelease or not files or all(f.get("yanked") for f in files):
            continue
        if best is None or version > best[0]:
            best = (version, min(f["upload_time"][:10] for f in files))
    if best is None:
        raise LookupError(f"no stable release of {name} on PyPI")
    return best


def advisories(name: str, version: str) -> list[str]:
    query = {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
    return [v["id"] for v in _get("https://api.osv.dev/v1/query", query).get("vulns", [])]


def main() -> int:
    failures = 0
    for line in sys.stdin:
        if not line.strip():
            continue
        group, name, installed = line.split()
        try:
            latest, date = latest_release(name)
            ids = advisories(name, installed)
        except (OSError, LookupError, ValueError) as exc:
            print(f"{group}\t{name}\t{installed}\tERROR {exc}")
            failures += 1
            continue
        flag = "" if Version(installed) == latest else "OUTDATED"
        print(f"{group}\t{name}\t{installed}\t{latest}\t{date}\t{flag}\t{','.join(ids) or '-'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
