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
Origin allow-list of the extension on /api/v1/ (phase 3, D-054 point 6).

With the session cookie (option 3 of D-010), any other extension holding a
host permission on the server could call the API with the user's cookie.
Chrome sends `Origin: chrome-extension://<id>` on a POST from an extension
page and no Origin on a GET (observed on 2026-10-07): POST requires an
allowed origin, GET /version accepts an absent one, any other origin is
refused (403, audit outcome `origin_refused`), before the body is read.
"""

import json
import logging
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any, cast

import pytest
import requests
from fastapi import FastAPI

import main
import text_api
from tests import test_text_api as base

# Helpers and fixtures of the text API tests (untyped module): typed views.
Response = tuple[int, dict[str, str], Any, int]
ANALYZE: str = base.ANALYZE
PSEUDO: str = base.PSEUDO
CANARY: str = base.CANARY
DETECT: dict[str, str] = base.DETECT
EXT_ORIGIN: str = base.EXT_ORIGIN
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

OTHER_EXTENSION = "chrome-extension://ponmlkjihgfedcbaponmlkjihgfedcba"
VERSION = "/api/v1/version"


@pytest.fixture
def no_analyzer(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_get(*args: object, **kwargs: object) -> None:
        raise AssertionError("no analyzer call expected")

    monkeypatch.setattr(requests, "get", fail_get)


# --- configuration -------------------------------------------------------------


def test_liste_vide_par_defaut() -> None:
    assert text_api.TextApiSettings().allowed_origins == frozenset()
    assert text_api.TextApiSettings.from_env({}).allowed_origins == frozenset()
    assert text_api.TextApiSettings.from_env({"EXTENSION_ALLOWED_ORIGINS": "  "}).allowed_origins == frozenset()


def test_liste_lue_depuis_l_environnement() -> None:
    env = {"EXTENSION_ALLOWED_ORIGINS": f" {EXT_ORIGIN} , {OTHER_EXTENSION}"}
    assert text_api.TextApiSettings.from_env(env).allowed_origins == frozenset({EXT_ORIGIN, OTHER_EXTENSION})


@pytest.mark.parametrize(
    "value",
    [
        "https://obfusk8.lab.local",  # the web interface never calls the API
        "chrome-extension://abcdefghijklmnop",  # too short
        "chrome-extension://abcdefghijklmnopabcdefghijklmnoq",  # q is not an extension id letter (a-p)
        "chrome-extension://ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP",  # browsers send lowercase
        f"{EXT_ORIGIN}/",
        "moz-extension://0b0f7a9e-0000-4000-8000-000000000000",  # Firefox: later, with option 2 (D-054 point 1)
        "*",
        "null",
        f"{EXT_ORIGIN},,{OTHER_EXTENSION}",  # empty entry
    ],
)
def test_entree_invalide_fait_echouer_le_demarrage(value: str) -> None:
    with pytest.raises(text_api.TextApiConfigError):
        text_api.TextApiSettings.from_env({"EXTENSION_ALLOWED_ORIGINS": value})


# --- POST ----------------------------------------------------------------------


@pytest.mark.parametrize("path", [ANALYZE, PSEUDO])
def test_post_origine_autorisee_acceptee(audit_dir: Path, extension_dir: Path, path: str) -> None:
    app = _app(_deps(audit_dir, extension_dir, _regex_detector(DETECT)))
    status, _, body, _ = _post(app, path, {"text": "Camille Martin"})
    assert status == 200, body


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"origin": OTHER_EXTENSION},
        {"origin": "https://obfusk8.lab.local"},
        {"origin": "null"},
        {"origin": EXT_ORIGIN.upper()},
        {"origin": f"{EXT_ORIGIN} "},
    ],
    ids=["absente", "autre-extension", "interface-web", "null", "majuscules", "espace"],
)
@pytest.mark.parametrize("path", [ANALYZE, PSEUDO])
def test_post_origine_absente_ou_non_autorisee_refusee_403(
    audit_dir: Path, extension_dir: Path, caplog: pytest.LogCaptureFixture, headers: dict[str, str], path: str
) -> None:
    detector = _regex_detector(DETECT)
    app = _app(_deps(audit_dir, extension_dir, detector))
    caplog.set_level(logging.DEBUG)
    body = json.dumps({"text": f"Bonjour {CANARY}"}).encode()
    status, resp_headers, answer, consumed = _run(
        _call(app, "POST", path, headers={"content-type": "application/json", **headers}, chunks=[body], origin=False)
    )
    assert status == 403
    assert answer == {"detail": STRINGS["text_api_origin_refused"], "request_id": resp_headers["x-request-id"]}
    assert consumed == 0  # refused before reading the body
    assert detector.calls == []
    (line,) = _audit_lines(audit_dir)
    assert line["outcome"] == "origin_refused" and line["text_chars"] is None
    assert CANARY not in json.dumps(line) and CANARY not in caplog.text


def test_post_plusieurs_en_tetes_origin_refuse(audit_dir: Path, extension_dir: Path) -> None:
    app = _app(_deps(audit_dir, extension_dir, _regex_detector(DETECT)))
    raw = json.dumps({"text": "x"}).encode()

    async def call() -> int:
        sent: list[MutableMapping[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        headers = [
            (b"x-auth-request-user", b"id-fictif-0001"),
            (b"x-auth-request-email", b"utilisateur.fictif@exemple.invalid"),
            (b"content-type", b"application/json"),
            (b"origin", OTHER_EXTENSION.encode()),
            (b"origin", EXT_ORIGIN.encode()),
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

    assert cast(Callable[[Any], int], base._run)(call()) == 403


def test_liste_vide_refuse_tout_post(audit_dir: Path, extension_dir: Path) -> None:
    app = _app(_deps(audit_dir, extension_dir, _regex_detector(DETECT)), allowed_origins=frozenset())
    status, _, body, _ = _post(app, ANALYZE, {"text": "Camille Martin"})
    assert status == 403 and body["detail"] == STRINGS["text_api_origin_refused"]


def test_identite_verifiee_avant_l_origine(audit_dir: Path, extension_dir: Path) -> None:
    app = _app(_deps(audit_dir, extension_dir, _regex_detector(DETECT)))
    status, _, body, _ = _post(app, ANALYZE, {"text": "x"}, identity=False, origin=False)
    assert status == 403 and body["detail"] == STRINGS["text_api_identity_missing"]
    (line,) = _audit_lines(audit_dir)
    assert line["outcome"] == "forbidden"


# --- GET /version ----------------------------------------------------------------


def test_version_sans_origine_acceptee(audit_dir: Path, extension_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_api._TextApi, "_fetch_recognizers_fingerprint", lambda self: None)
    app = _app(_deps(audit_dir, extension_dir, _regex_detector({})))
    status, _, body, _ = _run(_call(app, "GET", VERSION, origin=False))
    assert status == 200 and body["api_version"] == "1.0"
    status, _, body, _ = _run(_call(app, "GET", VERSION))
    assert status == 200


@pytest.mark.parametrize("origin", [OTHER_EXTENSION, "https://obfusk8.lab.local", "null"])
def test_version_origine_non_autorisee_refusee_403(
    audit_dir: Path, extension_dir: Path, no_analyzer: None, origin: str
) -> None:
    app = _app(_deps(audit_dir, extension_dir, _regex_detector({})))
    status, headers, body, _ = _run(_call(app, "GET", VERSION, headers={"origin": origin}, origin=False))
    assert status == 403
    assert body == {"detail": STRINGS["text_api_origin_refused"], "request_id": headers["x-request-id"]}


def test_message_origine_traduit() -> None:
    for lang in ("fr", "en"):
        strings = json.loads((Path(main.__file__).parent / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        assert strings["text_api_origin_refused"].strip()
