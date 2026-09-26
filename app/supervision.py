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
supervision.py — generic interface for sending operational alerts
(unreachable dependency, disk full, certificate about to expire,
antivirus threat detected...), with a syslog adapter (RFC 5424) as
first implementation.

Choosing syslog first: an old and universally supported standard on the
enterprise SIEM side, implementable with the Python standard library
alone (socket) — no external dependency needed for this first adapter,
unlike the antivirus (ICAP).

IMPORTANT RULE, to be respected for any alert added later: never put
personal data in `message`/`details` — a hashed file name or a job
identifier, yes; a patient name or a user email, no. An alert
potentially goes out to a third-party SIEM, outside this project's
direct control.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import select
import socket
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache

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
    """No real sending — used when ALERT_SINK=none (default).
    Logs locally so the information is not silently lost."""

    def send(self, alert: Alert) -> None:
        log.warning(
            "[ALERT_SINK=none, not forwarded] [%s] %s: %s | %s",
            alert.severity.value,
            alert.source,
            alert.message,
            alert.details,
        )


class SyslogAlertSink(AlertSink):
    """
    RFC 5424 syslog sender (UDP, or TCP framed per RFC 6587), standard
    library only.

    Deliberately NOT logging.handlers.SysLogHandler — three defects
    confirmed end-to-end against a real rsyslog collector:
    - no RFC 5424 header (it sends "<PRI>msg": no version, timestamp or
      hostname), which a strict SIEM may reject;
    - in TCP, each message is terminated by a NUL byte instead of an
      RFC 6587 frame delimiter: a standard rsyslog kept ALL alerts
      buffered as long as the connection stayed open (i.e. forever, the
      sink being cached) and delivered them only on disconnect, merged
      into a single message carrying the first one's severity;
    - in TCP, no reconnection: once the connection breaks (collector
      restart), every later alert is lost, the error being swallowed by
      handleError() — invisible to `_send_alert` in main.py.

    Here: one RFC 5424 message per alert, UDP datagram or TCP line
    terminated by LF (RFC 6587 §3.4.2 non-transparent framing — safe
    because the JSON payload never contains a raw LF, json.dumps escapes
    it). TCP: lazy connection with a timeout, reconnection when the
    collector closed or broke the connection.

    Delivery is ASYNCHRONOUS: send() only formats the message and puts it
    in a bounded queue, a dedicated thread does the network I/O. Callers
    run inside the request path — `/api/detect` is an async endpoint whose
    synchronous work blocks the whole event loop (measured: /health
    stalled 5.5 s behind one upload waiting on the antivirus) — so a
    hung collector, a slow DNS lookup or a TCP connect timeout must never
    reach them. An alert that cannot be delivered (collector down) is
    retried with backoff for up to `max_retry_seconds`, in order, then
    dropped with a local error log: main.py sends periodic alerts only on
    state CHANGE, a single lost alert would otherwise hide an incident
    until the next reminder.
    """

    _FACILITY_LOCAL0 = 16
    _SEVERITY_CODE = {  # RFC 5424 §6.2.1
        AlertSeverity.INFO: 6,
        AlertSeverity.WARNING: 4,
        AlertSeverity.CRITICAL: 2,
    }
    _APP_NAME = "anonymiseur"
    # ~2 KB per alert at most: bounds the memory held during a long
    # collector outage to a few MB, whatever the alert rate.
    _QUEUE_SIZE = 1000

    def __init__(
        self,
        host: str,
        port: int = 514,
        use_tcp: bool = False,
        timeout: float = 5.0,
        max_retry_seconds: float = 600.0,
        max_backoff_seconds: float = 30.0,
    ):
        self.host = host
        self.port = port
        self.use_tcp = use_tcp
        self.timeout = timeout
        self.max_retry_seconds = max_retry_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._hostname = _rfc5424_token(socket.gethostname(), 255)
        self._sock: socket.socket | None = None
        self._queue: queue.Queue = queue.Queue(maxsize=self._QUEUE_SIZE)
        self._worker = threading.Thread(target=self._run, name="syslog-alert-sender", daemon=True)
        self._worker.start()

    def _format(self, alert: Alert) -> bytes:
        pri = self._FACILITY_LOCAL0 * 8 + self._SEVERITY_CODE[alert.severity]
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        payload = json.dumps(
            {"source": alert.source, "message": alert.message, "details": alert.details},
            ensure_ascii=False,
        )
        # <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID STRUCTURED-DATA MSG
        return (
            f"<{pri}>1 {timestamp} {self._hostname} {self._APP_NAME} {os.getpid()} "
            f"{_rfc5424_token(alert.source, 32)} - {payload}"
        ).encode("utf-8", errors="replace")  # a lone surrogate (external data, e.g. an ICAP threat name) must not drop the alert

    def send(self, alert: Alert) -> None:
        """Never blocks. Raises only if the queue is full (collector down
        for a long time and alerts piling up) — `_send_alert` logs it."""
        try:
            self._queue.put_nowait((self._format(alert), time.monotonic(), alert.source, alert.severity.value))
        except queue.Full:
            raise RuntimeError("syslog alert queue full, alert dropped") from None

    def _run(self) -> None:
        while True:
            frame, queued_at, source, severity = self._queue.get()
            backoff = 1.0
            while True:
                try:
                    self._deliver(frame)
                    break
                except Exception as exc:  # not only OSError: any escape would kill this thread for good (e.g. UnicodeError on a malformed SYSLOG_HOST)
                    if time.monotonic() - queued_at > self.max_retry_seconds:
                        log.error(
                            "Alerte syslog abandonnée après %.0f s d'échecs (source=%s, sévérité=%s): %s",
                            self.max_retry_seconds, source, severity, exc,
                        )
                        break
                    log.warning(
                        "Envoi syslog échoué (source=%s, sévérité=%s), nouvel essai dans %.0f s: %s",
                        source, severity, backoff, exc,
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, self.max_backoff_seconds)

    def _deliver(self, frame: bytes) -> None:
        if not self.use_tcp:
            family, socktype, proto, _, addr = socket.getaddrinfo(
                self.host, self.port, type=socket.SOCK_DGRAM
            )[0]
            with socket.socket(family, socktype, proto) as sock:
                sock.sendto(frame, addr)
            return
        if self._sock is not None and self._peer_closed():
            self._close()
        try:
            if self._sock is None:
                self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._sock.sendall(frame + b"\n")
        except OSError:
            self._close()
            raise

    def _peer_closed(self) -> bool:
        """True if the collector closed the connection. Checked BEFORE
        sending: after a remote close, sendall() still succeeds locally
        and the alert is lost — the error only shows on the next send.
        poll(), not select(): the app's seccomp profile only allows poll
        (select fails with ENOSYS there, confirmed), and select() is
        limited to file descriptors below 1024."""
        poller = select.poll()
        poller.register(self._sock, select.POLLIN)
        if not poller.poll(0):
            return False
        try:
            return self._sock.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def _close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def _rfc5424_token(value: str, max_len: int) -> str:
    """HOSTNAME/MSGID fields: printable US-ASCII without spaces, "-" if empty
    (RFC 5424 §6.2)."""
    token = "".join(c for c in value if 33 <= ord(c) <= 126)[:max_len]
    return token or "-"


