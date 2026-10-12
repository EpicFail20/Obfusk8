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
Bearer token required on /api/v1/ (phase "option 2", D-057 point 8).

With ENABLE_EXTENSION_OIDC=true, oauth2-proxy verifies the extension's own
token (issuer, signature, audience, expiry) and, with
bearer_token_login_fallback=false, answers 403 to ANY invalid Authorization
header instead of falling back to the session cookie. A request that reaches
the application with `Authorization: Bearer …` was therefore authenticated
by that token. EXTENSION_REQUIRE_BEARER=true (production) refuses the other
requests, those authenticated by the session cookie, which any extension
holding a host permission could ride (D-054 point 6): 401, so that the
extension signs in. Off by default: the lab extension keeps the cookie.
"""

import json
import logging
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI

import main
import text_api
from tests import fake_secrets
from tests import test_text_api as base

Response = tuple[int, dict[str, str], Any, int]
ANALYZE: str = base.ANALYZE
PSEUDO: str = base.PSEUDO
CANARY: str = base.CANARY
DETECT: dict[str, str] = base.DETECT
STRINGS: dict[str, str] = base.STRINGS
_app = cast(Callable[..., FastAPI], base._app)
_audit_lines = cast(Callable[[Path], list[dict[str, Any]]], base._audit_lines)
_call = cast(Callable[..., Any], base._call)
_deps = cast(Callable[..., text_api.TextApiDeps], base._deps)
_post = cast(Callable[..., Response], base._post)
_regex_detector = cast(Callable[[dict[str, str]], Any], base._regex_detector)
_run = cast(Callable[[Any], Response], base._run)
audit_dir = base.audit_dir  # pytest fixtures, used by parameter name
extension_dir = base.extension_dir

VERSION = "/api/v1/version"
# A fictitious JWT built at run time (fake_secrets, EXT-39 rule).
TOKEN_CANARY = fake_secrets.JWT_HS256
BEARER = {"authorization": f"Bearer {TOKEN_CANARY}"}
STRICT = {"oidc_enabled": True, "require_bearer": True}


# --- configuration -----------------------------------------------------------


def test_desactive_par_defaut() -> None:
    settings = text_api.TextApiSettings.from_env({})
    assert settings.oidc_enabled is False and settings.require_bearer is False


def test_lu_depuis_l_environnement() -> None:
    env = {"ENABLE_EXTENSION_OIDC": "true", "EXTENSION_REQUIRE_BEARER": "true"}
    settings = text_api.TextApiSettings.from_env(env)
    assert settings.oidc_enabled is True and settings.require_bearer is True


def test_jeton_exige_sans_verification_des_jetons_fait_echouer_le_demarrage() -> None:
    """Without ENABLE_EXTENSION_OIDC, oauth2-proxy ignores Authorization and
    authenticates by cookie: a cookie plus any forged Authorization header
    would pass the check. Refused at startup."""
    with pytest.raises(text_api.TextApiConfigError, match="ENABLE_EXTENSION_OIDC"):
        text_api.TextApiSettings.from_env({"EXTENSION_REQUIRE_BEARER": "true"})


@pytest.mark.parametrize("name", ["ENABLE_EXTENSION_OIDC", "EXTENSION_REQUIRE_BEARER"])
def test_valeur_invalide_fait_echouer_le_demarrage(name: str) -> None:
    with pytest.raises(text_api.TextApiConfigError):
        text_api.TextApiSettings.from_env({name: "oui"})


# --- requests ------------------------------------------------------------------


@pytest.mark.parametrize("path", [ANALYZE, PSEUDO])
def test_desactive_requete_sans_jeton_servie_comme_avant(audit_dir: Path, extension_dir: Path, path: str) -> None:
    app = _app(_deps(audit_dir, extension_dir, _regex_detector(DETECT)))
    status, _, body, _ = _post(app, path, {"text": "Camille Martin"})
    assert status == 200, body


@pytest.mark.parametrize("path", [ANALYZE, PSEUDO])
def test_jeton_porteur_accepte(audit_dir: Path, extension_dir: Path, path: str) -> None:
    app = _app(_deps(audit_dir, extension_dir, _regex_detector(DETECT)), **STRICT)
    status, _, body, _ = _post(app, path, {"text": "Camille Martin"}, headers=BEARER)
    assert status == 200, body


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": ""},
        {"authorization": "Bearer "},
        {"authorization": "Bearer  "},
        {"authorization": "bearer " + TOKEN_CANARY},
        {"authorization": "Basic Og=="},
        {"authorization": TOKEN_CANARY},
        {"authorization": f"Bearer {TOKEN_CANARY} extra"},
    ],
    ids=["absent", "vide", "bearer-vide", "bearer-blanc", "minuscules", "basic", "sans-schema", "deux-mots"],
)
@pytest.mark.parametrize("path", [ANALYZE, PSEUDO])
def test_sans_jeton_porteur_refuse_401_avant_le_corps(
    audit_dir: Path, extension_dir: Path, caplog: pytest.LogCaptureFixture, headers: dict[str, str], path: str
) -> None:
    detector = _regex_detector(DETECT)
    app = _app(_deps(audit_dir, extension_dir, detector), **STRICT)
    caplog.set_level(logging.DEBUG)
    body = json.dumps({"text": f"Bonjour {CANARY}"}).encode()
    status, resp_headers, answer, consumed = _run(
        _call(app, "POST", path, headers={"content-type": "application/json", **headers}, chunks=[body])
    )
    assert status == 401
    assert answer == {"detail": STRINGS["text_api_bearer_required"], "request_id": resp_headers["x-request-id"]}
    assert consumed == 0
    assert detector.calls == []
    (line,) = _audit_lines(audit_dir)
    assert line["outcome"] == "bearer_required" and line["text_chars"] is None
    assert CANARY not in json.dumps(line) and CANARY not in caplog.text


def test_plusieurs_en_tetes_authorization_refuses(audit_dir: Path, extension_dir: Path) -> None:
    """oauth2-proxy reads the first header only: two headers are refused
    rather than guessing which one it verified."""
    app = _app(_deps(audit_dir, extension_dir, _regex_detector(DETECT)), **STRICT)
    raw = json.dumps({"text": "x"}).encode()

    async def call() -> int:
        sent: list[MutableMapping[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        headers = [
            (b"x-auth-request-user", b"id-fictif-0001"),
            (b"content-type", b"application/json"),
            (b"origin", base.EXT_ORIGIN.encode()),
            (b"authorization", f"Bearer {TOKEN_CANARY}".encode()),
            (b"authorization", b"Bearer autre"),
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": ANALYZE,
            "raw_path": ANALYZE.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
        }
        await app(scope, receive, send)
        return int(next(m for m in sent if m["type"] == "http.response.start")["status"])

    assert cast(Callable[[Any], int], base._run)(call()) == 401


def test_identite_verifiee_avant_le_jeton(audit_dir: Path, extension_dir: Path) -> None:
    app = _app(_deps(audit_dir, extension_dir, _regex_detector(DETECT)), **STRICT)
    status, _, body, _ = _post(app, ANALYZE, {"text": "x"}, identity=False)
    assert status == 403 and body["detail"] == STRINGS["text_api_identity_missing"]


def test_regle_d_origine_maintenue_avec_un_jeton(audit_dir: Path, extension_dir: Path) -> None:
    """D-057: the Origin rule is kept for token requests (defence in depth)."""
    app = _app(_deps(audit_dir, extension_dir, _regex_detector(DETECT)), **STRICT)
    status, _, body, _ = _post(app, ANALYZE, {"text": "x"}, headers=BEARER, origin=False)
    assert status == 403 and body["detail"] == STRINGS["text_api_origin_refused"]


def test_version_sans_jeton_refusee_401_avec_jeton_servie(
    audit_dir: Path, extension_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(text_api._TextApi, "_fetch_recognizers_fingerprint", lambda self: None)
    app = _app(_deps(audit_dir, extension_dir, _regex_detector({})), **STRICT)
    status, _, body, _ = _run(_call(app, "GET", VERSION))
    assert status == 401 and body["detail"] == STRINGS["text_api_bearer_required"]
    status, _, body, _ = _run(_call(app, "GET", VERSION, headers=BEARER))
    assert status == 200, body


def test_jeton_jamais_journalise_ni_audite(
    audit_dir: Path, extension_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = _app(_deps(audit_dir, extension_dir, _regex_detector(DETECT)), **STRICT)
    caplog.set_level(logging.DEBUG)
    for headers in (BEARER, {"authorization": f"Basic {TOKEN_CANARY}"}):
        _post(app, PSEUDO, {"text": "Camille Martin"}, headers=headers)
    audit = (audit_dir / "audit-extension.log").read_text(encoding="utf-8")
    for fragment in TOKEN_CANARY.split("."):
        assert fragment not in audit and fragment not in caplog.text


def test_messages_traduits() -> None:
    for lang in ("fr", "en"):
        strings = json.loads((Path(main.__file__).parent / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        assert strings["text_api_bearer_required"].strip()
