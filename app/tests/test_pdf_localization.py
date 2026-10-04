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
EXT-35: PDF detections that cannot be (fully) located on the page must never
be lost in silence.

`_detect_pdf` turns each detected entity into rectangles with
`page.search_for(slice of the page text)`. Measured in phase 2 step B
(benchmarks/documents/pdf_localization_bench.py): when a glyph has no
Unicode mapping, PyMuPDF extracts it as U+0000, and a NUL in the searched
string TRUNCATES the search — "Chloé<NUL>Boyer" only gets a rectangle over
"Chloé", and "Boyer" stays readable after redaction. A search returning no
rectangle at all leaves no zone whatsoever. Both cases must be counted,
reported to the reviewer and recorded (counts only) in the audit log.
"""

import html
import json
import logging

import pymupdf as fitz
import pytest

import main
from tests.test_main_units import _drive, _FakeRequest, _SyncUpload

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
# Synthetic name; U+200B is not in DejaVu Sans Mono, so it is extracted as U+0000.
TEXT_WITH_UNMAPPED_GLYPH = "Docteur Chloé\u200bBoyer suivi."
PLAIN_TEXT = "Docteur Chloé Boyer suivi."


def _pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="mono", fontfile=MONO)
    page.insert_text((50, 80), text, fontname="mono", fontsize=10)
    out: bytes = doc.tobytes()
    doc.close()
    return out


def _person_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake analyzer: one PERSON over "Chloé…Boyer" in the text it receives."""

    def fake_analyze(text: str, theme: dict | None = None) -> list[dict]:
        start = text.index("Chlo")
        return [
            {"entity_type": "PERSON", "start": start, "end": start + len("Chloé") + 1 + len("Boyer"), "score": 0.85}
        ]

    monkeypatch.setattr(main, "_analyze_text", fake_analyze)


def test_le_texte_extrait_contient_bien_un_nul(monkeypatch):
    """Precondition of the regression tests below (observed with PyMuPDF
    1.28.2): the unmapped glyph is extracted as U+0000."""
    doc = fitz.open(stream=_pdf(TEXT_WITH_UNMAPPED_GLYPH), filetype="pdf")
    try:
        assert "Chloé\x00Boyer" in doc[0].get_text()
    finally:
        doc.close()


def test_localisation_incomplete_comptee(monkeypatch):
    _person_entity(monkeypatch)
    doc = fitz.open(stream=_pdf(TEXT_WITH_UNMAPPED_GLYPH), filetype="pdf")
    try:
        detections, issues = main._detect_pdf(doc)
    finally:
        doc.close()
    assert detections, "the located part must still be offered for redaction"
    assert issues == {"unlocated": {}, "incomplete": {"PERSON": 1}}


def test_localisation_absente_comptee(monkeypatch):
    _person_entity(monkeypatch)
    monkeypatch.setattr(fitz.Page, "search_for", lambda self, needle, **kwargs: [])
    doc = fitz.open(stream=_pdf(PLAIN_TEXT), filetype="pdf")
    try:
        detections, issues = main._detect_pdf(doc)
    finally:
        doc.close()
    assert detections == []
    assert issues == {"unlocated": {"PERSON": 1}, "incomplete": {}}


def test_localisation_complete_sans_signalement(monkeypatch):
    _person_entity(monkeypatch)
    doc = fitz.open(stream=_pdf(PLAIN_TEXT), filetype="pdf")
    try:
        detections, issues = main._detect_pdf(doc)
    finally:
        doc.close()
    assert len(detections) == 1
    assert issues == {"unlocated": {}, "incomplete": {}}


def _detect_page(monkeypatch: pytest.MonkeyPatch, text: str) -> tuple[str, str]:
    _person_entity(monkeypatch)
    response = _drive(main.detect_document(_FakeRequest(), file=_SyncUpload("t.pdf", _pdf(text)), theme=""))
    assert response.status_code == 200
    with main._PENDING_JOBS_LOCK:
        job_id = next(iter(main.PENDING_JOBS))
    return job_id, bytes(response.body).decode("utf-8")


def test_avertissement_au_relecteur(monkeypatch):
    job_id, page = _detect_page(monkeypatch, TEXT_WITH_UNMAPPED_GLYPH)
    try:
        expected = html.escape(main.STRINGS["pdf_localization_warning"].format(count=1, types="PERSON (1)"))
        assert expected in page
        assert "Boyer" not in page.split(expected)[0][-2000:], "the warning must not quote the value"
    finally:
        main.PENDING_JOBS.pop(job_id, None)


def test_pas_d_avertissement_sans_perte(monkeypatch):
    job_id, page = _detect_page(monkeypatch, PLAIN_TEXT)
    try:
        assert "localization-warning" not in page
    finally:
        main.PENDING_JOBS.pop(job_id, None)


def test_audit_et_journal_comptes_sans_contenu(monkeypatch, tmp_path, caplog):
    """Counts in the audit line written at finalization, and in the
    application log at detection; never the value."""
    log_path = tmp_path / "audit.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    original = list(main.audit_log.handlers)
    for h in original:
        main.audit_log.removeHandler(h)
    main.audit_log.addHandler(handler)
    try:
        with caplog.at_level(logging.INFO, logger=main.log.name):
            job_id, _ = _detect_page(monkeypatch, TEXT_WITH_UNMAPPED_GLYPH)
        response = _drive(
            main.finalize_document(
                _FakeRequest(),
                job_id=job_id,
                excluded_ids="",
                manual_zones="[]",
                redacted_image_ids="",
                response_format="json",
            )
        )
        assert response.status_code == 200
        (main.WORKDIR / f"{job_id}--anonymise.pdf").unlink(missing_ok=True)
    finally:
        main.audit_log.removeHandler(handler)
        handler.close()
        for h in original:
            main.audit_log.addHandler(h)
        main.PENDING_JOBS.pop(job_id, None)
    event = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert event["pdf_localization_issues"] == {"unlocated": {}, "incomplete": {"PERSON": 1}}
    assert "Boyer" not in log_path.read_text(encoding="utf-8")
    assert "Boyer" not in caplog.text and "Chlo" not in caplog.text
    assert any("localis" in r.getMessage() and "PERSON" in r.getMessage() for r in caplog.records)


