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
rectangle at all leaves no zone whatsoever.

Decision D-026 (option R1): the missing rectangles are built from the
boxes of the characters of the value; what still cannot be covered is
counted, reported to the reviewer and recorded (counts only) in the audit
log. EXT-37: `search_for` returns None (not an empty list) when the
searched string starts with U+0000, which used to crash pass 2.
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
NO_ISSUE = {"unlocated": {}, "incomplete": {}, "recovered": {}}


def _pdf(*pages: str) -> bytes:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_font(fontname="mono", fontfile=MONO)
        page.insert_text((50, 80), text, fontname="mono", fontsize=10)
    out: bytes = doc.tobytes()
    doc.close()
    return out


def _person_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake analyzer: one PERSON over "Chloé…Boyer" in the text it receives, if any."""

    def fake_analyze(text: str, theme: dict | None = None) -> list[dict]:
        if "Chlo" not in text:
            return []
        start = text.index("Chlo")
        return [
            {"entity_type": "PERSON", "start": start, "end": start + len("Chloé") + 1 + len("Boyer"), "score": 0.85}
        ]

    monkeypatch.setattr(main, "_analyze_text", fake_analyze)


def _redacted_texts(pdf: bytes, detections: list[dict]) -> list[str]:
    """Page texts after the same redaction as finalization."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        main._apply_selected_redactions(doc, detections, set())
        return [page.get_text() for page in doc]
    finally:
        doc.close()


def test_le_texte_extrait_contient_bien_un_nul():
    """Precondition of the regression tests below (observed with PyMuPDF
    1.28.2): the unmapped glyph is extracted as U+0000, a NUL truncates the
    search, and a search STARTING with a NUL returns None (EXT-37)."""
    doc = fitz.open(stream=_pdf(TEXT_WITH_UNMAPPED_GLYPH), filetype="pdf")
    try:
        page = doc[0]
        assert "Chloé\x00Boyer" in page.get_text()
        assert page.search_for("Chloé\x00Boyer") == page.search_for("Chloé")
        assert page.search_for("\x00Boyer") is None
    finally:
        doc.close()


def test_repli_couvre_la_valeur_tronquee_par_un_nul(monkeypatch):
    _person_entity(monkeypatch)
    pdf = _pdf(TEXT_WITH_UNMAPPED_GLYPH)
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        detections, issues = main._detect_pdf(doc)
    finally:
        doc.close()
    assert issues == {**NO_ISSUE, "recovered": {"PERSON": 1}}
    remaining = _redacted_texts(pdf, detections)[0]
    assert "Boyer" not in remaining and "Chlo" not in remaining
    assert "Docteur" in remaining and "suivi" in remaining, "the fallback must not mask beyond the value"


def test_repli_quand_la_recherche_ne_trouve_rien(monkeypatch):
    _person_entity(monkeypatch)
    monkeypatch.setattr(fitz.Page, "search_for", lambda self, needle, **kwargs: [])
    pdf = _pdf(PLAIN_TEXT)
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        detections, issues = main._detect_pdf(doc)
    finally:
        doc.close()
    assert issues == {**NO_ISSUE, "recovered": {"PERSON": 1}}
    remaining = _redacted_texts(pdf, detections)[0]
    assert "Boyer" not in remaining and "Chlo" not in remaining


def test_ext37_recherche_renvoyant_none_ne_plante_pas(monkeypatch):
    """EXT-37: None from search_for, in pass 1 and in pass 2 (propagation
    to a second page), is an empty result, never a crash."""
    _person_entity(monkeypatch)
    monkeypatch.setattr(fitz.Page, "search_for", lambda self, needle, **kwargs: None)
    pdf = _pdf(PLAIN_TEXT, "Rappel : Chloé Boyer.")
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        detections, issues = main._detect_pdf(doc)
    finally:
        doc.close()
    assert issues["recovered"] == {"PERSON": 2}
    assert all("Boyer" not in text for text in _redacted_texts(pdf, detections))


def test_propagation_tronquee_par_un_nul_couverte(monkeypatch):
    """Pass 2 searches the propagated name on every page: a NUL in it
    truncates that search too. The other occurrence must be fully covered."""

    def first_page_only(text: str, theme: dict | None = None) -> list[dict]:
        if "Docteur" not in text:
            return []
        start = text.index("Chlo")
        return [{"entity_type": "PERSON", "start": start, "end": start + 12, "score": 0.85}]

    monkeypatch.setattr(main, "_analyze_text", first_page_only)
    name = "Chloé\u200b Boyer"
    pdf = _pdf(f"Docteur {name} suivi.", f"Rappel : {name} demain.")
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        assert "Chloé\x00 Boyer" in doc[1].get_text()
        detections, _ = main._detect_pdf(doc)
    finally:
        doc.close()
    page_two = _redacted_texts(pdf, detections)[1]
    assert "Boyer" not in page_two and "Chlo" not in page_two
    assert "Rappel" in page_two and "demain" in page_two


def test_localisation_complete_sans_signalement(monkeypatch):
    _person_entity(monkeypatch)
    doc = fitz.open(stream=_pdf(PLAIN_TEXT), filetype="pdf")
    try:
        detections, issues = main._detect_pdf(doc)
    finally:
        doc.close()
    assert len(detections) == 1
    assert issues == NO_ISSUE


