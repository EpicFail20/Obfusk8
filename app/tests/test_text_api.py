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
Tests of the text API (text_api.py, docs/api-extension.en.md). No external
service: Presidio is replaced by a fake `detect_blocks`, or by a fake
`main._analyze_text` for the tests going through main.py's real detection.

Requests are played through the full ASGI stack on a uvloop event loop
(the stdlib asyncio selector is blocked by the enforcing seccomp profile,
EXT-11). All text is synthetic.
"""

import json
import logging
import re
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import anyio
import pytest
import uvloop
from fastapi import FastAPI, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main
import text_api
from supervision import Alert

# A marker that must NEVER appear in a log, the audit log or an error body.
CANARY = "Zébulon Canari-Témoin"
USER = "utilisateur.fictif@exemple.invalid"
STRINGS = main.STRINGS


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _run(coro):
    loop = uvloop.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _call(app, method, path, body=b"", headers=None, chunks=None):
    """One request through the ASGI app. Returns (status, headers, json|bytes, chunks consumed)."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    parts = chunks if chunks is not None else [body]
    consumed = {"n": 0}

    async def receive():
        i = consumed["n"]
        if i < len(parts):
            consumed["n"] += 1
            return {"type": "http.request", "body": parts[i], "more_body": i < len(parts) - 1}
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    resp_headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    try:
        decoded = json.loads(payload)
    except ValueError:
        decoded = payload
    return start["status"], resp_headers, decoded, consumed["n"]


def _post(app, path, payload=None, raw=None, content_type="application/json", **kw):
    body = raw if raw is not None else json.dumps(payload).encode()
    headers = {"content-type": content_type, "content-length": str(len(body))} if content_type else {}
    headers.update(kw.pop("headers", {}))
    return _run(_call(app, "POST", path, body, headers, **kw))


def _regex_detector(patterns):
    """Fake detect_blocks: regex -> entity type, on block 0, records its calls."""
    calls = []

    def detect(blocks, theme, timeout):
        calls.append({"blocks": blocks, "theme": theme, "timeout": timeout})
        ((block_id, text),) = blocks
        found = []
        for entity_type, pattern in patterns.items():
            for m in re.finditer(pattern, text):
                found.append({"block_id": block_id, "start": m.start(), "end": m.end(), "entity_type": entity_type})
        return found

    detect.calls = calls
    return detect


def _audit_file_handlers():
    logger = logging.getLogger(text_api.AUDIT_LOGGER_NAME)
    return [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]


@pytest.fixture
def audit_dir(tmp_path):
    yield tmp_path
    logger = logging.getLogger(text_api.AUDIT_LOGGER_NAME)
    for h in _audit_file_handlers():
        h.close()
        logger.removeHandler(h)


@pytest.fixture
def extension_dir(tmp_path):
    d = tmp_path / "extension"
    d.mkdir()
    (d / "secrets.json").write_text(
        json.dumps({"ad_hoc_recognizers": [{"name": "FakeSecret", "supported_entity": "SECRET"}]})
    )
    return d


def _deps(audit_dir, extension_dir, detect, alerts=None, **overrides):
    values = dict(
        detect_blocks=detect,
        themes={"medical": {"score_threshold": 0.4, "ad_hoc_recognizers": [{"name": "ThemeRec"}]}},
        theme_labels={"medical": "Médical"},
        common_recognizers=[{"name": "Common"}],
        strings=STRINGS,
        send_alert=(alerts.append if alerts is not None else (lambda alert: None)),
        request_user=lambda request: USER,
        audit_dir=audit_dir,
        extension_dir=extension_dir,
        analyzer_url="http://presidio-analyzer.invalid:3000",
        analyzer_language="fr",
        default_score_threshold=0.4,
    )
    values.update(overrides)
    return text_api.TextApiDeps(**values)


def _app(deps, **settings):
    app = FastAPI()
    app.include_router(text_api.create_router(text_api.TextApiSettings(enabled=True, **settings), deps))
    return app