def test_audit_inchange_sans_perte(monkeypatch, tmp_path):
    """No issue: the audit line keeps exactly its previous fields."""
    log_path = tmp_path / "audit.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    original = list(main.audit_log.handlers)
    for h in original:
        main.audit_log.removeHandler(h)
    main.audit_log.addHandler(handler)
    try:
        job_id, _ = _detect_page(monkeypatch, PLAIN_TEXT)
        _drive(
            main.finalize_document(
                _FakeRequest(),
                job_id=job_id,
                excluded_ids="",
                manual_zones="[]",
                redacted_image_ids="",
                response_format="json",
            )
        )
        (main.WORKDIR / f"{job_id}--anonymise.pdf").unlink(missing_ok=True)
    finally:
        main.audit_log.removeHandler(handler)
        handler.close()
        for h in original:
            main.audit_log.addHandler(h)
        main.PENDING_JOBS.pop(job_id, None)
    event = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "pdf_localization_issues" not in event


def test_traductions_presentes():
    for lang in ("fr", "en"):
        strings = json.loads((main.I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        assert "{count}" in strings["pdf_localization_warning"]
        assert "{types}" in strings["pdf_localization_warning"]


def test_caractere_sans_boite_compte_comme_non_couvert():
    """A character whose box could not be found must never be assumed covered."""
    rect = fitz.Rect(0, 0, 10, 10)
    assert main._pdf_value_fully_covered("ab", [rect, rect], [rect])
    assert not main._pdf_value_fully_covered("ab", [rect, None], [rect])
    assert main._pdf_value_fully_covered("a b", [rect, None, rect], [rect]), "white space is ignored"


def test_boites_alignees_malgre_image_et_saut_de_ligne_supplementaire(monkeypatch):
    """Image blocks are skipped; an extra line break in the rebuilt text
    (observed after a NUL) does not shift the following characters."""
    doc = fitz.open()
    page = doc.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), False)
    page.insert_image(fitz.Rect(300, 300, 340, 340), pixmap=pixmap)
    page.insert_text((50, 80), "Camille Martin", fontsize=10)
    pdf = fitz.open(stream=doc.tobytes(), filetype="pdf")
    try:
        page = pdf[0]
        text = page.get_text()
        boxes = main._pdf_char_boxes(page, text)
        assert all(box is not None for c, box in zip(text, boxes, strict=True) if not c.isspace())
        real = page.get_text
        monkeypatch.setattr(
            type(page),
            "get_text",
            lambda self, *a, **k: _with_extra_line_break(real(*a, **k)) if a == ("rawdict",) else real(*a, **k),
        )
        shifted = main._pdf_char_boxes(page, text)
        assert shifted == boxes
    finally:
        pdf.close()
        doc.close()


def _with_extra_line_break(raw: dict) -> dict:
    """Splits the first text line after its 7th character, as PyMuPDF does
    after an unmapped glyph: one more line break in the rebuilt text."""
    for block in raw["blocks"]:
        if block.get("type") == 0:
            line = block["lines"][0]
            span = line["spans"][0]
            head = {**span, "chars": span["chars"][:7]}
            tail = {**span, "chars": span["chars"][7:]}
            block["lines"] = [
                {**line, "spans": [head]},
                {**line, "spans": [tail, *line["spans"][1:]]},
                *block["lines"][1:],
            ]
            break
    return raw


def test_deux_entites_sur_une_page(monkeypatch):
    """Character boxes are computed once per page and reused."""

    def fake_analyze(text: str, theme: dict | None = None) -> list[dict]:
        first, second = text.index("Chlo"), text.index("Suivi")
        return [
            {"entity_type": "PERSON", "start": first, "end": first + 11, "score": 0.85},
            {"entity_type": "PERSON", "start": second, "end": second + 13, "score": 0.85},
        ]

    monkeypatch.setattr(main, "_analyze_text", fake_analyze)
    calls = []
    real = main._pdf_char_boxes
    monkeypatch.setattr(main, "_pdf_char_boxes", lambda page, text: calls.append(1) or real(page, text))
    doc = fitz.open(stream=_pdf(PLAIN_TEXT + " Suivi Martin."), filetype="pdf")
    try:
        _, issues = main._detect_pdf(doc)
    finally:
        doc.close()
    assert issues == {"unlocated": {}, "incomplete": {}}
    assert calls == [1]


def test_caractere_invisible_de_largeur_nulle_sans_fausse_alerte():
    """A zero-width character (U+200B) has an empty box: nothing visible can
    leak from it. Measured false alarms otherwise: 11 values out of 231 on
    the DejaVu Sans scenario of the EXT-35 bench, none actually exposed."""
    rect = fitz.Rect(0, 0, 10, 10)
    zero_width = fitz.Rect(5, 0, 5, 10)
    assert main._pdf_value_fully_covered("a\u200bb", [rect, zero_width, rect], [rect])
