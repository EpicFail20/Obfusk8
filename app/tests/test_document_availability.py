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
Availability of the document flow (EXT-51, EXT-47, decision D-048).

EXT-51: the quota of documents pending review was global only
(MAX_PENDING_JOBS), so one user who uploaded without finalizing blocked the
flow of every other user for JOB_REVIEW_TTL_SECONDS. Each user now has
their own quota (MAX_PENDING_JOBS_PER_USER, 429 with Retry-After), the
global cap stays as a last resort (503), and a user can cancel their own
pending documents (POST /api/cancel/{job_id}).

EXT-47: a request without identity no longer falls back to a shared
"inconnu" owner: it is refused (403).

D-048 point 3: every state-changing document route accepts a request only
from the application's own origin (Origin, else Referer); cancellation also
requires the X-Obfusk8-Action header that only the review page sends.
"""

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Coroutine, Iterator
from typing import Any, cast

import pytest
import uvloop
from fastapi import HTTPException, Request, UploadFile

import main
from tests.doc_headers import TEST_ORIGIN, doc_headers
from tests.test_main_units import _asgi_request, _FakeRequest, _multipart_file_body, _SyncUpload

ALICE = "alice@exemple.invalid"
BOB = "bob@exemple.invalid"
CANCEL_HEADERS = {"x-obfusk8-action": "cancel"}
CANARY_TEXT = "Camille Canari, 12 rue du Leurre"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    loop = uvloop.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _empty_queue(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(main, "_analyze_text", lambda text, theme=None, **kwargs: [])
    monkeypatch.setattr(main, "MAX_PENDING_JOBS", 6)
    monkeypatch.setattr(main, "MAX_PENDING_JOBS_PER_USER", 3, raising=False)
    monkeypatch.setattr(main, "ENABLE_JOB_CANCEL", True, raising=False)
    with main._PENDING_JOBS_LOCK:
        main.PENDING_JOBS.clear()
    yield
    with main._PENDING_JOBS_LOCK:
        main.PENDING_JOBS.clear()


def _req(headers: dict[str, str]) -> Request:
    """The routes only read `request.headers`: a duck-typed stand-in."""
    return cast(Request, _FakeRequest(headers))


def _detect(headers: dict[str, str], payload: bytes = b"nom,ville\nJean,Paris\n") -> Any:
    return _run(main.detect_document(_req(headers), file=cast(UploadFile, _SyncUpload("f.csv", payload)), theme=""))


def _job_id(response: Any) -> str:
    match = re.search(r'name="job_id" value="([0-9a-f]{32})"', bytes(response.body).decode("utf-8"))
    assert match
    return match.group(1)


def _cancel(job_id: str, headers: dict[str, str]) -> Any:
    return _run(main.cancel_document(job_id, _req(headers)))


def _status(call: Callable[[], Any]) -> tuple[int, str, dict[str, str]]:
    try:
        response = call()
    except HTTPException as exc:
        return exc.status_code, str(exc.detail), dict(exc.headers or {})
    return response.status_code, "", {}


# --- EXT-51: one user can no longer block the others ------------------------


def test_ext51_un_utilisateur_qui_remplit_sa_file_ne_bloque_pas_les_autres() -> None:
    """Reproduces EXT-51: Alice uploads without ever finalizing. Before the
    fix she filled the global queue and Bob got 503; now she stops at her
    own quota and Bob still uploads."""
    for _ in range(3):
        assert _detect(doc_headers(ALICE)).status_code == 200
    for _ in range(5):
        status, _, _ = _status(lambda: _detect(doc_headers(ALICE)))
        assert status == 429
    assert _detect(doc_headers(BOB)).status_code == 200


def test_quota_utilisateur_429_avec_retry_after_et_message_traduit() -> None:
    for _ in range(3):
        _detect(doc_headers(ALICE))
    status, detail, headers = _status(lambda: _detect(doc_headers(ALICE)))
    assert status == 429
    assert detail == main.STRINGS["too_many_pending_jobs_user"].format(count=3)
    retry_after = int(headers["Retry-After"])
    # Oldest job just created: its remaining review time plus one sweep.
    assert main.JOB_REVIEW_TTL_SECONDS < retry_after <= main.JOB_REVIEW_TTL_SECONDS + main.SWEEP_INTERVAL_SECONDS


def test_retry_after_suit_le_plus_ancien_document_de_l_utilisateur() -> None:
    now = 10_000.0
    created = [now - 500, now - 100, now - 20]
    ttl = main.JOB_REVIEW_TTL_SECONDS
    assert main._retry_after_seconds(created, now) == ttl - 500 + main.SWEEP_INTERVAL_SECONDS
    # Already expired, waiting for the sweep: at least one second.
    assert main._retry_after_seconds([now - ttl - 30], now) == main.SWEEP_INTERVAL_SECONDS - 30
    assert main._retry_after_seconds([now - ttl - 3600], now) == 1
    # No finished job yet (only an in-flight detection): one detection deadline.
    assert main._retry_after_seconds([], now) == main.MAX_DETECTION_SECONDS


def test_plafond_global_conserve_en_503() -> None:
    users = [f"u{i}@exemple.invalid" for i in range(3)]
    for user in users:
        for _ in range(2):
            assert _detect(doc_headers(user)).status_code == 200
    status, detail, _ = _status(lambda: _detect(doc_headers(BOB)))
    assert status == 503
    assert detail == main.STRINGS["too_many_pending_jobs"]


def test_quota_utilisateur_desactive_par_zero_comportement_global_seul(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "MAX_PENDING_JOBS_PER_USER", 0)
    for _ in range(6):
        assert _detect(doc_headers(ALICE)).status_code == 200
    status, _, _ = _status(lambda: _detect(doc_headers(BOB)))
    assert status == 503


def test_place_reservee_liberee_si_la_detection_echoue() -> None:
    """A rejected document (unreadable PDF) must not keep its reserved place."""
    for _ in range(3):
        status, _, _ = _status(lambda: _detect(doc_headers(ALICE), payload=b"%PDF-1.4 tronque"))
        assert status == 400
    assert main._PENDING_RESERVATIONS == {}
    assert _detect(doc_headers(ALICE)).status_code == 200


def test_quota_compte_l_identite_nettoyee() -> None:
    """The quota key is the same sanitized identity as job ownership: a
    format character does not open a second quota."""
    for _ in range(3):
        _detect(doc_headers(ALICE))
    status, _, _ = _status(lambda: _detect(doc_headers("alice\u200b@exemple.invalid")))
    assert status == 429


def test_gestionnaire_d_erreur_transmet_retry_after() -> None:
    """http_exception_handler used to drop the exception's headers: the 429
    would have lost its Retry-After (JSON and HTML pages alike)."""
    exc = HTTPException(status_code=429, detail="x", headers={"Retry-After": "42"})
    for accept in ("application/json", "text/html"):
        response = _run(main.http_exception_handler(_req({"accept": accept}), exc))
        assert response.headers["retry-after"] == "42"
        assert response.status_code == 429


# --- EXT-47: identity required -----------------------------------------------


@pytest.mark.parametrize("email", [None, "", "   ", "\u200b\u202e"])
def test_ext47_identite_absente_ou_vide_refusee_403_partout(email: str | None) -> None:
    headers = {"origin": TEST_ORIGIN, **CANCEL_HEADERS}
    if email is not None:
        headers["x-auth-request-email"] = email
    job_id = _job_id(_detect(doc_headers(ALICE)))
    calls = {
        "detect": lambda: _detect(headers),
        "finalize": lambda: _run(main.finalize_document(_req(headers), job_id=job_id)),
        "preview": lambda: _run(main.preview_image(job_id, 0, _req(headers))),
        "cancel": lambda: _cancel(job_id, headers),
    }
    for route, call in calls.items():
        status, detail, _ = _status(call)
        assert (status, detail) == (403, main.STRINGS["identity_missing"]), route
    assert job_id in main.PENDING_JOBS


# --- D-048 point 3: same-origin requests only --------------------------------


@pytest.mark.parametrize(
    "origin_headers",
    [
        {},
        {"origin": "https://attaquant.invalid"},
        {"origin": "null"},
        {"origin": TEST_ORIGIN + ".attaquant.invalid"},
        {"origin": "http://obfusk8.test.invalid"},
        {"referer": "https://attaquant.invalid/page"},
        {"origin": "https://attaquant.invalid", "referer": TEST_ORIGIN + "/"},
    ],
)
def test_requete_d_une_autre_origine_refusee_403_sur_chaque_route(origin_headers: dict[str, str]) -> None:
    job_id = _job_id(_detect(doc_headers(ALICE)))
    headers = {"x-auth-request-email": ALICE, **CANCEL_HEADERS, **origin_headers}
    calls = {
        "detect": lambda: _detect(headers),
        "finalize": lambda: _run(main.finalize_document(_req(headers), job_id=job_id)),
        "cancel": lambda: _cancel(job_id, headers),
    }
    for route, call in calls.items():
        status, detail, _ = _status(call)
        assert (status, detail) == (403, main.STRINGS["cross_origin_refused"]), route
    assert job_id in main.PENDING_JOBS


def test_referer_de_la_meme_origine_accepte_a_defaut_d_origin() -> None:
    headers = {"x-auth-request-email": ALICE, "referer": TEST_ORIGIN + "/api/detect"}
    assert _detect(headers).status_code == 200


def test_application_sans_app_domain_refuse_tout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed: without APP_DOMAIN no origin can match."""
    monkeypatch.setattr(main, "APP_ORIGIN", None)
    status, _, _ = _status(lambda: _detect(doc_headers(ALICE)))
    assert status == 403