DETECT = {
    "PERSON": r"Camille Martin|Zébulon Canari-Témoin",
    "PHONE_NUMBER": r"06 12 34 56 78",
    "EMAIL_ADDRESS": r"\S+@exemple\.invalid",
}
ANALYZE = "/api/v1/text/analyze"
PSEUDO = "/api/v1/text/pseudonymize"


def _audit_lines(audit_dir):
    path = audit_dir / "audit-extension.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# ---------------------------------------------------------------------------
# Settings and configuration loading
# ---------------------------------------------------------------------------


def test_reglages_par_defaut_desactives():
    s = text_api.TextApiSettings.from_env({})
    assert (s.enabled, s.max_text_chars, s.max_analysis_seconds, s.max_concurrency, s.max_queue) == (
        False,
        20000,
        10,
        1,
        8,
    )
    # 12 bytes per code point + 4096 of envelope: must equal the Traefik label.
    assert s.max_body_bytes == 244096


def test_reglages_lus_depuis_l_environnement():
    s = text_api.TextApiSettings.from_env(
        {
            "ENABLE_EXTENSION_API": "TRUE",
            "MAX_TEXT_CHARS": "100",
            "MAX_TEXT_ANALYSIS_SECONDS": "3",
            "MAX_TEXT_CONCURRENCY": "2",
            "MAX_TEXT_QUEUE": "4",
        }
    )
    assert (s.enabled, s.max_text_chars, s.max_analysis_seconds, s.max_concurrency, s.max_queue) == (True, 100, 3, 2, 4)
    assert text_api.TextApiSettings.from_env({"ENABLE_EXTENSION_API": " "}).enabled is False


@pytest.mark.parametrize(
    "env",
    [{"ENABLE_EXTENSION_API": "yes"}, {"MAX_TEXT_CHARS": "abc"}, {"MAX_TEXT_CHARS": "0"}, {"MAX_TEXT_QUEUE": "-1"}],
)
def test_reglage_invalide_echoue_au_demarrage(env):
    with pytest.raises(text_api.TextApiConfigError):
        text_api.TextApiSettings.from_env(env)


THEMES_FOR_INCLUDE = {
    "medical": {"ad_hoc_recognizers": [{"name": "FrenchNirRecognizer", "supported_entity": "FR_NIR"}]}
}


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"ad_hoc_recognizers": "x"}),
        json.dumps([1, 2]),
        json.dumps({"ad_hoc_recognizers": [], "include_theme_recognizers": ["medical"]}),
        json.dumps({"ad_hoc_recognizers": [], "include_theme_recognizers": {"medical": "FrenchNirRecognizer"}}),
        json.dumps({"ad_hoc_recognizers": [], "include_theme_recognizers": {"medical": ["Inexistant"]}}),
        json.dumps({"ad_hoc_recognizers": [], "include_theme_recognizers": {"inconnu": ["FrenchNirRecognizer"]}}),
    ],
)
def test_fichier_de_reconnaisseurs_illisible_ou_invalide_echoue(tmp_path, content):
    (tmp_path / "a.json").write_text(content)
    with pytest.raises(text_api.TextApiConfigError):
        text_api.load_extension_recognizers(tmp_path, THEMES_FOR_INCLUDE)


def test_reconnaisseur_de_theme_inclus_par_reference_et_copie(tmp_path):
    (tmp_path / "a.json").write_text(
        json.dumps(
            {"include_theme_recognizers": {"medical": ["FrenchNirRecognizer"]}, "ad_hoc_recognizers": [{"name": "X"}]}
        )
    )
    loaded = text_api.load_extension_recognizers(tmp_path, THEMES_FOR_INCLUDE)
    assert [r["name"] for r in loaded] == ["FrenchNirRecognizer", "X"]
    loaded[0]["name"] = "modifié"
    assert THEMES_FOR_INCLUDE["medical"]["ad_hoc_recognizers"][0]["name"] == "FrenchNirRecognizer"


