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
Proxy header trust of the app server (phase 2 ter, D-043 point 4).

The app never uses the client address, and only Traefik may speak for the
client (CLAUDE.md §1, EXT-48). uvicorn trusts X-Forwarded-For/-Proto by
default from its FORWARDED_ALLOW_IPS (127.0.0.1, plus ::1 since 0.53.0), and
the handling of these headers changed across releases (duplicates ignored in
0.48.0, consumed in 0.49.0). The production command line therefore disables
them (`--no-proxy-headers`): no forwarding header changes the client address
the app sees, whatever its source and whatever the uvicorn defaults.

The command line is read from app/Dockerfile (the repository is mounted
read-only by app/run-tests.sh, OBFUSK8_REPO) and interpreted by the uvicorn
INSTALLED IN THE IMAGE (its own click parser and Config), so a future default
change is caught here. If the real client address ever becomes needed, the
only acceptable setting is forwarded_allow_ips limited to Traefik's fixed
address (10.89.18.10), never a wider range.
"""

import json
import os
import re
from pathlib import Path
from typing import Any

import uvloop
from uvicorn.config import Config
from uvicorn.main import main as uvicorn_command

# Fixed address of Traefik on app-internal (docker-compose.yml), loopback
# addresses trusted by uvicorn's default FORWARDED_ALLOW_IPS, and a forged
# client address (TEST-NET-3, RFC 5737).
_TRAEFIK = "10.89.18.10"
_PEERS = (_TRAEFIK, "127.0.0.1", "::1", "10.89.18.132")
_FORGED = "203.0.113.66"


def _production_command() -> list[str]:
    root = Path(os.environ.get("OBFUSK8_REPO", ""))
    dockerfile = root / "app" / "Dockerfile"
    assert dockerfile.is_file(), "OBFUSK8_REPO must point to the repository (see app/run-tests.sh)"
    match = re.search(r"^CMD (\[.*\])\s*$", dockerfile.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, "no exec-form CMD in app/Dockerfile"
    command: list[str] = json.loads(match.group(1))
    assert command[0] == "uvicorn"
    return command


def _production_config(app: Any) -> Config:
    """uvicorn Config built from the production command line, with the
    parameters uvicorn's own CLI parser derives from it."""
    command = _production_command()
    params = uvicorn_command.make_context("uvicorn", command[1:]).params
    config = Config(
        app,
        proxy_headers=params["proxy_headers"],
        forwarded_allow_ips=params["forwarded_allow_ips"],
        server_header=params["server_header"],
    )
    config.load()
    return config


def _client_seen(peer: str, headers: list[tuple[bytes, bytes]]) -> tuple[Any, str]:
    seen: dict[str, Any] = {}

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen["client"] = scope["client"]
        seen["scheme"] = scope["scheme"]

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        return None

    config = _production_config(app)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": (peer, 40000),
        "server": ("172.20.0.5", 8000),
    }
    loop = uvloop.new_event_loop()
    try:
        loop.run_until_complete(config.loaded_app(scope, receive, send))
    finally:
        loop.close()
    return seen["client"], seen["scheme"]


def test_commande_de_production_desactive_les_entetes_de_proxy() -> None:
    assert "--no-proxy-headers" in _production_command()
    assert _production_config(lambda *_: None).proxy_headers is False


def test_x_forwarded_for_ignore_quelle_que_soit_la_source() -> None:
    for peer in _PEERS:
        client, scheme = _client_seen(
            peer,
            [
                (b"host", b"obfusk8.lab.local"),
                (b"x-forwarded-for", _FORGED.encode()),
                (b"x-forwarded-proto", b"https"),
            ],
        )
        assert client == (peer, 40000), peer
        assert scheme == "http", peer


def test_entetes_de_transfert_dupliques_ignores() -> None:
    # uvicorn 0.48.0 ignored duplicated forwarding headers, 0.49.0 consumes
    # them: neither behavior may reach the app.
    for peer in _PEERS:
        client, _ = _client_seen(
            peer,
            [
                (b"x-forwarded-for", _FORGED.encode()),
                (b"x-forwarded-for", b"198.51.100.7"),
                (b"forwarded", f"for={_FORGED}".encode()),
            ],
        )
        assert client == (peer, 40000), peer
