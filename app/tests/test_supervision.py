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
Tests for the supervision.py module — a real local UDP syslog receiver
(no mocking), so actually run and verified while this file was written,
unlike the antivirus.py (ICAP) and metrics.py (prometheus_client) tests,
which depend on external libraries not available in the environment
where this file was written.
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


@pytest.fixture(autouse=True)
def _reset_alert_sink_cache():
    """get_alert_sink() is cached (@lru_cache) to avoid rebuilding a
    socket for every alert in production — but that means that from one
    test to the next, without this fixture, all tests would get the
    instance from the very first successful call, regardless of the
    ALERT_SINK config each one simulates."""
    get_alert_sink.cache_clear()
    yield
    get_alert_sink.cache_clear()


@pytest.fixture
def local_syslog_receiver():
    """A real local UDP receiver, not a mock — receives what
    SyslogAlertSink actually sends over the network (localhost)."""
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

    assert len(received) == 1, "the syslog message never arrived"
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


def test_get_alert_sink_est_mis_en_cache(monkeypatch):
    """Without caching, each alert would rebuild a socket + a named logger
    that is never cleaned up (memory/FD leak) — see security review,
    section 10."""
    monkeypatch.setenv("ALERT_SINK", "syslog")
    monkeypatch.setenv("SYSLOG_HOST", "127.0.0.1")
    monkeypatch.setenv("SYSLOG_PORT", "5140")
    first = get_alert_sink()
    second = get_alert_sink()
    assert first is second


def test_get_alert_sink_reessaie_apres_un_echec(monkeypatch):
    """lru_cache never memoizes an exception: a syslog host unreachable
    on the first call must not prevent a later call (once the config is
    fixed) from retrying construction."""
    monkeypatch.setenv("ALERT_SINK", "syslog")
    monkeypatch.delenv("SYSLOG_HOST", raising=False)
    with pytest.raises(RuntimeError):
        get_alert_sink()

    monkeypatch.setenv("SYSLOG_HOST", "127.0.0.1")
    assert isinstance(get_alert_sink(), SyslogAlertSink)