def test_fichiers_reels_de_l_extension_charges_avec_les_vrais_themes():
    """The shipped app/themes/extension/ files load against the real themes
    (a renamed theme recognizer would fail here, not in production)."""
    loaded = text_api.load_extension_recognizers(main.THEMES_DIR / "extension", main.THEMES)
    entities = {r["supported_entity"] for r in loaded}
    assert {"SECRET", "CREDIT_CARD", "FR_NIR", "EMAIL_ADDRESS"} <= entities


def test_empreinte_de_configuration_stable_et_sensible(audit_dir, extension_dir):
    deps = _deps(audit_dir, extension_dir, _regex_detector({}))
    a = text_api.detection_config_fingerprint(deps, [{"name": "x"}])
    assert a == text_api.detection_config_fingerprint(deps, [{"name": "x"}])
    assert a != text_api.detection_config_fingerprint(deps, [{"name": "y"}])
    assert re.fullmatch(r"[0-9a-f]{16}", a)


# ---------------------------------------------------------------------------
# Pure text processing
# ---------------------------------------------------------------------------


def test_positions_utf16_avec_caracteres_hors_plan_de_base():
    text = "😀a𝄞b"
    assert text_api.utf16_prefix(text) == [0, 2, 3, 5, 6]


def test_propagation_valeurs_exactes_tous_types_avec_frontieres_de_mot():
    text = "Paul a écrit à paul.x@exemple.invalid ; Pauline aussi. Paul, encore. Contact : paul.x@exemple.invalid"
    spans = [text_api.Span(0, 4, "PERSON"), text_api.Span(15, 37, "EMAIL_ADDRESS")]
    result = text_api.propagate_exact_values(text, spans)
    values = [(text[s.start : s.end], s.entity_type) for s in result]
    assert values.count(("Paul", "PERSON")) == 2  # "Pauline" not touched
    assert values.count(("paul.x@exemple.invalid", "EMAIL_ADDRESS")) == 2


def test_propagation_ignore_les_valeurs_trop_courtes():
    text = "AB et AB"
    assert text_api.propagate_exact_values(text, [text_api.Span(0, 2, "X")]) == [text_api.Span(0, 2, "X")]


def test_pseudonymisation_meme_valeur_meme_marqueur():
    text = "Camille Martin appelle Camille Martin au 06 12 34 56 78."
    spans = [text_api.Span(0, 14, "PERSON"), text_api.Span(23, 37, "PERSON"), text_api.Span(41, 55, "PHONE_NUMBER")]
    out, mapping = text_api.pseudonymize(text, spans)
    assert out == "⟦PERSON_1⟧ appelle ⟦PERSON_1⟧ au ⟦PHONE_NUMBER_1⟧."
    assert [(m.placeholder, m.original) for m in mapping] == [
        ("⟦PERSON_1⟧", "Camille Martin"),
        ("⟦PHONE_NUMBER_1⟧", "06 12 34 56 78"),
    ]


def test_pseudonymisation_sans_collision_avec_un_marqueur_present_dans_le_texte():
    text = "Voir ⟦PERSON_1⟧ et ⟦PERSON_2⟧ : Camille Martin"
    out, mapping = text_api.pseudonymize(text, [text_api.Span(31, 45, "PERSON")])
    assert mapping[0].placeholder == "⟦PERSON_3⟧"
    assert out.count("⟦PERSON_3⟧") == 1
    # Restoration is exact: replacing each placeholder gives back the input.
    restored = out
    for entry in mapping:
        restored = restored.replace(entry.placeholder, entry.original)
    assert restored == text


def test_chevauchements_fusionnes_type_le_plus_long_puis_secret():
    text = "token=abcdef123456"
    spans = [text_api.Span(0, 18, "API_KEY"), text_api.Span(6, 18, "SECRET"), text_api.Span(0, 18, "SECRET")]
    out, mapping = text_api.pseudonymize(text, spans)
    assert out == "⟦SECRET_1⟧" and mapping[0].entity_type == "SECRET"
    # Longest wins over SECRET when longer.
    out, mapping = text_api.pseudonymize("abcdef", [text_api.Span(0, 6, "PERSON"), text_api.Span(1, 3, "SECRET")])
    assert out == "⟦PERSON_1⟧"


