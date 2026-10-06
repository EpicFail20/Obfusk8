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
Build-time check of the analyzer image (phase 2 ter, D-043 point 3): the
installed Python environment must be exactly the one declared in
requirements.lock, same distributions and same versions, nothing more and
nothing less; for the direct-URL entries (spaCy models), the SHA-256
recorded at install time (direct_url.json) must be the declared one.
Run after pip itself is uninstalled. Exits non-zero, failing the build, on
any difference.

Usage: python3 check_lock.py requirements.lock
"""

import importlib.metadata
import json
import re
import sys

_PINNED = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")
_DIRECT = re.compile(r"^([A-Za-z0-9_.-]+) @ (\S+)-([^-]+)-py3-none-any\.whl")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared(lock_text: str) -> tuple[dict[str, str], dict[str, str]]:
    """({name: version}, {name: sha256 of the direct-URL entries})."""
    versions: dict[str, str] = {}
    direct: dict[str, str] = {}
    entries = re.split(r"\n(?=[A-Za-z])", re.sub(r"(?m)^#.*\n", "", lock_text))
    for entry in filter(None, (raw.strip() for raw in entries)):
        digest = _HASH.search(entry)
        if digest is None:
            raise SystemExit(f"check_lock: entry without --hash: {entry.splitlines()[0]}")
        if pinned := _PINNED.match(entry):
            versions[_canonical(pinned.group(1))] = pinned.group(2)
        elif url := _DIRECT.match(entry):
            name = _canonical(url.group(1))
            versions[name] = url.group(3)
            direct[name] = digest.group(1)
        else:
            raise SystemExit(f"check_lock: unparsed entry: {entry.splitlines()[0]}")
    return versions, direct


def _installed_sha256(name: str) -> str | None:
    raw = importlib.metadata.distribution(name).read_text("direct_url.json")
    if raw is None:
        return None
    hashes: dict[str, str] = json.loads(raw).get("archive_info", {}).get("hashes", {})
    return hashes.get("sha256")


def main(lock_path: str) -> int:
    with open(lock_path, encoding="utf-8") as lock:
        versions, direct = _declared(lock.read())
    installed = {_canonical(d.metadata["Name"]): d.version for d in importlib.metadata.distributions()}
    errors = [f"not declared: {n}=={v}" for n, v in sorted(installed.items()) if n not in versions]
    errors += [f"not installed: {n}=={v}" for n, v in sorted(versions.items()) if n not in installed]
    errors += [
        f"version: {n} installed {installed[n]}, declared {v}"
        for n, v in sorted(versions.items())
        if n in installed and installed[n] != v
    ]
    errors += [
        f"sha256: {n} installed from another artifact"
        for n, digest in sorted(direct.items())
        if n in installed and _installed_sha256(n) != digest
    ]
    for error in errors:
        print(f"check_lock: {error}", file=sys.stderr)
    if not errors:
        print(f"check_lock: {len(installed)} distributions match {lock_path}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
