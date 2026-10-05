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
Phase 2 bis (D-040, EXT-20): presidio-anonymizer is no longer part of the
stack. The application never called it to anonymize (redaction is done by
main.py itself); only the periodic health check watched it, so its removal
would otherwise raise a permanent "unreachable" alert.
"""

import time

import pytest

import main


class _StopLoop(Exception):
    pass


def _one_sweep(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Runs exactly one pass of the periodic loop and returns the health
    checks it made: the first sleep returns, the second ends the loop."""
    checked: list[tuple[str, str]] = []
    sleeps = iter([None])

    def fake_sleep(_seconds: float) -> None:
        if next(sleeps, _StopLoop) is _StopLoop:
            raise _StopLoop

    monkeypatch.setattr(time, "sleep", fake_sleep)
    monkeypatch.setattr(main, "_sweep_orphaned_files", lambda: None)
    monkeypatch.setattr(main, "_sweep_stale_jobs", lambda: None)
    monkeypatch.setattr(main, "_check_disk_space", lambda volume, path: None)
    monkeypatch.setattr(main, "_check_presidio_health", lambda service, url: checked.append((service, url)))
    with pytest.raises(_StopLoop):
        main._cleanup_sweep_loop()
    return checked


def test_seul_l_analyseur_est_surveille(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _one_sweep(monkeypatch) == [("analyzer", main.ANALYZER_URL)]


def test_plus_aucune_trace_de_l_anonymiseur() -> None:
    assert not hasattr(main, "ANONYMIZER_URL")
    assert not hasattr(main, "_anonymize_text")