def test_libelle_de_marqueur_assaini():
    _, mapping = text_api.pseudonymize("xyz", [text_api.Span(0, 3, "fr-nir ⟧")])
    assert mapping[0].placeholder == "⟦FR_NIR___1⟧"
    _, mapping = text_api.pseudonymize("xyz", [text_api.Span(0, 3, "")])
    assert mapping[0].placeholder == "⟦ENTITY_1⟧"


# ---------------------------------------------------------------------------
# Routes — nominal
# ---------------------------------------------------------------------------


def test_analyse_positions_sur_la_chaine_recue_et_en_utf16(audit_dir, extension_dir):
    detect = _regex_detector(DETECT)
    app = _app(_deps(audit_dir, extension_dir, detect))
    text = "😀 Bonjour, je suis Camille Martin, joignable au 06 12 34 56 78."
    status, headers, body, _ = _post(app, ANALYZE, {"text": text})
    assert status == 200, body
    assert re.fullmatch(r"[0-9a-f]{32}", body["request_id"]) and headers["x-request-id"] == body["request_id"]
    assert body["text_length"] == len(text) and body["text_length_utf16"] == len(text) + 1
    person = body["entities"][0]
    assert text[person["start"] : person["end"]] == "Camille Martin"
    assert person["start_utf16"] == person["start"] + 1  # one surrogate pair before
    assert all("score" not in e for e in body["entities"])
    assert [e["entity_type"] for e in body["entities"]] == ["PERSON", "PHONE_NUMBER"]
    # Theme-less: extension recognizers appended, no threshold forced by the API.
    theme = detect.calls[0]["theme"]
    assert theme == {"ad_hoc_recognizers": [{"name": "FakeSecret", "supported_entity": "SECRET"}]}
    assert detect.calls[0]["timeout"] <= 10


def test_analyse_avec_theme_ajoute_les_reconnaisseurs_propres(audit_dir, extension_dir):
    detect = _regex_detector(DETECT)
    app = _app(_deps(audit_dir, extension_dir, detect))
    status, _, body, _ = _post(app, ANALYZE, {"text": "Camille Martin", "theme": "medical"})
    assert status == 200 and body["theme"] == "medical"
    theme = detect.calls[0]["theme"]
    assert theme["score_threshold"] == 0.4
    assert [r["name"] for r in theme["ad_hoc_recognizers"]] == ["ThemeRec", "FakeSecret"]


def test_pseudonymisation_et_journal_d_audit_sans_contenu(audit_dir, extension_dir, caplog):
    app = _app(_deps(audit_dir, extension_dir, _regex_detector(DETECT)))
    text = f"{CANARY} écrit à z.canari@exemple.invalid ; {CANARY} rappelle."
    with caplog.at_level(logging.DEBUG):
        status, headers, body, _ = _post(app, PSEUDO, {"text": text})
    assert status == 200, body
    assert body["text"] == "⟦PERSON_1⟧ écrit à ⟦EMAIL_ADDRESS_1⟧ ; ⟦PERSON_1⟧ rappelle."
    assert {m["original"] for m in body["mapping"]} == {CANARY, "z.canari@exemple.invalid"}
    (event,) = _audit_lines(audit_dir)
    assert event["event"] == "text_pseudonymize" and event["outcome"] == "ok" and event["user"] == USER
    assert event["request_id"] == headers["x-request-id"]
    assert event["entities_found"] == {"EMAIL_ADDRESS": 1, "PERSON": 2} and event["total_entities"] == 3
    assert event["text_chars"] == len(text) and event["theme"] == "aucun"
    raw = (audit_dir / "audit-extension.log").read_text(encoding="utf-8")
    for secret in (CANARY, "Canari", "z.canari", "⟦"):
        assert secret not in raw
        assert secret not in caplog.text


