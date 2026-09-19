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
antivirus.py — generic antivirus scanning interface, with an ICAP adapter
(RFC 3507) as first implementation.

Goal: accommodate an enterprise antivirus already deployed on the client
infrastructure side, by speaking a standard protocol rather than a
proprietary API — engine swap possible without rewriting the application
integration (see get_scanner()).

Deliberately excluded: any cloud/SaaS engine (VirusTotal and equivalents).
Sending a document — potentially real medical data, not yet anonymized at
this stage of the pipeline — to an external third-party service would go
directly against all the network isolation work and structural leak fixes
made on this project.
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
    Raised when the engine cannot give a reliable verdict (timeout,
    connection refused, protocol error). The caller MUST reject the
    file in this case — never treat it as clean by default.
    Fail-closed policy, consistent with the rest of the project (e.g. a
    PII detection engine that is unavailable must never let an
    unverified document through).
    """


class AntivirusScanner(ABC):
    """Common interface for any antivirus engine usable by the application."""

    @abstractmethod
    def scan(self, file_bytes: bytes, filename_hint: str = "") -> ScanResult:
        """Scans the provided content. Must raise AntivirusUnavailableError
        rather than returning an uncertain verdict such as "clean"."""
        raise NotImplementedError


class NullScanner(AntivirusScanner):
    """
    No real scan — used when AV_ENGINE=none (default). Exists
    explicitly, rather than silently skipping the scan, so that the
    absence of protection is a visible choice (warning log at
    startup) rather than a configuration oversight.
    """

    def scan(self, file_bytes: bytes, filename_hint: str = "") -> ScanResult:
        return ScanResult(is_clean=True, engine_name="none (scan disabled)")


class IcapScanner(AntivirusScanner):
    """
    Adapter for an ICAP server (RFC 3507) already deployed on the client
    infrastructure side — enterprise antivirus, dedicated scanning
    gateway (e.g. FortiSandbox, a c-icap/SquidClamav server...).

    WARNING, important architecture point: this assumes a genuine ICAP
    SERVER directly reachable. FortiGate itself acts as an ICAP
    CLIENT (it forwards traffic passing through it to an external
    server) — it CANNOT be the direct target of this adapter. Confirm
    with the security team which server is the actual ICAP server to
    target (FortiSandbox or equivalent) before configuring
    ICAP_HOST/ICAP_PORT.

    Uses python-icap (RFC 3507, zero dependencies, MIT license).
    Acknowledged risk point: a young library at the time this code was
    written (status "Alpha" on PyPI, a single maintainer) — the ICAP
    protocol itself is old and stable (RFC from 2003), which limits the
    risk associated with how young this client implementation is, but
    it should still be monitored like any recent dependency (see the
    project's dependency-watch policy, audit section 4).
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
        # Allows injecting a client (real or mocked) for tests — without
        # this, it would be impossible to use python-icap's pytest plugin
        # (MockIcapClient) without monkeypatching the import, which is more
        # fragile.
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
            log.error("ICAP scan failed (%s:%s): %s", self.host, self.port, exc)
            raise AntivirusUnavailableError(
                f"ICAP server unreachable or in error: {exc}"
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
    Single configuration point: switch engines via an environment
    variable, without touching the calling code (see main.py).

    Cached (the config never changes during the container's lifetime):
    avoids rebuilding a scanner and replaying the "scan disabled" warning
    on every request, and lets main.py validate the config only once at
    startup (fail fast if misconfigured) rather than discovering an
    invalid AV_ENGINE/ICAP_* on the first request that comes along.
    """
    engine = os.environ.get("AV_ENGINE", "none").strip().lower()

    if engine == "none":
        log.warning(
            "AV_ENGINE=none: no antivirus scan active. Must be configured "
            "before any production deployment with real data."
        )
        return NullScanner()

    if engine == "icap":
        host = os.environ.get("ICAP_HOST")
        if not host:
            raise RuntimeError("AV_ENGINE=icap requires ICAP_HOST")
        return IcapScanner(
            host=host,
            port=int(os.environ.get("ICAP_PORT", "1344")),
            service=os.environ.get("ICAP_SERVICE", "avscan"),
            timeout=float(os.environ.get("ICAP_TIMEOUT_SECONDS", "30")),
            use_tls=os.environ.get("ICAP_TLS", "false").strip().lower() == "true",
        )

    raise ValueError(f"Unknown AV_ENGINE: {engine!r} (valid values: none, icap)")


def is_av_enforced() -> bool:
    """
    Indicates whether an unfavorable antivirus verdict (threat detected OR
    engine unreachable) must block processing of the file, or should only
    be logged while letting the file continue through the pipeline.

    Deliberately INDEPENDENT from AV_ENGINE: allows deploying a new
    engine in "observation" mode (see what it would have blocked,
    without disrupting real application usage) before switching it to
    blocking — same logic as SCMP_ACT_LOG mode before actual blocking
    for the seccomp profile.

    Defaults to True as soon as a real engine is configured (consistent
    with the principle already established in this project: better to
    wrongly block than to let a real threat through) — set explicitly to
    "false" for an observation-only deployment.
    """
    return os.environ.get("AV_ENFORCE", "true").strip().lower() == "true"
