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
Python phase (D-043 point 5, D-052 point 5): app and the analyzer run the same
Python, defined in ONE place, the `x-python-base` anchor of
docker-compose.build.yml, passed to both Dockerfiles as the PYTHON_BASE build
argument (no default: a build without it fails).

The analyzer build fails unless its interpreter is the "python" version of
presidio/analyzer-build/versions.json, which app/analyzer_versions.json
copies (app/run-tests.sh checks the two copies are identical): this suite,
run inside the app image, checks app's interpreter against the same value.
The repository is mounted read-only at OBFUSK8_REPO by app/run-tests.sh.
"""

import json
import os
import platform
import re
from pathlib import Path

import pytest

REPO = Path(os.environ.get("OBFUSK8_REPO", ""))
BUILD_FILE = REPO / "docker-compose.build.yml"
DOCKERFILES = (REPO / "app" / "Dockerfile", REPO / "presidio" / "analyzer-build" / "Dockerfile")
# A digest-pinned official Python image (EXT-01): python:X.Y.Z-slim@sha256:<64 hex>.
PINNED = re.compile(r"python:(\d+\.\d+\.\d+)-slim@sha256:[0-9a-f]{64}")


def _anchor() -> str:
    text = BUILD_FILE.read_text(encoding="utf-8")
    values: list[str] = re.findall(r'^x-python-base: &python-base "([^"]+)"$', text, re.MULTILINE)
    assert len(values) == 1, "exactly one x-python-base anchor in docker-compose.build.yml"
    return values[0]


@pytest.fixture(autouse=True)
def _repository() -> None:
    assert BUILD_FILE.is_file(), "OBFUSK8_REPO must point to the repository (see app/run-tests.sh)"


def test_meme_interpreteur_que_l_analyseur() -> None:
    declared = json.loads((Path(__file__).resolve().parent.parent / "analyzer_versions.json").read_text("utf-8"))
    assert platform.python_version() == declared["python"]


def test_base_python_definie_une_seule_fois_et_epinglee() -> None:
    match = PINNED.fullmatch(_anchor())
    assert match is not None, "python:X.Y.Z-slim@sha256:<digest>"
    assert match.group(1) == platform.python_version()


def test_les_deux_services_recoivent_l_ancre() -> None:
    text = BUILD_FILE.read_text(encoding="utf-8")
    assert text.count("PYTHON_BASE: *python-base") == 2


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=["app", "analyseur"])
def test_dockerfile_sans_base_codee_en_dur(dockerfile: Path) -> None:
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    froms = [line for line in lines if line.startswith("FROM ")]
    assert froms == ["FROM ${PYTHON_BASE}"]
    # Declared without a default: a build that does not pass it fails.
    assert "ARG PYTHON_BASE" in lines
    assert not PINNED.search("\n".join(lines))