def test_version(audit_dir, extension_dir, monkeypatch):
    calls = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return ["SpacyRecognizer", "EmailRecognizer"]

    def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        return _Resp()

    monkeypatch.setattr(text_api.requests, "get", fake_get)
    app = _app(_deps(audit_dir, extension_dir, _regex_detector({})))
    status, headers, body, _ = _run(_call(app, "GET", "/api/v1/version"))
    assert status == 200 and "x-request-id" in headers
    assert body["api_version"] == "1.0" and body["presidio_version"] is None
    assert body["themes"] == [{"key": "medical", "label": "Médical"}] and body["max_text_chars"] == 20000
    assert re.fullmatch(r"[0-9a-f]{16}", body["detection_config"])
    assert re.fullmatch(r"[0-9a-f]{16}", body["analyzer_recognizers"])
    _run(_call(app, "GET", "/api/v1/version"))
    assert len(calls) == 1  # cached after the first success


def test_version_analyseur_injoignable(audit_dir, extension_dir, monkeypatch):
    def fake_get(*args, **kwargs):
        raise text_api.requests.ConnectionError("down")

    monkeypatch.setattr(text_api.requests, "get", fake_get)
    app = _app(_deps(audit_dir, extension_dir, _regex_detector({})))
    status, _, body, _ = _run(_call(app, "GET", "/api/v1/version"))
    assert status == 200 and body["analyzer_recognizers"] is None


# ---------------------------------------------------------------------------
# Routes — rejected input (never an echo of the text)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,status,outcome,key",
    [
        (
            {"payload": {"text": CANARY}, "content_type": "text/plain"},
            415,
            "invalid",
            "text_api_unsupported_media_type",
        ),
        (
            {"payload": {"text": CANARY}, "content_type": "application/json; charset=latin-1"},
            415,
            "invalid",
            "text_api_unsupported_media_type",
        ),
        ({"payload": {"text": CANARY}, "content_type": None}, 415, "invalid", "text_api_unsupported_media_type"),
        ({"raw": b'{"text": "\xff\xfe"}'}, 400, "invalid", "text_api_invalid_json"),
        ({"raw": b'{"text": "Zebulon'}, 400, "invalid", "text_api_invalid_json"),
        ({"raw": b'{"text": "\\ud800 Zebulon"}'}, 400, "invalid", "text_api_invalid_json"),
        ({"payload": {"text": CANARY, "score_threshold": 0.99}}, 422, "invalid", "text_api_invalid_request"),
        ({"payload": {"text": CANARY, "entities": ["PERSON"]}}, 422, "invalid", "text_api_invalid_request"),
        ({"payload": {"text": ""}}, 422, "invalid", "text_api_invalid_request"),
        ({"payload": {"text": 42}}, 422, "invalid", "text_api_invalid_request"),
        ({"payload": {"theme": "medical"}}, 422, "invalid", "text_api_invalid_request"),
        ({"payload": [CANARY]}, 422, "invalid", "text_api_invalid_request"),
        ({"payload": {"text": CANARY, "theme": "inconnu"}}, 422, "invalid", "text_api_unknown_theme"),
        ({"payload": {"text": CANARY, "theme": "../../etc"}}, 422, "invalid", "text_api_invalid_request"),
    ],
)
def test_entrees_rejetees(audit_dir, extension_dir, caplog, kwargs, status, outcome, key):
    detect = _regex_detector(DETECT)
    app = _app(_deps(audit_dir, extension_dir, detect))
    with caplog.at_level(logging.DEBUG):
        got, headers, body, _ = _post(app, ANALYZE, **kwargs)
    assert got == status, body
    assert body["detail"] == STRINGS[key].format(max_seconds=10, max_chars=20000, max_bytes=244096)
    assert body["request_id"] == headers["x-request-id"]
    assert detect.calls == []  # nothing reached the analyzer
    (event,) = _audit_lines(audit_dir)
    assert event["outcome"] == outcome
    for leak in ("Zebulon", "Zébulon", "etc"):
        assert leak not in json.dumps(body, ensure_ascii=False)
        assert leak not in caplog.text
        assert leak not in (audit_dir / "audit-extension.log").read_text(encoding="utf-8")