def test_origine_attendue_derivee_d_app_domain() -> None:
    assert main._application_origin("obfusk8.lab.local") == "https://obfusk8.lab.local"
    assert main._application_origin("") is None
    assert main._application_origin("  ") is None


def test_interface_actuelle_formulaires_detect_finalize_fonctionnent() -> None:
    """The review interface posts ordinary forms; the browser sends the
    page's own Origin: the full flow still works."""
    job_id = _job_id(_detect(doc_headers(ALICE)))
    response = _run(
        main.finalize_document(
            _req(doc_headers(ALICE)),
            job_id=job_id,
            excluded_ids="",
            manual_zones="[]",
            redacted_image_ids="",
            response_format="json",
        )
    )
    assert response.status_code == 200
    assert job_id not in main.PENDING_JOBS


def test_detect_http_origine_etrangere_refusee_avant_tout_traitement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the ASGI stack: FastAPI parses the form first (body bounded by
    MAX_REQUEST_BODY_BYTES), then the 403 is decided before antivirus,
    detection or any pending place."""
    monkeypatch.setattr(main, "GATEWAY_SECRET", "")
    monkeypatch.setattr(main, "_run_antivirus_scan", lambda *args: pytest.fail("document processed"))
    body = _multipart_file_body(b"nom,ville\nJean,Paris\n")
    status, _, resp_body, _ = _asgi_request(
        "POST",
        "/api/detect",
        headers={
            "content-type": "multipart/form-data; boundary=XBOUNDARYX",
            "accept": "application/json",
            "x-auth-request-email": ALICE,
            "origin": "https://attaquant.invalid",
        },
        body_chunks=[body],
        content_length=len(body),
    )
    assert status == 403
    assert json.loads(resp_body)["detail"] == main.STRINGS["cross_origin_refused"]
    assert main.PENDING_JOBS == {}


# --- Cancellation -------------------------------------------------------------


def test_annulation_par_le_proprietaire_libere_la_place_immediatement(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(main, "_record_audit_event", lambda **fields: events.append(fields))
    jobs = [_job_id(_detect(doc_headers(ALICE), payload=f"nom\n{CANARY_TEXT}\n".encode())) for _ in range(3)]
    assert _status(lambda: _detect(doc_headers(ALICE)))[0] == 429

    response = _cancel(jobs[0], {**doc_headers(ALICE), **CANCEL_HEADERS})
    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "cancelled"}
    assert jobs[0] not in main.PENDING_JOBS
    # Nothing of a pending job is ever written to disk: nothing left to purge.
    assert not list(main.WORKDIR.glob(f"{jobs[0]}*"))
    assert _detect(doc_headers(ALICE)).status_code == 200

    assert len(events) == 1
    event = events[0]
    assert event["event"] == "cancellation"
    assert event["user"] == ALICE
    assert event["job_id"] == jobs[0]
    assert event["format"] == "csv"
    serialized = json.dumps(event, ensure_ascii=False)
    assert "Canari" not in serialized
    assert "Leurre" not in serialized


def test_annulation_du_document_d_un_autre_404_generique_sans_le_detruire(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(main, "_record_audit_event", lambda **fields: events.append(fields))
    job_id = _job_id(_detect(doc_headers(ALICE)))
    unknown = "0" * 32
    responses = {
        "other user": _status(lambda: _cancel(job_id, {**doc_headers(BOB), **CANCEL_HEADERS})),
        "unknown": _status(lambda: _cancel(unknown, {**doc_headers(BOB), **CANCEL_HEADERS})),
        "malformed": _status(lambda: _cancel("../../etc/passwd", {**doc_headers(BOB), **CANCEL_HEADERS})),
        "uppercase": _status(lambda: _cancel(job_id.upper(), {**doc_headers(ALICE), **CANCEL_HEADERS})),
        "too long": _status(lambda: _cancel(job_id + "0", {**doc_headers(ALICE), **CANCEL_HEADERS})),
    }
    expected: tuple[int, str, dict[str, str]] = (404, main.STRINGS["job_not_found_expired"], {})
    assert all(response == expected for response in responses.values()), responses
    assert job_id in main.PENDING_JOBS
    assert events == []


def test_annulation_d_un_document_expire_404(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = _job_id(_detect(doc_headers(ALICE)))
    main.PENDING_JOBS[job_id]["created_at"] = time.time() - main.JOB_REVIEW_TTL_SECONDS - 1
    main._sweep_stale_jobs()
    status, detail, _ = _status(lambda: _cancel(job_id, {**doc_headers(ALICE), **CANCEL_HEADERS}))
    assert (status, detail) == (404, main.STRINGS["job_not_found_expired"])


@pytest.mark.parametrize("action", [None, "", "finalize", "CANCEL "])
def test_annulation_sans_l_en_tete_de_l_interface_refusee(action: str | None) -> None:
    job_id = _job_id(_detect(doc_headers(ALICE)))
    headers = doc_headers(ALICE)
    if action is not None:
        headers["x-obfusk8-action"] = action
    status, detail, _ = _status(lambda: _cancel(job_id, headers))
    assert (status, detail) == (403, main.STRINGS["cross_origin_refused"])
    assert job_id in main.PENDING_JOBS


def test_annulation_desactivee_reponse_identique_a_une_route_absente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "GATEWAY_SECRET", "")
    monkeypatch.setattr(main, "ENABLE_JOB_CANCEL", False)
    job_id = _job_id(_detect(doc_headers(ALICE)))
    status, _, body, _ = _asgi_request(
        "POST",
        f"/api/cancel/{job_id}",
        headers={"accept": "application/json", **doc_headers(ALICE), **CANCEL_HEADERS},
    )
    absent_status, _, absent_body, _ = _asgi_request(
        "POST",
        "/api/route-inexistante",
        headers={"accept": "application/json", **doc_headers(ALICE)},
    )
    assert (status, body) == (absent_status, absent_body) == (404, b'{"detail":"Not Found"}')
    assert job_id in main.PENDING_JOBS


def test_annulation_http_corps_au_dela_du_plafond_413(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same body cap in the application as at the edge (cancel-bodylimit)."""
    monkeypatch.setattr(main, "GATEWAY_SECRET", "")
    status, _, _, consumed = _asgi_request(
        "POST",
        "/api/cancel/" + "0" * 32,
        headers={"accept": "application/json", **doc_headers(ALICE), **CANCEL_HEADERS},
        body_chunks=[b"x" * 1024] * 8,
        content_length=8 * 1024,
    )
    assert status == 413
    assert consumed == 0


