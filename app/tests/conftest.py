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
"""Shared test settings.

The document routes accept a state-changing request only from the
application's own origin (D-048 point 3). The suite runs without
APP_DOMAIN, so every test sees the same fictitious origin, which the
document-flow helpers send (tests/doc_headers.py).
"""

from collections.abc import Iterator

import pytest

import main
from tests.doc_headers import TEST_ORIGIN


@pytest.fixture(autouse=True)
def _application_origin(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(main, "APP_ORIGIN", TEST_ORIGIN, raising=False)
    yield
