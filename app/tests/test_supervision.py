"""
Tests du module supervision.py — récepteur syslog UDP local réel (pas de
simulation), donc réellement exécutés et vérifiés lors de l'écriture de ce
fichier, contrairement aux tests d'antivirus.py (ICAP) et de metrics.py
(prometheus_client), qui dépendent de bibliothèques externes non
disponibles dans l'environnement où ce fichier a été écrit.
"""

import json
import os
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from supervision import Alert, AlertSeverity, NullAlertSink, SyslogAlertSink, get_alert_sink  # noqa: E402


@pytest.fixture
def local_syslog_receiver():
    """Un vrai récepteur UDP local, pas un simulacre — reçoit ce que
    SyslogAlertSink envoie réellement sur le réseau (localhost)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.settimeout(2)

    received = []

    def receive_one():
        try:
            data, _ = sock.recvfrom(65535)
            received.append(data.decode("utf-8", errors="replace"))
        except socket.timeout:
            pass

    thread = threading.Thread(target=receive_one)
    thread.start()

    yield port, received, thread

    sock.close()


def test_alerte_critique_arrive_intacte_via_syslog(local_syslog_receiver):
    port, received, thread = local_syslog_receiver

    sink = SyslogAlertSink(host="127.0.0.1", port=port, use_tcp=False)
    sink.send(
        Alert(
            severity=AlertSeverity.CRITICAL,
            source="dependency-check",
            message="Vulnérabilité CRITICAL détectée dans une image Docker",
            details={"image": "presidio-analyzer", "cve": "CVE-2026-XXXX"},
        )
    )
    thread.join(timeout=3)

    assert len(received) == 1, "le message syslog n'est jamais arrivé"
    raw = received[0]
    assert "dependency-check" in raw
    assert "CVE-2026-XXXX" in raw
    assert "anonymiseur:" in raw


def test_null_alert_sink_ne_leve_aucune_exception():
    sink = NullAlertSink()
    sink.send(Alert(severity=AlertSeverity.INFO, source="test", message="ne doit rien envoyer"))


def test_get_alert_sink_defaut_sans_variable(monkeypatch):
    monkeypatch.delenv("ALERT_SINK", raising=False)
    assert isinstance(get_alert_sink(), NullAlertSink)


def test_get_alert_sink_syslog_correctement_configure(monkeypatch):
    monkeypatch.setenv("ALERT_SINK", "syslog")
    monkeypatch.setenv("SYSLOG_HOST", "127.0.0.1")
    monkeypatch.setenv("SYSLOG_PORT", "5140")
    assert isinstance(get_alert_sink(), SyslogAlertSink)


def test_get_alert_sink_syslog_sans_host_leve_erreur_claire(monkeypatch):
    monkeypatch.setenv("ALERT_SINK", "syslog")
    monkeypatch.delenv("SYSLOG_HOST", raising=False)
    with pytest.raises(RuntimeError):
        get_alert_sink()


def test_get_alert_sink_valeur_inconnue_leve_erreur(monkeypatch):
    monkeypatch.setenv("ALERT_SINK", "un_truc_qui_nexiste_pas")
    with pytest.raises(ValueError):
        get_alert_sink()