@lru_cache(maxsize=1)
def get_alert_sink() -> AlertSink:
    """
    Single configuration point, same principle as get_scanner() for
    the antivirus.

    Cached (the config never changes during the container's lifetime):
    without this cache, every alert would rebuild a SyslogAlertSink and,
    in TCP, a new connection — FD leak amplifiable by an attacker able
    to trigger alerts at will (e.g. uploads detected as a threat), and
    the TCP reconnection logic would be pointless. `lru_cache` only
    caches successful calls (an exception is never memoized): an invalid
    config is retried on every subsequent call rather than staying
    broken forever. Network problems (DNS, connection refused, broken
    TCP connection) are handled by the sink's sender thread (retries,
    then a local error log), never at construction nor in `.send()`.

    `.send()` never blocks, but can still raise (queue full during a long
    collector outage): do NOT call it without catching exceptions on the
    caller side (see `_send_alert` in main.py) — an alert must never
    cause the flow it is supposed to monitor to fail.
    """
    sink = os.environ.get("ALERT_SINK", "none").strip().lower()

    if sink == "none":
        log.warning(
            "ALERT_SINK=none: no alert forwarded outside local application "
            "logs. Must be configured before any production deployment."
        )
        return NullAlertSink()

    if sink == "syslog":
        host = os.environ.get("SYSLOG_HOST")
        if not host:
            raise RuntimeError("ALERT_SINK=syslog requires SYSLOG_HOST")
        return SyslogAlertSink(
            host=host,
            port=int(os.environ.get("SYSLOG_PORT", "514")),
            use_tcp=os.environ.get("SYSLOG_TCP", "false").strip().lower() == "true",
        )

    raise ValueError(f"Unknown ALERT_SINK: {sink!r} (valid values: none, syslog)")
