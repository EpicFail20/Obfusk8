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
Minimal client for the benchmarks: authenticates THROUGH THE FULL CHAIN
(Traefik -> oauth2-proxy -> Keycloak login form) like a browser, then calls
the text API. Nothing is measured behind Traefik's back.

Configuration (environment only — credentials never go in the repository):
  BENCH_BASE_URL      e.g. https://obfusk8.lab.local
  BENCH_USER          test account (synthetic, lab only)
  BENCH_PASSWORD
  BENCH_INSECURE_TLS  "1" for the lab's self-signed certificate
  BENCH_RESOLVE_ADDRESS   address to resolve *.lab.local to when the lab
                      names are not in /etc/hosts (VM-local runs): the
                      stack's BIND_ADDRESS, since it listens there only (D-047)
  BENCH_RESOLVE_LOOPBACK  "1": same with 127.0.0.1 (BIND_ADDRESS=127.0.0.1)
  BENCH_ACCOUNTS_FILE "user, password" per line, outside the repository
                      (default ~/.obfusk8-test-accounts), for multi-user runs
  BENCH_EXTENSION_ORIGIN  Origin sent to /api/v1/ (default: the lab extension);
                      must be in the server's EXTENSION_ALLOWED_ORIGINS
"""

import html
import os
import re
import socket
import time
from pathlib import Path
from typing import Any

import requests

HTTP_OK = 200
HTTP_ACCEPTED = 202
HTTP_TOO_MANY_REQUESTS = 429

BASE_URL = os.environ.get("BENCH_BASE_URL", "https://obfusk8.lab.local").rstrip("/")
VERIFY_TLS = os.environ.get("BENCH_INSECURE_TLS") != "1"
# Phase 3 (D-054 point 6): /api/v1/ serves only the Origin of an allowed
# browser extension, as Chrome sends it from the side panel. Default: the lab
# extension, whose identifier is stable (public key in config/lab.json of the
# obfusk8-extension repository). Sent per request: the session itself carries
# the application's Origin for the document routes (see login()).
EXTENSION_ORIGIN = os.environ.get("BENCH_EXTENSION_ORIGIN", "chrome-extension://glaimpfdmfkidcgalcblojmkomplcgpa")
TEXT_API_HEADERS = {"Origin": EXTENSION_ORIGIN}


def lab_address() -> str | None:
    """Address the *.lab.local names resolve to, or None (system resolver)."""
    address = os.environ.get("BENCH_RESOLVE_ADDRESS", "").strip()
    if address:
        return address
    return "127.0.0.1" if os.environ.get("BENCH_RESOLVE_LOOPBACK") == "1" else None


_LAB_ADDRESS = lab_address()
if _LAB_ADDRESS is not None:
    _real_getaddrinfo = socket.getaddrinfo

    def _lab_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(host, str) and host.endswith(".lab.local"):
            host = _LAB_ADDRESS
        return _real_getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = _lab_getaddrinfo

if not VERIFY_TLS:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


ACCOUNTS_FILE = Path(os.environ.get("BENCH_ACCOUNTS_FILE", "~/.obfusk8-test-accounts")).expanduser()


def read_accounts() -> list[tuple[str, str, str]]:
    """(label, user, password) of each test account. Only the label
    (compte-N) is ever printed or written: never a name or a password."""
    out = []
    for index, line in enumerate(ACCOUNTS_FILE.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            user, password = (part.strip() for part in line.split(",", 1))
            out.append((f"compte-{index}", user, password))
    return out


def login(user: str | None = None, password: str | None = None) -> requests.Session:
    """Session of BENCH_USER/BENCH_PASSWORD, or of the account given (phase 2,
    multi-user benchmark)."""
    session = requests.Session()
    session.verify = VERIFY_TLS
    page = session.get(f"{BASE_URL}/oauth2/start?rd=%2F", timeout=30)
    form = re.search(r'<form[^>]+id="kc-form-login"[^>]+action="([^"]+)"', page.text)
    if not form:
        raise RuntimeError(f"Keycloak login form not found (HTTP {page.status_code})")
    session.post(
        html.unescape(form.group(1)),
        data={
            "username": user if user is not None else os.environ["BENCH_USER"],
            "password": password if password is not None else os.environ["BENCH_PASSWORD"],
            "credentialId": "",
        },
        timeout=30,
    )
    # EXT-52: a cookie name proved nothing (oauth2-proxy sets
    # `_oauth2_proxy_csrf` before any authentication, and Keycloak answers a
    # wrong password with its login form and a 200). Ask oauth2-proxy itself:
    # /oauth2/auth answers 202 for an authenticated session only (401
    # otherwise, observed on the stack on 2026-10-07).
    check = session.get(f"{BASE_URL}/oauth2/auth", timeout=30, allow_redirects=False)
    if check.status_code != HTTP_ACCEPTED:
        raise RuntimeError(f"login failed: /oauth2/auth answered HTTP {check.status_code}")
    # Like a browser on the application's pages: the document routes require
    # this Origin (D-048 point 3). Set only now, never sent to Keycloak.
    session.headers["Origin"] = BASE_URL
    return session


_JOB_ID = re.compile(r'name="job_id" value="([0-9a-f]{32})"')


def cancel_job(session: Any, review_page: str) -> None:
    """Cancels the document whose review page is given, so that a benchmark
    never holds more than one pending document: since D-048 a user may hold
    MAX_PENDING_JOBS_PER_USER (3) of them, and a fourth upload gets a 429.
    Needs ENABLE_JOB_CANCEL=true (the default). Stops the benchmark on any
    failure rather than measuring with jobs piling up."""
    match = _JOB_ID.search(review_page)
    if match is None:
        raise RuntimeError("cancel: no job_id in the review page")
    headers = {"X-Obfusk8-Action": "cancel", "Accept": "application/json"}
    response = session.post(f"{BASE_URL}/api/cancel/{match.group(1)}", headers=headers, timeout=30)
    if response.status_code != HTTP_OK:
        raise RuntimeError(f"cancel: HTTP {response.status_code}")


class TextApi:
    """Calls /api/v1/text/* with pacing below the per-user Traefik rate limit
    (60/min, burst 20): a 429 from Traefik is waited out and retried, never
    counted as a detection result."""

    def __init__(self, session: requests.Session, min_interval: float = 1.05) -> None:
        self.session = session
        self.min_interval = min_interval
        self._last = 0.0

    def call(self, route: str, text: str, theme: str | None = None) -> tuple[int, dict[str, Any], float]:
        payload: dict[str, Any] = {"text": text}
        if theme:
            payload["theme"] = theme
        for _ in range(10):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            started = time.monotonic()
            response = self.session.post(
                f"{BASE_URL}/api/v1/text/{route}", json=payload, headers=TEXT_API_HEADERS, timeout=60
            )
            elapsed = time.monotonic() - started
            self._last = time.monotonic()
            if response.status_code == HTTP_TOO_MANY_REQUESTS and "request_id" not in response.text:
                time.sleep(float(response.headers.get("Retry-After", "2")))
                continue
            return response.status_code, response.json(), elapsed
        raise RuntimeError("rate limited 10 times in a row")

    def version(self, wait_seconds: float = 60) -> dict[str, Any]:
        """Also waits for the stack: right after `app` is recreated, Traefik
        answers 404/502 until it has registered the new container."""
        deadline = time.monotonic() + wait_seconds
        while True:
            response = self.session.get(f"{BASE_URL}/api/v1/version", headers=TEXT_API_HEADERS, timeout=30)
            if response.status_code == HTTP_OK:
                body: dict[str, Any] = response.json()
                return body
            if time.monotonic() > deadline:
                raise RuntimeError(f"/api/v1/version unavailable (HTTP {response.status_code})")
            time.sleep(2)
