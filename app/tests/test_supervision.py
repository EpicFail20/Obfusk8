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
import re
import socket
import sys
import threading
import time
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
    # RFC 5424 header: <PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID SD MSG
    # (local0 = 16, critical = 2 -> PRI 130), MSGID = the alert source.
    match = re.match(r"<130>1 \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z \S+ anonymiseur \d+ dependency-check - (\{.*\})$", raw)
    assert match, raw
    assert json.loads(match.group(1))["details"]["cve"] == "CVE-2026-XXXX"
    assert "\x00" not in raw


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


@pytest.mark.parametrize(
    "severity,pri",
    [(AlertSeverity.INFO, 134), (AlertSeverity.WARNING, 132), (AlertSeverity.CRITICAL, 130)],
)
def test_severite_encodee_dans_la_priorite(local_syslog_receiver, severity, pri):
    port, received, thread = local_syslog_receiver
    SyslogAlertSink(host="127.0.0.1", port=port).send(Alert(severity=severity, source="t", message="m"))
    thread.join(timeout=3)
    assert received and received[0].startswith(f"<{pri}>1 ")


def test_msgid_hors_ascii_imprimable_neutralise(local_syslog_receiver):
    port, received, thread = local_syslog_receiver
    SyslogAlertSink(host="127.0.0.1", port=port).send(
        Alert(severity=AlertSeverity.INFO, source="disk space\u202e", message="m")
    )
    thread.join(timeout=3)
    assert " diskspace - {" in received[0]


def test_donnee_externe_malformee_ni_perte_ni_injection_de_trame(local_syslog_receiver):
    """A lone surrogate must not drop the alert; a LF in a detail must not
    create a second syslog frame (TCP framing is LF-based)."""
    port, received, thread = local_syslog_receiver
    SyslogAlertSink(host="127.0.0.1", port=port).send(
        Alert(severity=AlertSeverity.CRITICAL, source="antivirus", message="m",
              details={"threat": "Eicar\udcff\n<130>1 - faux - - - - injecté"})
    )
    thread.join(timeout=3)
    assert len(received) == 1
    raw = received[0]
    assert "\n" not in raw  # no raw LF: no second frame possible
    threat = json.loads(raw.split(" - ", 1)[1])["details"]["threat"]
    assert threat.startswith("Eicar") and "\n<130>1" in threat  # kept as data inside the JSON


class _TcpReceiver:
    """A real local TCP syslog receiver that splits frames on LF, like a
    standard rsyslog (RFC 6587 non-transparent framing)."""

    def __init__(self, port: int = 0):
        self.server = socket.create_server(("127.0.0.1", port))
        self.port = self.server.getsockname()[1]
        self.frames: list[str] = []
        self.connections = 0
        self._conns: list[socket.socket] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self.connections += 1
            self._conns.append(conn)
            buf = b""
            with conn:
                while True:
                    try:
                        chunk = conn.recv(65535)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self.frames.append(line.decode("utf-8"))

    def wait_frames(self, n: int, timeout: float = 3.0) -> list[str]:
        deadline = time.monotonic() + timeout
        while len(self.frames) < n and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.frames

    def close(self):
        """Stops listening AND closes the open connections, as a collector
        restart does."""
        self.server.close()
        for conn in self._conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()


def test_tcp_chaque_alerte_livree_immediatement_et_separement():
    """Regression: with logging.handlers.SysLogHandler, TCP messages were
    NUL-terminated — a real rsyslog kept them all buffered while the
    connection stayed open, then delivered them merged into one message."""
    receiver = _TcpReceiver()
    try:
        sink = SyslogAlertSink(host="127.0.0.1", port=receiver.port, use_tcp=True)
        sink.send(Alert(severity=AlertSeverity.CRITICAL, source="antivirus", message="un"))
        sink.send(Alert(severity=AlertSeverity.WARNING, source="presidio", message="deux\navec saut de ligne"))
        frames = receiver.wait_frames(2)  # connection still open
        assert len(frames) == 2, frames
        assert frames[0].startswith("<130>1 ") and '"un"' in frames[0]
        assert frames[1].startswith("<132>1 ") and "deux\\navec" in frames[1]  # LF escaped by JSON
        assert receiver.connections == 1  # one persistent connection
    finally:
        receiver.close()