def test_corps_declare_trop_grand_rejete_sans_lecture(audit_dir, extension_dir):
    app = _app(_deps(audit_dir, extension_dir, _regex_detector({})), max_text_chars=10)
    cap = text_api.TextApiSettings(max_text_chars=10).max_body_bytes
    status, _, body, consumed = _run(
        _call(app, "POST", ANALYZE, b"x", {"content-type": "application/json", "content-length": str(cap + 1)})
    )
    assert status == 413 and consumed == 0
    assert body["detail"] == STRINGS["text_api_body_too_large"].format(max_bytes=cap)


def test_corps_chunke_trop_grand_interrompu_au_plafond(audit_dir, extension_dir):
    app = _app(_deps(audit_dir, extension_dir, _regex_detector({})), max_text_chars=10)
    chunks = [b"x" * 1024] * 20  # 20 KiB, cap = 12 * 10 + 4096 = 4216 bytes
    status, _, _, consumed = _run(
        _call(app, "POST", ANALYZE, headers={"content-type": "application/json"}, chunks=chunks)
    )
    assert status == 413
    assert consumed <= 5, consumed  # stopped right after the cap, not the whole body


def test_texte_trop_long_413(audit_dir, extension_dir):
    detect = _regex_detector({})
    app = _app(_deps(audit_dir, extension_dir, detect), max_text_chars=10)
    status, _, body, _ = _post(app, ANALYZE, {"text": "a" * 11})
    assert status == 413 and body["detail"] == STRINGS["text_api_text_too_long"].format(max_chars=10)
    assert detect.calls == []
    assert _post(app, ANALYZE, {"text": "😀" * 10})[0] == 200  # code points, not bytes or UTF-16 units


# ---------------------------------------------------------------------------
# Routes — analyzer failures, unexpected errors, admission control
# ---------------------------------------------------------------------------


def test_analyseur_indisponible_503_et_alerte_limitee(audit_dir, extension_dir):
    def detect(blocks, theme, timeout):
        raise HTTPException(status_code=502, detail="Moteur d'analyse indisponible")

    alerts = []
    app = _app(_deps(audit_dir, extension_dir, detect, alerts=alerts))
    for _ in range(3):
        status, headers, body, _ = _post(app, ANALYZE, {"text": CANARY})
        assert status == 503 and headers["retry-after"] == "5"
        assert body["detail"] == STRINGS["text_api_analyzer_unavailable"]
    assert len(alerts) == 1 and isinstance(alerts[0], Alert) and alerts[0].source == "presidio"
    assert CANARY not in json.dumps(alerts[0].details)
    assert [e["outcome"] for e in _audit_lines(audit_dir)] == ["analyzer_unavailable"] * 3


@pytest.mark.parametrize("exc", [HTTPException(status_code=400, detail=CANARY), RuntimeError(f"boom {CANARY}")])
def test_erreur_inattendue_500_generique_sans_contenu(audit_dir, extension_dir, caplog, exc):
    def detect(blocks, theme, timeout):
        raise exc

    app = _app(_deps(audit_dir, extension_dir, detect))
    with caplog.at_level(logging.DEBUG):
        status, headers, body, _ = _post(app, PSEUDO, {"text": CANARY})
    assert status == 500 and body["detail"] == STRINGS["text_api_internal_error"]
    assert headers["x-request-id"] in caplog.text  # traceable in the logs
    assert CANARY not in caplog.text and CANARY not in json.dumps(body, ensure_ascii=False)
    assert _audit_lines(audit_dir)[0]["outcome"] == "error"


def _blocking_detector(release: threading.Event, started: threading.Event):
    def detect(blocks, theme, timeout):
        started.set()
        release.wait(5)
        return []

    return detect


