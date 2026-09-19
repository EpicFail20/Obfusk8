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
Tests for the IcapScanner adapter, via the mock ICAP client provided by
python-icap (icap.pytest_plugin) — no real ICAP server needed for these
tests. Written from the library's official documentation; not run
locally due to lack of network access to install it — to be actually
validated before trusting this file.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from antivirus import AntivirusUnavailableError, IcapScanner  # noqa: E402
from icap.exception import IcapTimeoutError  # noqa: E402
from icap.pytest_plugin import IcapResponseBuilder, MockIcapClient  # noqa: E402


def _scanner_with_mock(mock_client: MockIcapClient) -> IcapScanner:
    return IcapScanner(host="icap-test", client_factory=lambda: mock_client)


def test_fichier_propre_est_reconnu_comme_propre():
    mock_client = MockIcapClient()
    mock_client.on_respmod(IcapResponseBuilder().clean().build())

    scanner = _scanner_with_mock(mock_client)
    result = scanner.scan(b"contenu inoffensif", filename_hint="rapport.pdf")

    assert result.is_clean is True
    assert result.engine_name == "icap"
    assert result.threat_name is None


def test_menace_detectee_est_signalee():
    mock_client = MockIcapClient()
    mock_client.on_respmod(IcapResponseBuilder().virus("Trojan.Generic").build())

    scanner = _scanner_with_mock(mock_client)
    result = scanner.scan(b"contenu suspect", filename_hint="document.docx")

    assert result.is_clean is False
    assert result.threat_name == "Trojan.Generic"


def test_eicar_est_detecte():
    """Standard antivirus detection test file (not a real virus) —
    see https://www.eicar.org/download-anti-malware-testfile/"""
    eicar = b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'

    mock_client = MockIcapClient()
    mock_client.on_respmod(
        callback=lambda data, **kw: (
            IcapResponseBuilder().virus("EICAR-Test").build()
            if b"EICAR" in data
            else IcapResponseBuilder().clean().build()
        )
    )

    scanner = _scanner_with_mock(mock_client)
    result = scanner.scan(eicar, filename_hint="eicar.com")

    assert result.is_clean is False
    assert result.threat_name == "EICAR-Test"


def test_timeout_serveur_leve_antivirus_unavailable_pas_un_verdict_propre():
    """The most important point to verify: if the server is unavailable,
    we must NEVER return a 'clean' verdict by default —
    fail-closed policy."""
    mock_client = MockIcapClient()
    mock_client.on_any(raises=IcapTimeoutError("délai dépassé"))

    scanner = _scanner_with_mock(mock_client)

    with pytest.raises(AntivirusUnavailableError):
        scanner.scan(b"peu importe le contenu", filename_hint="fichier.pdf")


def test_connexion_refusee_leve_antivirus_unavailable():
    from icap.exception import IcapConnectionError

    mock_client = MockIcapClient()
    mock_client.on_any(raises=IcapConnectionError("connexion refusée"))

    scanner = _scanner_with_mock(mock_client)

    with pytest.raises(AntivirusUnavailableError):
        scanner.scan(b"peu importe", filename_hint="fichier.docx")


def test_plusieurs_fichiers_sequentiels_verdicts_distincts():
    """Checks that a misconfigured engine does not 'freeze' on the first
    verdict returned — each file must be evaluated independently."""
    mock_client = MockIcapClient()
    mock_client.on_respmod(
        IcapResponseBuilder().clean().build(),
        IcapResponseBuilder().virus("Trojan.Gen").build(),
        IcapResponseBuilder().clean().build(),
    )
    scanner = _scanner_with_mock(mock_client)

    r1 = scanner.scan(b"fichier 1")
    r2 = scanner.scan(b"fichier 2")
    r3 = scanner.scan(b"fichier 3")

    assert r1.is_clean is True
    assert r2.is_clean is False
    assert r3.is_clean is True


class TestIsAvEnforced:
    """is_av_enforced() does not depend on any ICAP client -- only on the
    AV_ENFORCE environment variable."""

    def test_par_defaut_active(self, monkeypatch):
        monkeypatch.delenv("AV_ENFORCE", raising=False)
        from antivirus import is_av_enforced
        assert is_av_enforced() is True

    def test_explicitement_desactive(self, monkeypatch):
        monkeypatch.setenv("AV_ENFORCE", "false")
        from antivirus import is_av_enforced
        assert is_av_enforced() is False

    def test_insensible_a_la_casse(self, monkeypatch):
        monkeypatch.setenv("AV_ENFORCE", "FALSE")
        from antivirus import is_av_enforced
        assert is_av_enforced() is False

    def test_explicitement_active(self, monkeypatch):
        monkeypatch.setenv("AV_ENFORCE", "true")
        from antivirus import is_av_enforced
        assert is_av_enforced() is True