def test_annulation_http_bout_en_bout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "GATEWAY_SECRET", "")
    job_id = _job_id(_detect(doc_headers(ALICE)))
    status, _, body, _ = _asgi_request(
        "POST",
        f"/api/cancel/{job_id}",
        headers={"accept": "application/json", **doc_headers(ALICE), **CANCEL_HEADERS},
    )
    assert (status, json.loads(body)) == (200, {"status": "cancelled"})
    assert job_id not in main.PENDING_JOBS


def test_page_de_revision_porte_le_bouton_d_annulation() -> None:
    page = bytes(_detect(doc_headers(ALICE)).body).decode("utf-8")
    assert 'id="cancel-job"' in page
    assert "X-Obfusk8-Action" in page
    assert main.STRINGS["cancel_job_button"] in page


def test_page_de_revision_sans_bouton_si_annulation_desactivee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "ENABLE_JOB_CANCEL", False)
    page = bytes(_detect(doc_headers(ALICE)).body).decode("utf-8")
    assert 'id="cancel-job"' not in page


def test_page_de_revision_pdf_porte_aussi_le_bouton() -> None:
    """The pixel review page (PDF, image) is built separately from the text one."""
    import pymupdf as fitz

    doc = fitz.open()
    doc.new_page().insert_text((50, 80), "Rapport fictif", fontsize=10)
    pdf = doc.tobytes()
    doc.close()
    page = bytes(_detect(doc_headers(ALICE), payload=pdf).body).decode("utf-8")
    assert 'id="finalize-form"' in page
    assert 'id="cancel-job"' in page


