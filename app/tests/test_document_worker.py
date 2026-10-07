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
EXT-07 and EXT-34, decision D-019: document processing (detection,
preview, finalization) no longer runs on the event loop of the single
worker, and every use of PyMuPDF happens on ONE dedicated thread
("PyMuPDF does not support running on multiple threads", official
documentation). What the user sees does not change: same responses, same
errors, same deadlines.

Requests run on a uvloop event loop: the stdlib asyncio selector is blocked
by the enforcing seccomp profile (EXT-11).
"""

import asyncio
import json
import re
import threading
import time

import pymupdf as fitz
import pytest
import uvloop
from fastapi import HTTPException

import main
from tests.test_main_units import _FakeRequest, _SyncUpload
from tests.doc_headers import doc_headers

ALICE = doc_headers("alice@exemple.invalid")


def _run(coro):
    loop = uvloop.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _pdf(text: str = "Le patient Camille Martin est sorti.") -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((50, 80), text, fontsize=10)
    out: bytes = doc.tobytes()
    doc.close()
    return out


def _recording_analyzer(threads: list[str], delay: float = 0.0):
    def fake_analyze(text: str, theme: dict | None = None, **kwargs: object) -> list[dict]:
        threads.append(threading.current_thread().name)
        if delay:
            time.sleep(delay)
        i = text.find("Camille Martin")
        return [{"entity_type": "PERSON", "start": i, "end": i + 14, "score": 0.85}] if i >= 0 else []

    return fake_analyze


def _job_id(response) -> str:
    match = re.search(r'name="job_id" value="([0-9a-f]{32})"', bytes(response.body).decode("utf-8"))
    assert match
    return match.group(1)


def test_detection_apercu_et_finalisation_sur_un_seul_fil_dedie(monkeypatch):
    """D-019: every PyMuPDF use (detect, preview, finalize) on the same,
    dedicated thread — never the event loop's, never two threads."""
    analyzer_threads: list[str] = []
    monkeypatch.setattr(main, "_analyze_text", _recording_analyzer(analyzer_threads))
    pdf = _pdf()  # built before fitz.open is instrumented: the test's own use is not counted
    pymupdf_threads: set[int] = set()
    real_open = fitz.open

    def recording_open(*args, **kwargs):
        pymupdf_threads.add(threading.get_ident())
        return real_open(*args, **kwargs)

    monkeypatch.setattr(main.fitz, "open", recording_open)

    async def scenario():
        loop_thread = threading.get_ident()
        detect = await main.detect_document(_FakeRequest(ALICE), file=_SyncUpload("t.pdf", pdf), theme="")
        job_id = _job_id(detect)
        preview = await main.preview_image(job_id, 0, _FakeRequest(ALICE))
        final = await main.finalize_document(
            _FakeRequest(ALICE),
            job_id=job_id,
            excluded_ids="",
            manual_zones="[]",
            redacted_image_ids="",
            response_format="json",
        )
        return loop_thread, detect, preview, final, job_id

    loop_thread, detect, preview, final, job_id = _run(scenario())
    (main.WORKDIR / f"{job_id}-document-anonymise.pdf").unlink(missing_ok=True)
    assert detect.status_code == preview.status_code == final.status_code == 200
    assert preview.media_type == "image/png"
    assert json.loads(bytes(final.body))["total_redactions"] == 1
    assert len(pymupdf_threads) == 1, "PyMuPDF used from more than one thread"
    assert loop_thread not in pymupdf_threads, "PyMuPDF used on the event loop"
    assert analyzer_threads and all(name.startswith("document") for name in analyzer_threads)


def test_la_boucle_reste_libre_pendant_un_traitement(monkeypatch):
    """EXT-07: while a document is processed, the event loop keeps serving
    (a ticker coroutine stands for /health or a text API request)."""
    monkeypatch.setattr(main, "_analyze_text", _recording_analyzer([], delay=0.5))

    async def scenario():
        ticks = 0
        done = asyncio.Event()

        async def ticker():
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        task = asyncio.ensure_future(ticker())
        started = time.monotonic()
        response = await main.detect_document(
            _FakeRequest(ALICE), file=_SyncUpload("t.csv", b"nom\nCamille Martin\n"), theme=""
        )
        elapsed = time.monotonic() - started
        done.set()
        await task
        return response, ticks, elapsed

    response, ticks, elapsed = _run(scenario())
    main.PENDING_JOBS.pop(_job_id(response), None)
    assert response.status_code == 200
    assert elapsed >= 0.5
    assert ticks >= 20, f"event loop blocked: {ticks} ticks in {elapsed:.2f}s"


def test_erreur_du_traitement_identique(monkeypatch):
    """An HTTPException raised on the worker thread reaches the client
    unchanged (same status, same translated message)."""

    async def scenario():
        return await main.detect_document(
            _FakeRequest(ALICE), file=_SyncUpload("t.pdf", b"%PDF-1.7 tronqu\xe9"), theme=""
        )

    with pytest.raises(HTTPException) as exc_info:
        _run(scenario())
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail in (main.STRINGS["pdf_unreadable"], main.STRINGS["pdf_corrupt_structure"])


def test_delai_de_detection_inchange(monkeypatch):
    """MAX_DETECTION_SECONDS stays enforced (cooperatively, on the worker
    thread): same 400 and message; the thread is free again afterwards."""
    monkeypatch.setattr(main, "MAX_DETECTION_SECONDS", 0)
    monkeypatch.setattr(main, "_analyze_text", _recording_analyzer([], delay=0.05))
    text = "\n".join(f"Ligne {i} Camille Martin" for i in range(400))

    async def scenario():
        return await main.detect_document(_FakeRequest(ALICE), file=_SyncUpload("t.csv", text.encode()), theme="")

    with pytest.raises(HTTPException) as exc_info:
        _run(scenario())
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == main.STRINGS["detection_timeout"].format(max_seconds=0)
    assert main._DOCUMENT_EXECUTOR.submit(lambda: "libre").result(timeout=5) == "libre"


def test_un_seul_fil_de_traitement():
    assert main._DOCUMENT_EXECUTOR._max_workers == 1