def test_tcp_reconnexion_apres_redemarrage_du_collecteur():
    """Regression: SysLogHandler never reconnected in TCP — after a
    collector restart every later alert was silently lost. The very first
    alert after the restart must arrive too (not only the next ones)."""
    first = _TcpReceiver()
    port = first.port
    sink = SyslogAlertSink(host="127.0.0.1", port=port, use_tcp=True)
    sink.send(Alert(severity=AlertSeverity.INFO, source="t", message="avant"))
    assert len(first.wait_frames(1)) == 1
    first.close()  # collector restart: its end of the connection is closed
    time.sleep(0.2)
    second = _TcpReceiver(port)
    try:
        sink.send(Alert(severity=AlertSeverity.INFO, source="t", message="apres"))
        frames = second.wait_frames(1)
        assert len(frames) == 1 and '"apres"' in frames[0]
    finally:
        second.close()


def _free_port() -> int:
    probe = socket.create_server(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # nothing listens on this port any more
    return port


def test_envoi_jamais_bloquant_et_alerte_livree_au_retour_du_collecteur():
    """send() runs in the request path of an async endpoint (blocking
    there freezes the whole app): it must return at once even with the
    collector down, and the alert must still arrive once it is back —
    periodic alerts are sent only on state change, a lost one would hide
    an incident."""
    port = _free_port()
    sink = SyslogAlertSink(host="127.0.0.1", port=port, use_tcp=True, timeout=1, max_backoff_seconds=0.2)
    start = time.monotonic()
    sink.send(Alert(severity=AlertSeverity.CRITICAL, source="t", message="pendant la panne"))
    assert time.monotonic() - start < 0.1
    time.sleep(0.5)  # a few failed attempts
    receiver = _TcpReceiver(port)
    try:
        frames = receiver.wait_frames(1, timeout=5)
        assert len(frames) == 1 and '"pendant la panne"' in frames[0]
    finally:
        receiver.close()


def test_alerte_abandonnee_apres_le_delai_de_reessai(caplog):
    port = _free_port()
    sink = SyslogAlertSink(host="127.0.0.1", port=port, use_tcp=True, timeout=1,
                           max_retry_seconds=0.3, max_backoff_seconds=0.1)
    with caplog.at_level("ERROR", logger="anonymiseur.supervision"):
        sink.send(Alert(severity=AlertSeverity.WARNING, source="disk-space", message="m"))
        deadline = time.monotonic() + 5
        while not caplog.records and time.monotonic() < deadline:
            time.sleep(0.05)
    assert any("abandonnée" in r.getMessage() and "disk-space" in r.getMessage() for r in caplog.records)


def test_file_pleine_signalee_a_l_appelant(monkeypatch):
    """Bounded memory during a long outage; the caller (_send_alert) is
    told, instead of a silent loss."""
    monkeypatch.setattr(SyslogAlertSink, "_QUEUE_SIZE", 2)
    sink = SyslogAlertSink(host="127.0.0.1", port=_free_port(), use_tcp=True, timeout=1,
                           max_retry_seconds=0.3, max_backoff_seconds=0.1)  # drains quickly afterwards
    with pytest.raises(RuntimeError):
        for _ in range(10):  # the worker holds one, the queue two more
            sink.send(Alert(severity=AlertSeverity.INFO, source="t", message="m"))


def test_thread_d_envoi_survit_a_une_erreur_non_reseau(caplog):
    """getaddrinfo raises UnicodeError (not OSError) on a malformed host:
    it must not kill the sender thread, or every later alert would pile up
    forever without a trace."""
    sink = SyslogAlertSink(host="a" * 70 + ".corp", port=514, max_retry_seconds=0.2, max_backoff_seconds=0.05)
    with caplog.at_level("WARNING", logger="anonymiseur.supervision"):
        sink.send(Alert(severity=AlertSeverity.INFO, source="survie", message="1"))
        sink.send(Alert(severity=AlertSeverity.INFO, source="survie", message="2"))
        deadline = time.monotonic() + 5
        while sum(("abandonnée" in r.getMessage() and "source=survie" in r.getMessage()) for r in caplog.records) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    assert sum(("abandonnée" in r.getMessage() and "source=survie" in r.getMessage()) for r in caplog.records) == 2
    assert sink._worker.is_alive()
