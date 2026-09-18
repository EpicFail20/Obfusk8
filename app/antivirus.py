#Copyright (C) 2026 CARROLAGGI Xavier
#This program is free software: you can redistribute it and/or modify
#it under the terms of the GNU Affero General Public License as published
#by the Free Software Foundation, either version 3 of the License, or
#(at your option) any later version.

#This program is distributed in the hope that it will be useful,
#but WITHOUT ANY WARRANTY; without even the implied warranty of
#MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#GNU Affero General Public License for more details.

#You should have received a copy of the GNU Affero General Public License
#along with this program. If not, see <https://www.gnu.org/licenses/>.


"""
antivirus.py — interface générique de scan antivirus, avec un adaptateur
ICAP (RFC 3507) comme première implémentation.

Objectif : accueillir un antivirus d'entreprise déjà déployé côté
infrastructure cliente, en parlant un protocole standard plutôt qu'une API
propriétaire — swap de moteur possible sans réécrire l'intégration
applicative (voir get_scanner()).

Exclu délibérément : tout moteur cloud/SaaS (VirusTotal et équivalents).
Envoyer un document — potentiellement une donnée médicale réelle, pas
encore anonymisée à ce stade du pipeline — à un service tiers externe irait
directement à l'encontre de tout le travail d'isolation réseau et de
correction des fuites structurelles fait sur ce projet.
"""

from __future__ import annotations

import logging
import os
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger("anonymiseur.antivirus")


@dataclass
class ScanResult:
    is_clean: bool
    engine_name: str
    threat_name: str | None = None


class AntivirusUnavailableError(Exception):
    """
    Levée quand le moteur ne peut pas rendre de verdict fiable (timeout,
    connexion refusée, erreur de protocole). L'appelant DOIT rejeter le
    fichier dans ce cas — jamais le traiter comme propre par défaut.
    Politique d'échec fermé, cohérente avec le reste du projet (ex: un
    moteur de détection PII indisponible ne doit jamais faire passer un
    document non vérifié).
    """


class AntivirusScanner(ABC):
    """Interface commune à tout moteur antivirus utilisable par l'application."""

    @abstractmethod
    def scan(self, file_bytes: bytes, filename_hint: str = "") -> ScanResult:
        """Scanne le contenu fourni. Doit lever AntivirusUnavailableError
        plutôt que de renvoyer un verdict incertain comme "propre"."""
        raise NotImplementedError


class NullScanner(AntivirusScanner):
    """
    Aucun scan réel — utilisé quand AV_ENGINE=none (par défaut). Existe
    explicitement, plutôt que de sauter le scan silencieusement, pour que
    l'absence de protection soit un choix visible (log d'avertissement au
    démarrage) et non un oubli de configuration.
    """

    def scan(self, file_bytes: bytes, filename_hint: str = "") -> ScanResult:
        return ScanResult(is_clean=True, engine_name="none (scan désactivé)")


