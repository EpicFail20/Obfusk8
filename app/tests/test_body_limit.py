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
EXT-22: _RequestBodyLimitMiddleware answers 413 whatever the inner layers do
with the exception it raises once the cap is exceeded. The end-to-end case
(branding middleware turning it into a 400) is the existing, unmodified
test_upload_chunke_sans_content_length_interrompu_au_plafond; these tests
drive the middleware alone around inner apps covering the other paths.
"""

import json

import pytest
import uvloop

import main

LIMIT = 10


def _run(inner, chunks):
    sent: list[dict] = []
    consumed = {"n": 0}

    async def receive():
        i = consumed["n"]
        consumed["n"] += 1
        if i < len(chunks):
            return {"type": "http.request", "body": chunks[i], "more_body": i < len(chunks) - 1}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/x", "headers": [(b"accept", b"application/json")]}
    loop = uvloop.new_event_loop()
    try:
        loop.run_until_complete(main._RequestBodyLimitMiddleware(inner)(scope, receive, send))
    finally:
        loop.close()
    return sent, consumed["n"]


async def _read_all(receive):
    while True:
        message = await receive()
        if not message.get("more_body"):
            return


def _status(sent):
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.fixture(autouse=True)
def _small_cap(monkeypatch):
    monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", LIMIT)


def test_exception_propagee_telle_quelle_donne_413():
    async def inner(scope, receive, send):
        await _read_all(receive)

    sent, consumed = _run(inner, [b"x" * 8, b"x" * 8, b"x" * 8])
    assert _status(sent) == 413
    assert consumed == 2, "reading stops at the chunk that exceeds the cap"
    body = json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))
    assert body["detail"] == main.RequestBodyTooLarge().detail


def test_reponse_interieure_remplacee_par_413():
    """An inner layer that turns the exception into its own response (the
    400 of EXT-22): only the 413 reaches the client, once."""

    async def inner(scope, receive, send):
        try:
            await _read_all(receive)
        except main.RequestBodyTooLarge:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"parsing error"})

    sent, _ = _run(inner, [b"x" * 8, b"x" * 8])
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]
    assert b"parsing error" not in b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def test_sous_le_plafond_reponse_inchangee():
    async def inner(scope, receive, send):
        await _read_all(receive)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent, _ = _run(inner, [b"x" * 4, b"x" * 4])
    assert _status(sent) == 200
    assert sent[-1]["body"] == b"ok"


def test_autre_erreur_sans_depassement_propagee():
    async def inner(scope, receive, send):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _run(inner, [b"x"])


def test_depassement_apres_debut_de_reponse_non_masque():
    """A response already started cannot be replaced: the error propagates
    (the server closes the connection) rather than sending two responses."""

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await _read_all(receive)

    with pytest.raises(main.RequestBodyTooLarge):
        _run(inner, [b"x" * 8, b"x" * 8])
