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
EXT-52: the benchmark client believed a failed sign-in had succeeded. Its
check (any cookie named `_oauth2_proxy*`) was met by oauth2-proxy's CSRF
cookie, set before authentication. login() now asks oauth2-proxy itself
(/oauth2/auth: 202 only for an authenticated session, 401 otherwise,
observed on the stack on 2026-10-07) and stops otherwise.

The client (benchmarks/obfusk8_client.py, mounted read-only from the
repository by app/run-tests.sh) runs against a fake HTTP session: no network.
"""

import importlib.util
import os
import socket
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

BASE = "https://obfusk8.test.invalid"
FORM_ACTION = "http://keycloak.test.invalid/realms/lab/login-actions/authenticate?x=1"
LOGIN_PAGE = f'<form id="kc-form-login" class="f" action="{FORM_ACTION}" method="post"></form>'


def _client(monkeypatch: pytest.MonkeyPatch, **env: str) -> ModuleType:
    monkeypatch.setenv("BENCH_BASE_URL", BASE)
    for name in ("BENCH_RESOLVE_LOOPBACK", "BENCH_RESOLVE_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    # The client patches socket.getaddrinfo at import when asked to resolve
    # the lab names: restored after the test.
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    path = Path(os.environ.get("OBFUSK8_REPO", "")) / "benchmarks" / "obfusk8_client.py"
    assert path.is_file(), "OBFUSK8_REPO must point to the repository (see app/run-tests.sh)"
    spec = importlib.util.spec_from_file_location("obfusk8_client_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _Cookie:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSession:
    """Records every request with the headers it carried at that moment."""

    auth_status = 401

    def __init__(self) -> None:
        self.verify = True
        self.headers: dict[str, str] = {}
        # Set by oauth2-proxy on /oauth2/start, before any authentication.
        self.cookies = [_Cookie("_oauth2_proxy_csrf")]
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append(("GET", url, dict(self.headers)))
        if url.endswith("/oauth2/auth"):
            return _Response(self.auth_status)
        return _Response(200, LOGIN_PAGE)

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append(("POST", url, dict(self.headers)))
        # Wrong password: Keycloak shows its login form again, with a 200.
        return _Response(200, LOGIN_PAGE)


def _with_session(monkeypatch: pytest.MonkeyPatch, client: ModuleType, auth_status: int) -> list[_FakeSession]:
    sessions: list[_FakeSession] = []

    def factory() -> _FakeSession:
        session = _FakeSession()
        session.auth_status = auth_status
        sessions.append(session)
        return session

    monkeypatch.setattr(client.requests, "Session", factory)
    return sessions


def test_ext52_identifiants_refuses_echec_immediat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduces EXT-52: wrong credentials, only the CSRF cookie present.
    The old check accepted this session."""
    client = _client(monkeypatch)
    _with_session(monkeypatch, client, auth_status=401)
    with pytest.raises(RuntimeError, match="login failed"):
        client.login("compte-fictif", "mot-de-passe-faux")


def test_session_authentifiee_acceptee_et_verifiee_aupres_d_oauth2_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    sessions = _with_session(monkeypatch, client, auth_status=202)
    session = client.login("compte-fictif", "mot-de-passe-fictif")
    assert session is sessions[0]
    assert ("GET", f"{BASE}/oauth2/auth", {}) in [(m, u, h) for m, u, h in session.calls]


def test_session_porte_l_origine_de_l_application_comme_un_navigateur(monkeypatch: pytest.MonkeyPatch) -> None:
    """The document routes require the application's Origin (D-048 point 3);
    it is added once signed in, never sent to Keycloak."""
    client = _client(monkeypatch)
    sessions = _with_session(monkeypatch, client, auth_status=202)
    session = client.login("compte-fictif", "mot-de-passe-fictif")
    assert session.headers["Origin"] == BASE
    keycloak_calls = [headers for _, url, headers in sessions[0].calls if url.startswith("http://keycloak")]
    assert keycloak_calls
    assert all("Origin" not in headers for headers in keycloak_calls)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, None),
        ({"BENCH_RESOLVE_LOOPBACK": "1"}, "127.0.0.1"),
        ({"BENCH_RESOLVE_ADDRESS": "192.0.2.10"}, "192.0.2.10"),
        ({"BENCH_RESOLVE_LOOPBACK": "1", "BENCH_RESOLVE_ADDRESS": "192.0.2.10"}, "192.0.2.10"),
    ],
)
def test_adresse_de_resolution_des_noms_du_laboratoire(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str | None
) -> None:
    """Since D-047 the stack listens on BIND_ADDRESS only, not on 127.0.0.1."""
    client = _client(monkeypatch, **env)
    assert client.lab_address() == expected
