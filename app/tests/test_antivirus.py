"""
Tests de l'adaptateur IcapScanner, via le client ICAP simulé fourni par
python-icap (icap.pytest_plugin) — aucun serveur ICAP réel nécessaire pour
ces tests. Écrits d'après la documentation officielle de la bibliothèque ;
non exécutés localement faute d'accès réseau pour l'installer — à valider
réellement avant de faire confiance à ce fichier.
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
    """Fichier de test standard de détection antivirus (pas un vrai virus) —
    voir https://www.eicar.org/download-anti-malware-testfile/"""
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
    """Le point le plus important à vérifier : en cas d'indisponibilité du
    serveur, on ne doit JAMAIS renvoyer un verdict 'propre' par défaut —
    politique d'échec fermé."""
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
    """Vérifie qu'un moteur mal configuré ne 'fige' pas sur le premier
    verdict rendu — chaque fichier doit être évalué indépendamment."""
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
    """is_av_enforced() ne dépend d'aucun client ICAP -- juste de la
    variable d'environnement AV_ENFORCE."""

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