class IcapScanner(AntivirusScanner):
    """
    Adaptateur pour un serveur ICAP (RFC 3507) déjà déployé côté
    infrastructure cliente — antivirus d'entreprise, passerelle de scan
    dédiée (ex: FortiSandbox, un serveur c-icap/SquidClamav...).

    ATTENTION, point d'architecture important : ceci suppose un véritable
    SERVEUR ICAP directement joignable. FortiGate lui-même agit comme
    CLIENT ICAP (il transmet du trafic qui passe à travers lui vers un
    serveur externe) — il ne peut PAS être la cible directe de cet
    adaptateur. Confirmer avec l'équipe sécurité quel est le véritable
    serveur ICAP à cibler (FortiSandbox ou équivalent) avant de configurer
    ICAP_HOST/ICAP_PORT.

    Utilise python-icap (RFC 3507, zéro dépendance, licence MIT).
    Point de vigilance assumé : bibliothèque jeune au moment de l'écriture
    de ce code (statut "Alpha" sur PyPI, un seul mainteneur) — le protocole
    ICAP lui-même est ancien et stable (RFC de 2003), ce qui limite le
    risque associé à la jeunesse de cette implémentation cliente, mais à
    surveiller comme toute dépendance récente (voir politique de veille
    dépendances du projet, section 4 de l'audit).
    """

    def __init__(
        self,
        host: str,
        port: int = 1344,
        service: str = "avscan",
        timeout: float = 30.0,
        use_tls: bool = False,
        client_factory=None,
    ):
        self.host = host
        self.port = port
        self.service = service
        self.timeout = timeout
        self.use_tls = use_tls
        # Permet l'injection d'un client (réel ou simulé) pour les tests —
        # sans ça, impossible d'utiliser le plugin pytest de python-icap
        # (MockIcapClient) sans monkeypatcher l'import, plus fragile.
        self._client_factory = client_factory

    def _make_client(self):
        if self._client_factory is not None:
            return self._client_factory()
        from icap import IcapClient

        ssl_context = ssl.create_default_context() if self.use_tls else None
        return IcapClient(
            self.host, port=self.port, ssl_context=ssl_context, timeout=self.timeout
        )

    def scan(self, file_bytes: bytes, filename_hint: str = "") -> ScanResult:
        from icap.exception import (
            IcapConnectionError,
            IcapException,
            IcapProtocolError,
            IcapServerError,
            IcapTimeoutError,
        )

        try:
            with self._make_client() as client:
                response = client.scan_bytes(
                    file_bytes,
                    filename=filename_hint or "document",
                    service=self.service,
                )
        except (
            IcapConnectionError,
            IcapTimeoutError,
            IcapProtocolError,
            IcapServerError,
            IcapException,
        ) as exc:
            log.error("Scan ICAP échoué (%s:%s): %s", self.host, self.port, exc)
            raise AntivirusUnavailableError(
                f"Serveur ICAP injoignable ou en erreur: {exc}"
            ) from exc

        if response.is_no_modification:
            return ScanResult(is_clean=True, engine_name="icap")

        threat_name = response.headers.get("X-Virus-ID") or response.headers.get(
            "X-Infection-Found"
        )
        return ScanResult(is_clean=False, engine_name="icap", threat_name=threat_name)


@lru_cache(maxsize=1)
def get_scanner() -> AntivirusScanner:
    """
    Point de configuration unique : change de moteur via variable
    d'environnement, sans toucher au code appelant (voir main.py).

    Mis en cache (la config ne change jamais en cours de vie du conteneur) :
    évite de reconstruire un scanner et de rejouer le warning "scan
    désactivé" à chaque requête, et permet à main.py de valider la config
    une seule fois au démarrage (échec rapide si mal configurée) plutôt que
    de découvrir une AV_ENGINE/ICAP_* invalide sur la première requête venue.
    """
    engine = os.environ.get("AV_ENGINE", "none").strip().lower()

    if engine == "none":
        log.warning(
            "AV_ENGINE=none : aucun scan antivirus actif. À configurer avant "
            "toute mise en production avec de vraies données."
        )
        return NullScanner()

    if engine == "icap":
        host = os.environ.get("ICAP_HOST")
        if not host:
            raise RuntimeError("AV_ENGINE=icap requiert ICAP_HOST")
        return IcapScanner(
            host=host,
            port=int(os.environ.get("ICAP_PORT", "1344")),
            service=os.environ.get("ICAP_SERVICE", "avscan"),
            timeout=float(os.environ.get("ICAP_TIMEOUT_SECONDS", "30")),
            use_tls=os.environ.get("ICAP_TLS", "false").strip().lower() == "true",
        )

    raise ValueError(f"AV_ENGINE inconnu: {engine!r} (valeurs valides: none, icap)")


def is_av_enforced() -> bool:
    """
    Indique si un verdict antivirus défavorable (menace détectée OU moteur
    injoignable) doit bloquer le traitement du fichier, ou seulement être
    journalisé en laissant le fichier continuer dans le pipeline.

    Volontairement INDÉPENDANT de AV_ENGINE : permet de déployer un nouveau
    moteur en mode "observation" (voir ce qu'il aurait bloqué, sans
    perturber l'usage réel de l'application) avant de l'activer en
    blocage — même logique que le mode SCMP_ACT_LOG avant blocage réel
    pour le profil seccomp.

    Par défaut à True dès qu'un moteur réel est configuré (cohérent avec
    le principe déjà établi sur ce projet : mieux vaut bloquer à tort
    qu'laisser passer une vraie menace) — à positionner explicitement à
    "false" pour un déploiement en observation seule.
    """
    return os.environ.get("AV_ENFORCE", "true").strip().lower() == "true"