def _no_char_boxes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither the search nor the character boxes locate anything: the
    residual case the fallback cannot fix."""
    monkeypatch.setattr(fitz.Page, "search_for", lambda self, needle, **kwargs: [])
    monkeypatch.setattr(main, "_pdf_char_boxes", lambda page, text: [None] * len(text))


def test_localisation_absente_comptee(monkeypatch):
    _person_entity(monkeypatch)
    _no_char_boxes(monkeypatch)
    doc = fitz.open(stream=_pdf(PLAIN_TEXT), filetype="pdf")
    try:
        detections, issues = main._detect_pdf(doc)
    finally:
        doc.close()
    assert detections == []
    assert issues == {**NO_ISSUE, "unlocated": {"PERSON": 1}}


def test_localisation_incomplete_comptee(monkeypatch):
    """Part of the value located, the rest without a box: incomplete."""
    _person_entity(monkeypatch)
    real = main._pdf_char_boxes

    def lose_last_box(page: fitz.Page, text: str) -> list:
        boxes = real(page, text)
        boxes[text.index("Boyer") + 4] = None
        return boxes

    monkeypatch.setattr(main, "_pdf_char_boxes", lose_last_box)
    doc = fitz.open(stream=_pdf(TEXT_WITH_UNMAPPED_GLYPH), filetype="pdf")
    try:
        detections, issues = main._detect_pdf(doc)
    finally:
        doc.close()
    assert detections
    assert issues == {**NO_ISSUE, "incomplete": {"PERSON": 1}}


def _detect_page(monkeypatch: pytest.MonkeyPatch, text: str) -> tuple[str, str]:
    _person_entity(monkeypatch)
    response = _drive(main.detect_document(_FakeRequest(), file=_SyncUpload("t.pdf", _pdf(text)), theme=""))
    assert response.status_code == 200
    with main._PENDING_JOBS_LOCK:
        job_id = next(iter(main.PENDING_JOBS))
    return job_id, bytes(response.body).decode("utf-8")


def test_avertissement_au_relecteur_si_perte_residuelle(monkeypatch):
    _no_char_boxes(monkeypatch)
    job_id, page = _detect_page(monkeypatch, PLAIN_TEXT)
    try:
        expected = html.escape(main.STRINGS["pdf_localization_warning"].format(count=1, types="PERSON (1)"))
        assert expected in page
        assert "Boyer" not in page, "the review page never quotes the value"
    finally:
        main.PENDING_JOBS.pop(job_id, None)


@pytest.mark.parametrize("text", [PLAIN_TEXT, TEXT_WITH_UNMAPPED_GLYPH])
def test_pas_d_avertissement_sans_perte_residuelle(monkeypatch, text):
    """Plain value, or value fully covered by the fallback: no warning."""
    job_id, page = _detect_page(monkeypatch, text)
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
        (main.WORKDIR / f"{job_id}-document-anonymise.pdf").unlink(missing_ok=True)
    finally:
        main.audit_log.removeHandler(handler)
        handler.close()
        for h in original:
            main.audit_log.addHandler(h)
        main.PENDING_JOBS.pop(job_id, None)
    event = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert event["pdf_localization_issues"] == {**NO_ISSUE, "recovered": {"PERSON": 1}}
    assert "Boyer" not in log_path.read_text(encoding="utf-8")
    assert "Boyer" not in caplog.text and "Chlo" not in caplog.text
    assert any("EXT-35" in r.getMessage() and "PERSON" in r.getMessage() for r in caplog.records)


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
        (main.WORKDIR / f"{job_id}-document-anonymise.pdf").unlink(missing_ok=True)
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
    assert issues == NO_ISSUE
    assert calls == [1]


def test_caractere_invisible_de_largeur_nulle_sans_fausse_alerte():
    """A zero-width character (U+200B) has an empty box: nothing visible can
    leak from it. Measured false alarms otherwise: 11 values out of 231 on
    the DejaVu Sans scenario of the EXT-35 bench, none actually exposed."""
    rect = fitz.Rect(0, 0, 10, 10)
    zero_width = fitz.Rect(5, 0, 5, 10)
    assert main._pdf_value_fully_covered("a\u200bb", [rect, zero_width, rect], [rect])


def test_rectangles_de_repli_par_ligne():
    """Runs of uncovered characters become one rectangle per line; a covered
    character, a line break or a change of line ends a run; characters
    without a box are left to the caller (reported, never guessed)."""
    a, b = fitz.Rect(0, 0, 10, 10), fitz.Rect(10, 0, 20, 10)
    c, d = fitz.Rect(30, 0, 40, 10), fitz.Rect(0, 20, 10, 30)
    assert main._pdf_fallback_rects("ab", [a, b], []) == [fitz.Rect(0, 0, 20, 10)]
    assert main._pdf_fallback_rects("a c", [a, None, c], []) == [fitz.Rect(0, 0, 40, 10)], "spaces do not split"
    assert main._pdf_fallback_rects("abc", [a, b, c], [b]) == [a, c], "a covered character splits"
    assert main._pdf_fallback_rects("a\nd", [a, None, d], []) == [a, d]
    assert main._pdf_fallback_rects("ad", [a, d], []) == [a, d], "a change of line splits, even without a line break"
    assert main._pdf_fallback_rects("ax", [a, None], []) == [a]