def test_file_pleine_429_puis_reprise(audit_dir, extension_dir):
    release, started = threading.Event(), threading.Event()
    app = _app(_deps(audit_dir, extension_dir, _blocking_detector(release, started)), max_concurrency=1, max_queue=1)
    body = json.dumps({"text": "abc"}).encode()
    headers = {"content-type": "application/json"}
    results = []

    async def scenario():
        async def one():
            results.append(await _call(app, "POST", ANALYZE, body, headers))

        async with anyio.create_task_group() as tg:
            tg.start_soon(one)  # in progress
            await anyio.to_thread.run_sync(started.wait, 5)
            tg.start_soon(one)  # waiting
            await anyio.sleep(0.05)
            third = await _call(app, "POST", ANALYZE, body, headers)  # refused
            results.append(third)
            release.set()

    _run(scenario())
    statuses = sorted(r[0] for r in results)
    assert statuses == [200, 200, 429]
    refused = next(r for r in results if r[0] == 429)
    assert refused[1]["retry-after"] == "1" and refused[2]["detail"] == STRINGS["text_api_busy"]
    assert sorted(e["outcome"] for e in _audit_lines(audit_dir)) == ["busy", "ok", "ok"]


def test_attente_au_dela_du_delai_503(audit_dir, extension_dir):
    release, started = threading.Event(), threading.Event()
    app = _app(
        _deps(audit_dir, extension_dir, _blocking_detector(release, started)),
        max_concurrency=1,
        max_queue=1,
        max_analysis_seconds=1,
    )
    body = json.dumps({"text": "abc"}).encode()
    headers = {"content-type": "application/json"}
    results = []

    async def scenario():
        async def first():
            results.append(await _call(app, "POST", ANALYZE, body, headers))

        async with anyio.create_task_group() as tg:
            tg.start_soon(first)
            await anyio.to_thread.run_sync(started.wait, 5)
            results.append(await _call(app, "POST", ANALYZE, body, headers))  # waits > 1 s
            release.set()

    _run(scenario())
    timed_out = next(r for r in results if r[0] == 503)
    assert timed_out[2]["detail"] == STRINGS["text_api_timeout"].format(max_seconds=1)


def test_delai_epuise_avant_l_analyse_503(audit_dir, extension_dir, monkeypatch):
    """The budget is spent before the analysis can start (slow queue
    admission): 503, and nothing is sent to the analyzer."""
    detect = _regex_detector({})
    app = _app(_deps(audit_dir, extension_dir, detect), max_analysis_seconds=1)

    class _FakeTime:
        # started, deadline base, then "remaining" computed 5 s later.
        values = iter([0.0, 0.0, 5.0])
        strftime = staticmethod(time.strftime)

        def monotonic(self):
            return next(self.values, 5.0)

    monkeypatch.setattr(text_api, "time", _FakeTime())
    status, _, body, _ = _post(app, ANALYZE, {"text": "abc"})
    assert status == 503 and detect.calls == []
    assert body["detail"] == STRINGS["text_api_timeout"].format(max_seconds=1)


# ---------------------------------------------------------------------------
# Integration with main.py (real detection path, fake Presidio)
# ---------------------------------------------------------------------------


def test_drapeau_desactive_routes_absentes_404_identique():
    """Flag off (default): no /api/v1 route, the 404 is exactly the one any
    unknown path already gets — behavior strictly unchanged."""
    assert main.TEXT_API_SETTINGS.enabled is False
    assert not [r for r in main.app.routes if getattr(r, "path", "").startswith("/api/v1")]
    asgi = main.app
    secret = main.GATEWAY_SECRET
    try:
        main.GATEWAY_SECRET = ""
        for method, path in (("GET", "/api/v1/version"), ("POST", ANALYZE), ("POST", PSEUDO)):
            status, _, body, _ = _run(_call(asgi, method, path, b"{}", {"content-type": "application/json"}))
            ref_status, _, ref_body, _ = _run(
                _call(asgi, method, "/api/nexistepas", b"{}", {"content-type": "application/json"})
            )
            assert (status, body) == (ref_status, ref_body) == (404, {"detail": "Not Found"})
    finally:
        main.GATEWAY_SECRET = secret
    assert not _audit_file_handlers()