def test_referer_mal_forme_refuse() -> None:
    headers = {"x-auth-request-email": ALICE, "referer": "https://[obfusk8.test.invalid/"}
    status, detail, _ = _status(lambda: _detect(headers))
    assert (status, detail) == (403, main.STRINGS["cross_origin_refused"])


def test_reservations_multiples_rendues_une_a_une() -> None:
    """Exactness of the reservation count if detections ever run in
    parallel (D-019): each release gives back one place only."""
    with main._PENDING_JOBS_LOCK:
        for _ in range(3):
            main._reserve_pending_place(ALICE)
        status, _, _ = _status(lambda: main._reserve_pending_place(ALICE))
        assert status == 429
        main._release_pending_place(ALICE)
        assert main._PENDING_RESERVATIONS == {ALICE: 2}
        main._release_pending_place(ALICE)
        main._release_pending_place(ALICE)
    assert main._PENDING_RESERVATIONS == {}


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("MAX_PENDING_JOBS_PER_USER", "-1", "MAX_PENDING_JOBS_PER_USER must be 0"),
        ("ENABLE_JOB_CANCEL", "oui", "ENABLE_JOB_CANCEL must be 'true' or 'false'"),
    ],
)
def test_configuration_invalide_refusee_au_demarrage(variable: str, value: str, message: str) -> None:
    """Fail fast, like the antivirus and text API settings: an invalid value
    stops the application instead of silently weakening the quota."""
    env = {**os.environ, variable: value}
    result = subprocess.run(
        [sys.executable, "-c", "import main"], env=env, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode != 0
    assert message in result.stderr
