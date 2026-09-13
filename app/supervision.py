"""
supervision.py — interface générique d'envoi d'alertes opérationnelles
(dépendance injoignable, disque plein, certificat bientôt expiré, menace
antivirus détectée...), avec un adaptateur syslog (RFC 5424) comme
première implémentation.

Choix de syslog en premier : standard ancien et universellement supporté
côté SIEM d'entreprise, déjà disponible dans la bibliothèque standard
Python (logging.handlers.SysLogHandler) — aucune dépendance externe
nécessaire pour ce premier adaptateur, contrairement à l'antivirus (ICAP).

RÈGLE IMPORTANTE, à respecter pour toute alerte ajoutée plus tard : jamais
de donnée personnelle dans `message`/`details` — un nom de fichier hashé
ou un identifiant de job, oui ; un nom de patient ou un email utilisateur,
non. Une alerte part potentiellement vers un SIEM tiers, hors du contrôle
direct de ce projet.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger("anonymiseur.supervision")


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    severity: AlertSeverity
    source: str
    message: str
    details: dict = field(default_factory=dict)


class AlertSink(ABC):
    @abstractmethod
    def send(self, alert: Alert) -> None:
        raise NotImplementedError


class NullAlertSink(AlertSink):
    """Aucun envoi réel — utilisé quand ALERT_SINK=none (par défaut).
    Journalise localement pour ne pas perdre l'information silencieusement."""

    def send(self, alert: Alert) -> None:
        log.warning(
            "[ALERT_SINK=none, non transmise] [%s] %s: %s | %s",
            alert.severity.value,
            alert.source,
            alert.message,
            alert.details,
        )


class SyslogAlertSink(AlertSink):
    """Adaptateur RFC 5424 via logging.handlers.SysLogHandler (bibliothèque
    standard, aucune dépendance externe nécessaire pour ce protocole ancien
    et stable)."""

    _LEVEL_MAP = {
        AlertSeverity.INFO: logging.INFO,
        AlertSeverity.WARNING: logging.WARNING,
        AlertSeverity.CRITICAL: logging.CRITICAL,
    }

    def __init__(self, host: str, port: int = 514, use_tcp: bool = False):
        socktype = socket.SOCK_STREAM if use_tcp else socket.SOCK_DGRAM
        handler = logging.handlers.SysLogHandler(
            address=(host, port),
            facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
            socktype=socktype,
        )
        handler.setFormatter(logging.Formatter("anonymiseur: %(message)s"))
        self._logger = logging.getLogger(f"anonymiseur.supervision.syslog.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.handlers.clear()
        self._logger.addHandler(handler)
        self._logger.propagate = False

    def send(self, alert: Alert) -> None:
        level = self._LEVEL_MAP[alert.severity]
        payload = json.dumps(
            {"source": alert.source, "message": alert.message, "details": alert.details},
            ensure_ascii=False,
        )
        self._logger.log(level, payload)


def get_alert_sink() -> AlertSink:
    """Point de configuration unique, même principe que get_scanner() pour
    l'antivirus."""
    sink = os.environ.get("ALERT_SINK", "none").strip().lower()

    if sink == "none":
        log.warning(
            "ALERT_SINK=none : aucune alerte transmise en dehors des journaux "
            "applicatifs locaux. À configurer avant toute mise en production."
        )
        return NullAlertSink()

    if sink == "syslog":
        host = os.environ.get("SYSLOG_HOST")
        if not host:
            raise RuntimeError("ALERT_SINK=syslog requiert SYSLOG_HOST")
        return SyslogAlertSink(
            host=host,
            port=int(os.environ.get("SYSLOG_PORT", "514")),
            use_tcp=os.environ.get("SYSLOG_TCP", "false").strip().lower() == "true",
        )

    raise ValueError(f"ALERT_SINK inconnu: {sink!r} (valeurs valides: none, syslog)")