def test_chemin_reel_de_detection_de_main(audit_dir, monkeypatch):
    """Through main._build_text_api_router: real _detect_text_blocks
    (normalization, name propagation), real themes, fake Presidio. The
    analyzer receives the normalized text, the API's own timeout, and the
    extension recognizers on top of the theme's."""
    seen = []

    def fake_analyze(text, theme=None, timeout=30):
        seen.append({"text": text, "theme": theme, "timeout": timeout})
        i = text.find("Camille Martin")
        return [{"entity_type": "PERSON", "start": i, "end": i + 14, "score": 0.85}] if i >= 0 else []

    monkeypatch.setattr(main, "_analyze_text", fake_analyze)
    monkeypatch.setattr(main, "AUDIT_DIR", audit_dir)
    app = FastAPI()
    app.include_router(main._build_text_api_router(text_api.TextApiSettings(enabled=True, max_analysis_seconds=7)))
    text = "CAMILLE MARTIN – né le 01–02–1990 ; Camille Martin rappelle."
    status, _, body, _ = _post(app, PSEUDO, {"text": text, "theme": "medical"})
    assert status == 200, body
    assert (
        seen[0]["text"] == "Camille Martin - né le 01-02-1990 ; Camille Martin rappelle."
    )  # length-preserving normalization
    assert 0 < seen[0]["timeout"] <= 7
    names = [r.get("name") for r in seen[0]["theme"]["ad_hoc_recognizers"]]
    assert names[: len(main.THEMES["medical"]["ad_hoc_recognizers"])] == [
        r.get("name") for r in main.THEMES["medical"]["ad_hoc_recognizers"]
    ]
    # Propagation: the detected value appears in the response once per occurrence.
    assert body["text"].count("⟦PERSON_1⟧") == 1 and body["text"].count("⟦PERSON_2⟧") == 1
    assert "Camille Martin" not in body["text"] and "CAMILLE MARTIN" not in body["text"]


def test_flux_documents_appel_a_l_analyseur_inchange(monkeypatch):
    """Document flow: _detect_text_blocks still calls _analyze_text(text,
    theme=...) with NO timeout argument, and _analyze_text keeps its 30 s
    default towards Presidio."""
    with monkeypatch.context() as patch:
        patch.setattr(main, "_analyze_text", lambda text, theme=None: [])
        assert main._detect_text_blocks([(0, "Bonjour Camille Martin")]) == []

    posted = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    def fake_post(url, json, timeout):
        posted["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(main.requests, "post", fake_post)
    main._analyze_text("Bonjour Camille Martin")
    assert posted["timeout"] == 30


def test_modele_rejette_une_surrogate_isolee_hors_json():
    """Pydantic's JSON parser refuses a lone surrogate (400); the string
    constraints refuse it on any other construction path."""
    from pydantic import ValidationError

    from text_api_models import TextRequest

    with pytest.raises(ValidationError) as caught:
        TextRequest.model_validate({"text": "a\ud800b"})
    assert caught.value.errors(include_input=False)[0]["type"] == "string_unicode"


def test_budget_epuise_pendant_l_appel_classe_timeout_sans_alerte(audit_dir, extension_dir):
    """Regression (observed end to end, 14 parallel 20k-char requests): the
    last one in the queue spent its budget inside the call to Presidio; the
    requests timeout became main's 502 and was reported as
    analyzer_unavailable, with a false supervision alert. It is a timeout."""

    def detect(blocks, theme, timeout):
        time.sleep(timeout)  # the HTTP call consumes the whole remaining budget
        raise HTTPException(status_code=502, detail="Moteur d'analyse indisponible")

    alerts = []
    app = _app(_deps(audit_dir, extension_dir, detect, alerts=alerts), max_analysis_seconds=1)
    status, _, body, _ = _post(app, ANALYZE, {"text": "abc"})
    assert status == 503 and body["detail"] == STRINGS["text_api_timeout"].format(max_seconds=1)
    assert alerts == []
    assert _audit_lines(audit_dir)[0]["outcome"] == "timeout"
