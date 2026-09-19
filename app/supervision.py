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
enterprise SIEM side, already available in the Python standard library
(logging.handlers.SysLogHandler) — no external dependency needed for
this first adapter, unlike the antivirus (ICAP).

IMPORTANT RULE, to be respected for any alert added later: never put
personal data in `message`/`details` — a hashed file name or a job
identifier, yes; a patient name or a user email, no. An alert
potentially goes out to a third-party SIEM, outside this project's
direct control.
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
    """RFC 5424 adapter via logging.handlers.SysLogHandler (standard
    library, no external dependency needed for this old and stable
    protocol)."""

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


@lru_cache(maxsize=1)
def get_alert_sink() -> AlertSink:
    """
    Single configuration point, same principle as get_scanner() for
    the antivirus.

    Cached (the config never changes during the container's lifetime):
    without this cache, every alert would rebuild a full SyslogAlertSink
    (new socket + new named logger registered indefinitely by the
    logging module — never garbage-collected) — unbounded memory/FD
    leak, amplifiable by an attacker able to trigger alerts at will
    (e.g. uploads detected as a threat). `lru_cache` only caches
    successful calls (an exception is never memoized): a valid config
    thus stays cached for good, while a temporarily unreachable syslog
    host is retried on every subsequent call rather than staying broken
    forever.

    Do NOT call `.send()` directly on the returned value without
    catching exceptions on the caller side (see `_send_alert` in
    main.py): building a SyslogAlertSink can raise (invalid config, DNS
    unreachable, connection refused in TCP) or, in TCP, block for
    several seconds/minutes on a `connect()` with no timeout if the
    host does not respond — an alert must never cause the flow it is
    supposed to monitor to fail or freeze.
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
