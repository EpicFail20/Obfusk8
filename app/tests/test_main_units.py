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
Unit tests — require NO external service (no Presidio, no Traefik, no
Keycloak). Fast, to be run on every change to main.py or a theme file.

Execution: from /app in the container, `pytest` or `pytest tests/ -v`.
"""
import json
import io
import re
import struct
import sys
import zipfile
from pathlib import Path

import pytest
from docx import Document as WordDocument
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402
import pymupdf as fitz  # noqa: E402


# ---------------------------------------------------------------------------
# _normalize_allcaps
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("DURAND Xavier", "Durand Xavier"),
        ("Jean DUPONT habite à Paris", "Jean Dupont habite à Paris"),
        ("déjà en minuscule", "déjà en minuscule"),
        ("N°123", "N°123"),  # a single uppercase letter: never touched
    ],
)
def test_normalize_allcaps(text, expected):
    assert main._normalize_allcaps(text) == expected


# ---------------------------------------------------------------------------
# Theme regexes — examples that MUST match / must NOT match
# ---------------------------------------------------------------------------

def _get_pattern_regex(theme_key: str, pattern_name: str) -> str:
    """Looks up the regex of a specific pattern in an actually loaded theme
    (single source of truth: the same JSON sent to Presidio)."""
    theme = main.THEMES[theme_key]
    for recognizer in theme["ad_hoc_recognizers"]:
        for pattern in recognizer["patterns"]:
            if pattern["name"] == pattern_name:
                return pattern["regex"]
    raise KeyError(f"Pattern {pattern_name!r} not found in theme {theme_key!r}")


IEP_PATTERN_NAME = "IEP (zéro(s) en tête + 8 chiffres min)"

IEP_SHOULD_MATCH = [
    "IEP: 00123456",     # exactly 8 digits, 1 leading zero — edge case
    "IEP 000456789",     # 9 digits, 3 leading zeros
    "iep:0012345678",    # lowercase, no space
]
IEP_SHOULD_NOT_MATCH = [
    "IEP: 12345678",     # no leading zero
    "IEP: 0012345",      # only 7 digits (8 expected minimum)
]


@pytest.mark.parametrize("text", IEP_SHOULD_MATCH)
def test_iep_pattern_matches_valid_examples(text):
    regex = _get_pattern_regex("medical", IEP_PATTERN_NAME)
    assert re.search(regex, text) is not None, f"should have matched: {text!r}"


@pytest.mark.parametrize("text", IEP_SHOULD_NOT_MATCH)
def test_iep_pattern_rejects_invalid_examples(text):
    regex = _get_pattern_regex("medical", IEP_PATTERN_NAME)
    assert re.search(regex, text) is None, f"should not have matched: {text!r}"


# ---------------------------------------------------------------------------
# _cluster_detections — merging of overlapping zones (bug fixed tonight)
# ---------------------------------------------------------------------------

def test_cluster_detections_merges_overlapping_rects():
    """Two detections at almost the same spot (e.g. generic NER + custom
    pattern on the same name) must merge into a single clickable zone,
    otherwise a click only excludes one and the other stays redacted."""
    detections = [
        {
            "id": "a", "page": 0, "entity_type": "PERSON",
            "page_rect": [10, 10, 50, 20], "display_rect": [20, 20, 100, 40],
        },
        {
            "id": "b", "page": 0, "entity_type": "PATIENT_ID",
            "page_rect": [11, 10, 49, 20], "display_rect": [22, 20, 98, 40],
        },
    ]
    clusters = main._cluster_detections(detections, iou_threshold=0.3)
    assert len(clusters) == 1
    assert set(clusters[0]["member_ids"]) == {"a", "b"}


def test_cluster_detections_keeps_distinct_zones_separate():
    """Two detections clearly in different spots must never be wrongly
    merged."""
    detections = [
        {
            "id": "a", "page": 0, "entity_type": "PERSON",
            "page_rect": [10, 10, 50, 20], "display_rect": [20, 20, 100, 40],
        },
        {
            "id": "b", "page": 0, "entity_type": "LOCATION",
            "page_rect": [200, 200, 250, 220], "display_rect": [400, 400, 500, 440],
        },
    ]
    clusters = main._cluster_detections(detections, iou_threshold=0.3)
    assert len(clusters) == 2


def test_rect_iou_no_overlap_is_zero():
    assert main._rect_iou([0, 0, 10, 10], [100, 100, 110, 110]) == 0.0


def test_rect_iou_identical_rects_is_one():
    assert main._rect_iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0


# ---------------------------------------------------------------------------
# _check_page_images_sane — defense in depth for CVE-2026-3308 (absurd PDF
# image dimensions before any call to page.get_pixmap()). A simple fake
# object with get_images(full=True) is enough: the function only reads
# indices 2 (width) and 3 (height) of the tuple, just like PyMuPDF does for
# each embedded image referenced by a page.
# ---------------------------------------------------------------------------

class _FakePage:
    def __init__(self, images):
        self._images = images

    def get_images(self, full=False):
        return self._images


def _fake_image_entry(width, height):
    # (xref, smask, width, height, bpc, colorspace, alt_cs, name, filter, referencer_xref)
    return (1, 0, width, height, 8, "DeviceRGB", "", "Im0", "DCTDecode", 0)


def test_check_page_images_sane_allows_normal_dimensions():
    page = _FakePage([_fake_image_entry(800, 600)])
    main._check_page_images_sane(page)  # must not raise


def test_check_page_images_sane_allows_page_without_images():
    page = _FakePage([])
    main._check_page_images_sane(page)  # must not raise


def test_check_page_images_sane_rejects_oversized_dimensions():
    huge_side = main.MAX_IMAGE_PIXELS + 1
    page = _FakePage([_fake_image_entry(huge_side, 1)])
    with pytest.raises(HTTPException) as exc_info:
        main._check_page_images_sane(page)
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("width,height", [(0, 100), (100, 0), (-1, 100), (100, -1)])
def test_check_page_images_sane_rejects_zero_or_negative_dimensions(width, height):
    page = _FakePage([_fake_image_entry(width, height)])
    with pytest.raises(HTTPException) as exc_info:
        main._check_page_images_sane(page)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# _send_alert / _run_antivirus_scan — a failing alert must never break the
# main flow it monitors (security review, section 10 of
# plan_audit_consolide.md, same principle as the antivirus/ICAP review)
# ---------------------------------------------------------------------------

class _FakeScanner:
    def __init__(self, is_clean, threat_name=None):
        self._is_clean = is_clean
        self._threat_name = threat_name

    def scan(self, raw, filename_hint=""):
        from antivirus import ScanResult
        return ScanResult(is_clean=self._is_clean, engine_name="fake", threat_name=self._threat_name)


def _raising_alert_sink():
    raise RuntimeError("ALERT_SINK mal configuré (simulé)")


def test_send_alert_n_echoue_jamais_meme_si_get_alert_sink_leve(monkeypatch, caplog):
    monkeypatch.setattr(main, "get_alert_sink", _raising_alert_sink)
    main._send_alert(main.Alert(severity=main.AlertSeverity.WARNING, source="test", message="x"))  # must not raise


def test_antivirus_indisponible_renvoie_503_meme_si_alerte_echoue(monkeypatch):
    """Before the fix: an exception in get_alert_sink()/.send() prevented
    reaching the expected HTTPException 503, raising a generic 500 instead —
    masking the real cause (antivirus unavailable)."""
    monkeypatch.setattr(main, "get_scanner", lambda: (_ for _ in ()).throw(main.AntivirusUnavailableError("down")))
    monkeypatch.setattr(main, "get_alert_sink", _raising_alert_sink)
    monkeypatch.delenv("AV_ENFORCE", raising=False)  # default = blocking

    with pytest.raises(HTTPException) as exc_info:
        main._run_antivirus_scan(b"raw", "fichier.pdf", "abc123")
    assert exc_info.value.status_code == 503


def test_menace_detectee_renvoie_400_meme_si_alerte_echoue(monkeypatch):
    monkeypatch.setattr(main, "get_scanner", lambda: _FakeScanner(is_clean=False, threat_name="EICAR-Test"))
    monkeypatch.setattr(main, "get_alert_sink", _raising_alert_sink)
    monkeypatch.delenv("AV_ENFORCE", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        main._run_antivirus_scan(b"raw", "fichier.pdf", "abc123")
    assert exc_info.value.status_code == 400
    assert "EICAR-Test" in exc_info.value.detail


def test_nom_de_menace_est_assaini_avant_reutilisation(monkeypatch):
    """Same visual spoofing risk via Unicode formatting characters
    (RTL override...) as the one found and fixed on the audit log
    (3.5) — threat_name comes from the ICAP server, not the uploaded file,
    but sanitized as a precaution before joining the HTTP response and the
    syslog log."""
    threat_with_rtl_override = "Trojan‮exe.pdf"
    monkeypatch.setattr(main, "get_scanner", lambda: _FakeScanner(is_clean=False, threat_name=threat_with_rtl_override))
    monkeypatch.setattr(main, "get_alert_sink", _raising_alert_sink)
    monkeypatch.delenv("AV_ENFORCE", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        main._run_antivirus_scan(b"raw", "fichier.pdf", "abc123")
    assert "‮" not in exc_info.value.detail


# ---------------------------------------------------------------------------
# Manual redaction of a PDF image (_apply_manual_redactions) — the zone
# drawn by hand on an image must be truly unrecoverable in the output file,
# not just visually masked. Promoted to a permanent test (previously
# verified only via a disposable script, see
# app/tests/fixtures/verification_scripts/probe_image_redaction.py and
# plan_audit_consolide.md section 8): exercises the REAL entry point of
# the project rather than a reimplementation of the PyMuPDF mechanism.
# ---------------------------------------------------------------------------

RED = (255, 0, 0)
BLUE = (0, 0, 255)


def _build_two_color_pdf() -> bytes:
    """Single-page PDF with a 200x200 image: top half red (to be
    redacted), bottom half blue (to be kept as-is)."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200))
    pix.set_rect(fitz.IRect(0, 0, 200, 100), RED)
    pix.set_rect(fitz.IRect(0, 100, 200, 200), BLUE)
    page.insert_image(fitz.Rect(50, 50, 250, 250), pixmap=pix)
    raw = doc.tobytes()
    doc.close()
    return raw


def _color_present_in_any_image_object(pdf_bytes: bytes, target_rgb: tuple) -> bool:
    """Scans ALL image objects in the document (not just the ones
    reachable from the current page tree) — same method as the one that
    revealed the orphan-object leak after redaction (section 7)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        target = bytes(target_rgb)
        for xref in range(1, doc.xref_length()):
            if not doc.xref_is_image(xref):
                continue
            try:
                raw_img = doc.extract_image(xref)["image"]
                ipix = fitz.Pixmap(raw_img)
                if ipix.colorspace is None or ipix.colorspace.n < 3:
                    ipix = fitz.Pixmap(fitz.csRGB, ipix)
                elif ipix.alpha:
                    ipix = fitz.Pixmap(ipix, 0)
            except Exception:
                continue
            samples, n = ipix.samples, ipix.n
            if any(samples[i:i + 3] == target for i in range(0, len(samples) - n + 1, n)):
                return True
    finally:
        doc.close()
    return False


def test_zone_manuelle_sur_image_pdf_est_irrecuperable():
    raw = _build_two_color_pdf()
    assert _color_present_in_any_image_object(raw, RED)
    assert _color_present_in_any_image_object(raw, BLUE)

    # Manual zone in preview coordinates (display_rect = PDF coordinates *
    # PREVIEW_ZOOM), covering only the RED half of the inserted image.
    zone = {
        "page": 0,
        "rect": [
            50 * main.PREVIEW_ZOOM, 50 * main.PREVIEW_ZOOM,
            250 * main.PREVIEW_ZOOM, 150 * main.PREVIEW_ZOOM,
        ],
    }
    doc = fitz.open(stream=raw, filetype="pdf")
    manual_count = main._apply_manual_redactions(doc, [zone])
    main._wipe_pdf_metadata(doc)
    out_bytes = doc.tobytes(garbage=4, clean=True, deflate=True)
    doc.close()

    assert manual_count == 1
    assert not _color_present_in_any_image_object(out_bytes, RED), "the redacted red is still recoverable"
    assert _color_present_in_any_image_object(out_bytes, BLUE), "the non-redacted blue wrongly disappeared"


# ---------------------------------------------------------------------------
# Manual redaction of an entire DOCX image (_apply_docx_image_redactions) —
# fills the gap identified during the previous review (no mechanism
# existed for DOCX images). "Whole image" granularity rather than "precise
# pixel zone" as for PDF: DOCX has no fixed coordinate-based layout without
# a full rendering engine.
# ---------------------------------------------------------------------------

def _make_solid_png(size: int, rgb: tuple) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, size, size))
    pix.set_rect(pix.irect, rgb)
    return pix.tobytes("png")


def _docx_media_pixel_colors(raw_docx: bytes) -> list:
    """Color of pixel (0,0) of every image found in word/media/ of the
    output zip — direct zip scan rather than the python-docx relationship
    graph, to also verify that no orphan entry remains either (unlike PDF,
    a DOCX/OPC save always rewrites the whole package from the live graph,
    but it's better to verify than to assume)."""
    colors = []
    with zipfile.ZipFile(io.BytesIO(raw_docx)) as zf:
        for name in zf.namelist():
            if name.startswith("word/media/"):
                pix = fitz.Pixmap(zf.read(name))
                colors.append(pix.pixel(0, 0)[:3])
    return colors


def test_collect_docx_image_parts_trouve_les_images_du_corps():
    doc = WordDocument()
    doc.add_paragraph("texte")
    doc.add_picture(io.BytesIO(_make_solid_png(20, RED)))
    buf = io.BytesIO()
    doc.save(buf)

    reopened = WordDocument(io.BytesIO(buf.getvalue()))
    parts = main._collect_docx_image_parts(reopened)
    assert len(parts) == 1
    assert parts[0].content_type == "image/png"


def test_image_docx_caviardee_est_irrecuperable():
    doc = WordDocument()
    doc.add_picture(io.BytesIO(_make_solid_png(20, RED)))
    doc.add_picture(io.BytesIO(_make_solid_png(20, BLUE)))
    buf = io.BytesIO()
    doc.save(buf)
    raw = buf.getvalue()

    assert sorted(_docx_media_pixel_colors(raw)) == sorted([RED, BLUE])

    reopened = WordDocument(io.BytesIO(raw))
    parts = main._collect_docx_image_parts(reopened)
    assert len(parts) == 2
    red_part = next(p for p in parts if fitz.Pixmap(p.blob).pixel(0, 0)[:3] == RED)

    count = main._apply_docx_image_redactions(reopened, {str(red_part.partname)})
    assert count == 1

    out_buf = io.BytesIO()
    reopened.save(out_buf)
    out_colors = _docx_media_pixel_colors(out_buf.getvalue())

    assert (255, 0, 0) not in out_colors, "the redacted red is still recoverable in the output zip"
    assert (0, 0, 255) in out_colors, "the non-redacted blue wrongly disappeared"
    assert (0, 0, 0) in out_colors, "the replacement black square is missing"


def test_apply_docx_image_redactions_sans_selection_ne_modifie_rien():
    doc = WordDocument()
    doc.add_picture(io.BytesIO(_make_solid_png(20, RED)))
    buf = io.BytesIO()
    doc.save(buf)
    reopened = WordDocument(io.BytesIO(buf.getvalue()))

    assert main._apply_docx_image_redactions(reopened, set()) == 0
    assert main._apply_docx_image_redactions(reopened, {"/word/media/inexistant.png"}) == 0


@pytest.mark.parametrize("content_type,expected_fmt_ok", [("image/png", True), ("image/jpeg", True), ("image/gif", True)])
def test_black_placeholder_image_bytes_est_toujours_decodable(content_type, expected_fmt_ok):
    """Even for a format that cannot be re-encoded identically (gif...),
    the produced placeholder (PNG regardless) must remain a valid image."""
    data = main._black_placeholder_image_bytes(content_type)
    pix = fitz.Pixmap(data)
    assert pix.pixel(0, 0)[:3] == (0, 0, 0)


def test_build_docx_images_review_section_contient_case_a_cocher():
    doc = WordDocument()
    doc.add_picture(io.BytesIO(_make_solid_png(20, RED)))
    buf = io.BytesIO()
    doc.save(buf)
    reopened = WordDocument(io.BytesIO(buf.getvalue()))

    html_out = main._build_docx_images_review_section(reopened)
    assert 'class="docx-image-checkbox"' in html_out
    assert "data-image-id=" in html_out
    assert "base64," in html_out  # small image: embedded preview


def test_build_docx_images_review_section_grosse_image_sans_apercu(monkeypatch):
    monkeypatch.setattr(main, "MAX_DOCX_IMAGE_PREVIEW_BYTES", 10)  # force the threshold to be exceeded
    doc = WordDocument()
    doc.add_picture(io.BytesIO(_make_solid_png(20, RED)))
    buf = io.BytesIO()
    doc.save(buf)
    reopened = WordDocument(io.BytesIO(buf.getvalue()))

    html_out = main._build_docx_images_review_section(reopened)
    assert "Aperçu indisponible" in html_out
    assert 'class="docx-image-checkbox"' in html_out  # still selectable regardless


def test_build_docx_images_review_section_vide_si_aucune_image():
    doc = WordDocument()
    doc.add_paragraph("aucune image ici")
    buf = io.BytesIO()
    doc.save(buf)
    reopened = WordDocument(io.BytesIO(buf.getvalue()))

    assert main._build_docx_images_review_section(reopened) == ""


class _FakeImagePart:
    """content_type and partname come from the uploaded .docx file
    (declared in [Content_Types].xml / relationship targets) — hence
    controllable by an attacker. Verifies that an XSS payload in either
    one does not survive as-is in the review page rendered to the
    browser."""
    def __init__(self, partname, content_type, blob):
        self.partname = partname
        self.content_type = content_type
        self.blob = blob


def test_build_docx_images_review_section_echappe_le_contenu_hostile(monkeypatch):
    payload = '"><script>alert(1)</script>'
    fake_part = _FakeImagePart(payload, payload, _make_solid_png(10, RED))
    monkeypatch.setattr(main, "_collect_docx_image_parts", lambda document: [fake_part])

    html_out = main._build_docx_images_review_section(object())

    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


# ---------------------------------------------------------------------------
# Image support (PNG/JPEG) - input validation, OCR, redaction, metadata
# ---------------------------------------------------------------------------

from PIL import Image  # noqa: E402
from PIL.PngImagePlugin import PngInfo  # noqa: E402


def _solid_image_bytes(size, rgb, fmt="PNG", mode="RGB"):
    img = Image.new(mode, (size, size), rgb)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.parametrize("raw,expected", [
    (main.PNG_SIGNATURE + b"reste-arbitraire", "image"),
    (main.JPEG_SIGNATURE + b"reste-arbitraire", "image"),
])
def test_detect_file_kind_reconnait_png_et_jpeg(raw, expected):
    assert main._detect_file_kind(raw) == expected


def test_open_and_validate_image_rejette_dimensions_excessives():
    # Mode "1" (bilevel): ~8 MB for 8000x8000 = 64,000,000 pixels, well
    # above MAX_IMAGE_PIXELS (40,000,000 by default) — verifies that the
    # rejection is indeed based on the declared dimensions, without ever
    # decoding the pixels of a real image of that size.
    img = Image.new("1", (8000, 8000))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    with pytest.raises(HTTPException) as exc_info:
        main._open_and_validate_image(buf.getvalue())
    assert exc_info.value.status_code == 400


def test_open_and_validate_image_rejette_bombe_au_dela_du_double_du_seuil():
    # Beyond 2x MAX_IMAGE_PIXELS, Pillow itself raises
    # DecompressionBombError from Image.open() — verifies that this
    # internal exception is properly converted into a clean rejection
    # (400), not a raw traceback that would propagate as-is.
    img = Image.new("1", (13000, 13000))  # 169,000,000 pixels
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    with pytest.raises(HTTPException) as exc_info:
        main._open_and_validate_image(buf.getvalue())
    assert exc_info.value.status_code == 400


def test_open_and_validate_image_accepte_dimensions_normales():
    raw = _solid_image_bytes(50, RED)
    img = main._open_and_validate_image(raw)
    assert img.size == (50, 50)
    assert img.format == "PNG"


def test_image_corrompue_ou_tronquee_leve_erreur_propre():
    raw = _solid_image_bytes(50, RED)
    truncated = raw[:-30]  # header (IHDR) intact, IDAT data truncated

    # The header remains readable: Image.open() (Phase 1) does not yet
    # detect the problem, only a full decode (.load(), as done by
    # _handle_detect_image after validation) reveals it — reproduces the
    # real path rather than a test shortcut.
    img = main._open_and_validate_image(truncated)
    with pytest.raises(OSError):
        img.load()


def test_detect_image_sans_texte_renvoie_liste_vide():
    # Solid white image: no OCR word found -> no error, empty list
    # directly (must never call Presidio in this case).
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    assert main._detect_image(img) == []


def test_tesseract_cmd_est_un_chemin_absolu_pas_une_recherche_path():
    # Defense in depth against a $PATH hijack (see main.py): never
    # pytesseract's default value ("tesseract" alone).
    assert main.pytesseract.pytesseract.tesseract_cmd == "/usr/bin/tesseract"


def test_run_ocr_sous_processus_reellement_tue_au_depassement_du_timeout(monkeypatch):
    """
    Verifies the REAL tesseract subprocess (not a mock): with an
    absurdly short timeout, the subprocess must be cleanly terminated
    (SIGTERM/SIGKILL on the pytesseract side, see its kill() function)
    and the caller must receive a clean error — never a request blocked
    indefinitely, never a raw traceback. Reproduces the scenario that
    motivated adding MAX_OCR_SECONDS: a `subprocess.Popen` with no time
    bound can run indefinitely on a pathological image.
    """
    monkeypatch.setattr(main, "MAX_OCR_SECONDS", 0.001)
    img = Image.new("RGB", (200, 200), (255, 255, 255))

    with pytest.raises(HTTPException) as exc_info:
        main._run_ocr(img)
    assert exc_info.value.status_code == 400
    assert "temps imparti" in exc_info.value.detail


def test_run_ocr_timeout_error_distinct_de_tesseract_error(monkeypatch):
    """`TesseractError` inherits from `RuntimeError` — verifies that the
    more specific except clause does intercept first (no false timeout
    message for a real engine error)."""
    def _raise_tesseract_error(*args, **kwargs):
        raise main.pytesseract.TesseractError(1, "erreur moteur simulée")

    monkeypatch.setattr(main.pytesseract, "image_to_data", _raise_tesseract_error)
    img = Image.new("RGB", (10, 10), (255, 255, 255))

    with pytest.raises(HTTPException) as exc_info:
        main._run_ocr(img)
    assert exc_info.value.status_code == 400
    assert "temps imparti" not in exc_info.value.detail


def test_build_ocr_text_reconstruit_avec_offsets_corrects():
    words = [
        {"text": "Jean", "left": 0, "top": 0, "width": 30, "height": 10, "block_num": 1, "par_num": 1, "line_num": 1},
        {"text": "Durand", "left": 35, "top": 0, "width": 40, "height": 10, "block_num": 1, "par_num": 1, "line_num": 1},
        {"text": "Suite", "left": 0, "top": 20, "width": 30, "height": 10, "block_num": 1, "par_num": 1, "line_num": 2},
    ]
    text, spans = main._build_ocr_text(words)
    assert text == "Jean Durand\nSuite"
    assert text[spans[0][0]:spans[0][1]] == "Jean"
    assert text[spans[1][0]:spans[1][1]] == "Durand"
    assert text[spans[2][0]:spans[2][1]] == "Suite"


def test_map_entities_to_word_boxes_associe_les_bonnes_bounding_boxes():
    words = [
        {"text": "Jean", "left": 0, "top": 0, "width": 30, "height": 10, "block_num": 1, "par_num": 1, "line_num": 1},
        {"text": "Durand", "left": 35, "top": 0, "width": 40, "height": 10, "block_num": 1, "par_num": 1, "line_num": 1},
        {"text": "habite", "left": 0, "top": 20, "width": 30, "height": 10, "block_num": 1, "par_num": 1, "line_num": 2},
    ]
    text, spans = main._build_ocr_text(words)  # "Jean Durand\nhabite"
    entities = [{"start": 0, "end": 11, "entity_type": "PERSON"}]  # "Jean Durand"

    detections = main._map_entities_to_word_boxes(entities, spans)

    assert len(detections) == 2  # one rectangle per covered word
    assert all(d["entity_type"] == "PERSON" for d in detections)
    assert all(d["page"] == 0 for d in detections)
    rects = sorted(d["page_rect"] for d in detections)
    assert rects == [[0.0, 0.0, 30.0, 10.0], [35.0, 0.0, 75.0, 10.0]]
    # "habite" (outside the span [0,11)) must produce no detection
    assert all(d["page_rect"][0] < 76 for d in detections)


def test_zone_manuelle_image_est_irrecuperable():
    """Same requirement as for PDF (see
    test_zone_manuelle_sur_image_pdf_est_irrecuperable): a zone drawn
    manually on ONE half of an image must make that half unrecoverable
    at the pixel level, without touching the other half."""
    W, H = 200, 100
    img = Image.new("RGB", (W, H), RED)
    for x in range(W // 2, W):
        for y in range(H):
            img.putpixel((x, y), BLUE)

    draw = main.ImageDraw.Draw(img)
    manual_count = main._apply_manual_image_redactions(draw, img.mode, img.size, [{"page": 0, "rect": [0, 0, W // 2, H]}])
    assert manual_count == 1

    out_bytes = main._strip_image_metadata_and_encode(img, "PNG")
    out_img = Image.open(io.BytesIO(out_bytes))
    assert out_img.getpixel((10, 10)) == (0, 0, 0), "the redacted red zone did not turn black"
    assert out_img.getpixel((W - 10, 10)) == BLUE, "the non-redacted blue was wrongly altered"
    # Exhaustive scan: no residual red pixel anywhere in the output file.
    assert RED not in out_img.get_flattened_data(), "the redacted red is still recoverable somewhere in the image"


def test_strip_image_metadata_removes_png_text_chunks():
    img = Image.new("RGB", (20, 20), RED)
    pnginfo = PngInfo()
    pnginfo.add_text("Comment", "donnee-sensible")
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=pnginfo)
    reopened = Image.open(io.BytesIO(buf.getvalue()))
    assert "Comment" in reopened.text  # precondition: the chunk is indeed present

    out_bytes = main._strip_image_metadata_and_encode(reopened, "PNG")
    out_img = Image.open(io.BytesIO(out_bytes))
    assert not getattr(out_img, "text", {}), "a PNG text chunk survived stripping"
    assert b"donnee-sensible" not in out_bytes


def test_strip_image_metadata_removes_icc_profile():
    img = Image.new("RGB", (20, 20), RED)
    buf = io.BytesIO()
    img.save(buf, format="PNG", icc_profile=b"FAKE-ICC-PROFILE-DATA")
    reopened = Image.open(io.BytesIO(buf.getvalue()))
    assert "icc_profile" in reopened.info  # precondition

    out_bytes = main._strip_image_metadata_and_encode(reopened, "PNG")
    out_img = Image.open(io.BytesIO(out_bytes))
    assert "icc_profile" not in out_img.info
    assert b"FAKE-ICC-PROFILE-DATA" not in out_bytes


def _build_ifd(entries, next_ifd_offset=0):
    out = struct.pack("<H", len(entries))
    for tag, type_, count, value_bytes in entries:
        out += struct.pack("<HHI", tag, type_, count) + value_bytes
    out += struct.pack("<I", next_ifd_offset)
    return out


def _make_solid_jpeg(size, rgb):
    img = Image.new("RGB", (size, size), rgb)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _embed_exif_gps_and_thumbnail(main_jpeg: bytes, thumb_jpeg: bytes) -> bytes:
    """
    Manually builds a complete APP1/EXIF segment (GPS coordinates + a JPEG
    thumbnail embedded in IFD1, as a real camera/smartphone would) and
    inserts it right after the SOI marker of the main JPEG — faithfully
    reproduces the real EXIF structure rather than an approximation, so
    that the no-leak test (see
    test_image_exif_gps_et_miniature_ne_survivent_pas_au_caviardage) covers
    a realistic case.
    """
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    ifd0_offset = 8
    ifd0_size = 2 + 1 * 12 + 4
    gps_offset = ifd0_offset + ifd0_size
    gps_entries = [
        (1, 2, 2, b"N\x00\x00\x00"),                       # GPSLatitudeRef
        (2, 3, 2, struct.pack("<HH", 48, 51)),              # GPSLatitude (approx.)
        (3, 2, 2, b"E\x00\x00\x00"),                        # GPSLongitudeRef
        (4, 3, 2, struct.pack("<HH", 2, 21)),               # GPSLongitude (approx.)
    ]
    gps_size = 2 + len(gps_entries) * 12 + 4
    ifd1_offset = gps_offset + gps_size
    ifd1_size = 2 + 3 * 12 + 4
    thumb_offset = ifd1_offset + ifd1_size

    ifd0 = _build_ifd([(0x8825, 4, 1, struct.pack("<I", gps_offset))], next_ifd_offset=ifd1_offset)
    gps_ifd = _build_ifd(gps_entries, next_ifd_offset=0)
    ifd1 = _build_ifd(
        [
            (0x0103, 3, 1, struct.pack("<HH", 6, 0)),               # Compression = JPEG
            (0x0201, 4, 1, struct.pack("<I", thumb_offset)),        # JPEGInterchangeFormat
            (0x0202, 4, 1, struct.pack("<I", len(thumb_jpeg))),     # JPEGInterchangeFormatLength
        ],
        next_ifd_offset=0,
    )
    tiff_blob = header + ifd0 + gps_ifd + ifd1 + thumb_jpeg
    app1_payload = b"Exif\x00\x00" + tiff_blob
    app1_segment = b"\xff\xe1" + struct.pack(">H", len(app1_payload) + 2) + app1_payload
    return main_jpeg[:2] + app1_segment + main_jpeg[2:]


def test_image_exif_gps_et_miniature_ne_survivent_pas_au_caviardage():
    """
    Explicitly requested point of vigilance: if the main image is
    redacted but the EXIF thumbnail keeps the original pixels, that is a
    direct leak. Builds an image with a REAL EXIF thumbnail whose content
    (blue) differs from the main image (red) to unambiguously detect any
    survival.
    """
    main_bytes = _make_solid_jpeg(64, RED)
    thumb_bytes = _make_solid_jpeg(32, BLUE)
    raw = _embed_exif_gps_and_thumbnail(main_bytes, thumb_bytes)

    # Preconditions: EXIF, GPS and thumbnail indeed present on input.
    opened = Image.open(io.BytesIO(raw))
    assert "exif" in opened.info
    gps_ifd = opened.getexif().get_ifd(0x8825)
    assert gps_ifd, "invalid precondition: no GPS coordinates in the fixture"
    assert thumb_bytes in raw, "invalid precondition: the thumbnail is not embedded as-is"
    assert raw.count(b"\xff\xd8\xff") == 2, "invalid precondition: two JPEG streams (main + thumbnail) expected"

    job = {"raw_image": raw, "image_format": "JPEG", "detections": [], "theme": ""}
    summary, output_path, manual_count = main._finalize_image_job(
        job, "deadbeefcafebabedeadbeefcafebabe", set(), []
    )
    try:
        out_bytes = output_path.read_bytes()
        reopened = Image.open(io.BytesIO(out_bytes))

        assert "exif" not in reopened.info, "EXIF (hence GPS and the thumbnail) survived redaction"
        assert b"\xff\xe1" not in out_bytes, "a residual APP1/EXIF segment is present in the output file"
        assert out_bytes.count(b"\xff\xd8\xff") == 1, "a second JPEG stream (the thumbnail) is still present"
        assert thumb_bytes not in out_bytes, "the raw thumbnail bytes are still recoverable"
    finally:
        output_path.unlink(missing_ok=True)



# ---------------------------------------------------------------------------
# Final security verification pass — untrusted `theme` form field (audit
# log spoofing + file name overflow) and a RecursionError error-handling
# gap on `manual_zones`. Tests calling the REAL endpoints (async
# functions), not isolated internal functions.
#
# Coroutines are driven manually (`_drive`) rather than via
# `asyncio.run()`: creating a new event loop calls a selector syscall
# (epoll) absent from the service's real enforcing seccomp profile
# (`app-enforce.json`), which would make these tests fail inside the
# hardened container — the rest of the suite never uses asyncio. Neither
# endpoint has a real suspension point (finalize: zero `await`; detect:
# only `await file.read()`, resolved here by a synchronously-read
# upload), so a single `.send(None)` drives them to completion without a
# loop.
# ---------------------------------------------------------------------------
import time as _time  # noqa: E402
import unicodedata  # noqa: E402


def _drive(coro):
    """Runs a coroutine with no real suspension point through to its
    return, without creating an event loop (see section header)."""
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    raise AssertionError("the coroutine did not complete in one step (unexpected real await)")


class _FakeRequest:
    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


class _SyncUpload:
    """Duck-type of UploadFile: detect_document only uses `.filename` and
    `await .read()`. Synchronous read (async def with no await) so as to
    never suspend the calling coroutine."""
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self):
        return self._data


def _seed_csv_job(job_id: str, theme: str = "") -> None:
    with main._PENDING_JOBS_LOCK:
        main.PENDING_JOBS[job_id] = {
            "kind": "csv",
            "csv_text": "nom,ville\nJean,Paris\n",
            "csv_delimiter": ",",
            "detections": [],
            "clusters": {},
            "theme": theme,
            "filename_hash": "deadbeef",
            "user_email": "inconnu",
            "size_mb": 0.01,
            "created_at": _time.time(),
        }


def test_finalize_manual_zones_json_profondement_imbrique_ne_fait_pas_planter():
    """
    A deeply nested JSON array ("[[[[...") of barely ~200 KB (so BELOW
    Starlette's multipart-part limit of 1 MB) exceeds the `json` decoder's
    recursion depth — a RecursionError, not covered by
    `json.JSONDecodeError`, which propagated up to an uncontrolled generic
    500. Must now be treated as an ordinary malformed input: no manual
    zone, finalization completed successfully.
    """
    deep = "[" * 200000
    job_id = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    _seed_csv_job(job_id)
    try:
        resp = _drive(
            main.finalize_document(
                _FakeRequest(), job_id=job_id, excluded_ids="",
                manual_zones=deep, redacted_image_ids="", response_format="json",
            )
        )
        assert resp.status_code == 200
    finally:
        main.PENDING_JOBS.pop(job_id, None)


def test_finalize_manual_zones_json_invalide_ordinaire_reste_tolere():
    """Regression check: a simply invalid JSON is still treated as 'no
    zone', without failing — the new `except` does not tighten this
    already-in-place behavior."""
    job_id = "0011223344556677889900aabbccddee"
    _seed_csv_job(job_id)
    try:
        resp = _drive(
            main.finalize_document(
                _FakeRequest(), job_id=job_id, excluded_ids="",
                manual_zones="{pas du json", redacted_image_ids="", response_format="json",
            )
        )
        assert resp.status_code == 200
    finally:
        main.PENDING_JOBS.pop(job_id, None)


def test_detect_theme_non_fiable_est_assaini_et_borne(monkeypatch):
    """
    The `theme` field is freely controlled by the client. Without
    processing, (a) a Unicode bidirectional formatting character
    (U+202E) passes through intact into the audit log (same spoofing as
    3.5, fixed for `user` but not for `theme`), and (b) a very long value
    overflows the output file name (Errno 36) and makes finalization
    fail with a misleading message. Verifies via the real /api/detect
    then /api/finalize endpoints that the stored value is cleaned and
    bounded, and that the full cycle succeeds.
    """
    monkeypatch.setattr(main, "_analyze_text", lambda text, theme=None: [])
    hostile_theme = "medical‮" + ("a" * 500)
    upload = _SyncUpload("t.csv", b"nom,ville\nJean Dupont,Paris\n")

    detect_resp = _drive(main.detect_document(_FakeRequest(), file=upload, theme=hostile_theme))
    assert detect_resp.status_code == 200

    with main._PENDING_JOBS_LOCK:
        job_id, job = next(iter(main.PENDING_JOBS.items()))
    stored = job["theme"]
    try:
        assert all(unicodedata.category(ch)[0] != "C" for ch in stored), (
            "a Unicode control/format character survived in the stored theme"
        )
        assert len(stored) <= main.MAX_THEME_CHARS, "theme not bounded in length"

        final_resp = _drive(
            main.finalize_document(
                _FakeRequest(), job_id=job_id, excluded_ids="",
                manual_zones="[]", redacted_image_ids="", response_format="json",
            )
        )
        assert final_resp.status_code == 200
        payload = json.loads(bytes(final_resp.body))
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", stored)
        out = main.WORKDIR / f"{payload['job_id']}-{slug}-anonymise.csv"
        assert out.exists(), "output file missing (likely name overflow)"
        out.unlink(missing_ok=True)
    finally:
        main.PENDING_JOBS.pop(job_id, None)


# ---------------------------------------------------------------------------
# Additional security review pass: request size cap, security headers,
# error page escaping, umask.
#
# These tests go through the application's FULL ASGI stack (middlewares,
# routing, FastAPI form parsing, exception handler), not just endpoint
# functions — precisely the level at which the two middlewares operate.
# Manual coroutine driving (see `_drive`), so only paths with no real
# suspension point: multipart form held in memory (< 1 MB, beyond that
# Starlette goes through a thread), async endpoints (/api/detect,
# /api/finalize).
# ---------------------------------------------------------------------------
import os as _os  # noqa: E402
import stat as _stat  # noqa: E402


def _asgi_request(method, path, headers=None, body_chunks=(), content_length=None):
    """Plays an HTTP request through `main.app` (full ASGI stack) and
    returns (status, headers, body, number of body chunks actually
    consumed by the application)."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if content_length is not None:
        raw_headers.append((b"content-length", str(content_length).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": raw_headers,
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
    }
    chunks = list(body_chunks)
    consumed = {"n": 0}

    async def receive():
        i = consumed["n"]
        if i < len(chunks):
            consumed["n"] += 1
            return {"type": "http.request", "body": chunks[i], "more_body": i < len(chunks) - 1}
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    _drive(main.app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    resp_headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], resp_headers, body, consumed["n"]


def _multipart_file_body(payload: bytes, boundary: str = "XBOUNDARYX") -> bytes:
    return (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"f.bin\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
        + payload
        + f"\r\n--{boundary}--\r\n".encode()
    )


_EXPECTED_SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}


def test_upload_content_length_excessif_rejete_413_sans_lire_le_corps():
    """
    A Content-Length beyond the cap must be rejected BEFORE any body
    read: zero chunks consumed. Without this safeguard, the entire body
    was received (tmpfs = container memory) then re-read into memory
    before the MAX_UPLOAD_MB check — a single upload was enough to kill
    the container via OOM (reproduced: 700 MB chunked -> exit 137).
    """
    huge = 200 * 1024 * 1024
    status, headers, body, consumed = _asgi_request(
        "POST", "/api/detect",
        headers={"content-type": "multipart/form-data; boundary=XBOUNDARYX", "accept": "application/json"},
        body_chunks=[b"x" * 1024] * 4, content_length=huge,
    )
    assert status == 413
    assert consumed == 0, "the body must not be read at all"
    assert "Requête trop volumineuse" in json.loads(body)["detail"]
    # The 413 emitted directly by the middleware also carries the
    # security headers (middleware stacking order verified).
    for name, value in _EXPECTED_SECURITY_HEADERS.items():
        assert headers.get(name) == value, f"header {name} missing/incorrect on the 413"


def test_upload_chunke_sans_content_length_interrompu_au_plafond(monkeypatch):
    """
    Without Content-Length (chunked transfer), the cap must apply as
    data is received: reading stops as soon as the cap is exceeded,
    without consuming the rest of the body, and the response is a clean
    413 (not a generic 400 "error parsing the body", nor a 500).
    """
    monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 64 * 1024)
    chunk = b"\0" * (16 * 1024)
    body = _multipart_file_body(b"\0" * (256 * 1024))
    chunks = [body[i:i + len(chunk)] for i in range(0, len(body), len(chunk))]
    status, headers, resp_body, consumed = _asgi_request(
        "POST", "/api/detect",
        headers={"content-type": "multipart/form-data; boundary=XBOUNDARYX", "accept": "application/json"},
        body_chunks=chunks,
    )
    assert status == 413, resp_body[:200]
    assert consumed < len(chunks), "the entire body was consumed despite the cap"
    assert consumed <= 5, f"reading continued well beyond the cap ({consumed} 16 KB chunks)"
    assert headers.get("cache-control") == "no-store"


def test_upload_sous_le_plafond_passe_normalement(monkeypatch):
    """Regression check: an ordinary upload (small CSV, exact
    Content-Length) goes through both middlewares and reaches the review
    page, which carries the security headers — including no-store,
    essential on this page which displays the detected content IN THE
    CLEAR for review."""
    monkeypatch.setattr(main, "_analyze_text", lambda text, theme=None: [])
    body = _multipart_file_body(b"nom,ville\nJean Dupont,Paris\n")
    status, headers, resp_body, consumed = _asgi_request(
        "POST", "/api/detect",
        headers={"content-type": "multipart/form-data; boundary=XBOUNDARYX", "accept": "text/html"},
        body_chunks=[body], content_length=len(body),
    )
    try:
        assert status == 200, resp_body[:300]
        assert consumed == 1
        for name, value in _EXPECTED_SECURITY_HEADERS.items():
            assert headers.get(name) == value, f"header {name} missing/incorrect"
        assert "frame-ancestors 'none'" in headers.get("content-security-policy", "")
        assert "connect-src 'self'" in headers.get("content-security-policy", "")
        assert headers.get("strict-transport-security", "").startswith("max-age=")
    finally:
        with main._PENDING_JOBS_LOCK:
            main.PENDING_JOBS.clear()


def test_reponse_erreur_de_l_application_porte_les_entetes_de_securite():
    """Headers must also cover the error responses produced by
    http_exception_handler (here a 404 from /api/finalize for an unknown
    job)."""
    form = b"job_id=00000000000000000000000000000000"
    status, headers, _, _ = _asgi_request(
        "POST", "/api/finalize",
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "application/json"},
        body_chunks=[form], content_length=len(form),
    )
    assert status == 404
    for name, value in _EXPECTED_SECURITY_HEADERS.items():
        assert headers.get(name) == value, f"header {name} missing/incorrect on the 404"


def test_download_pdf_autorise_le_cadrage_par_sa_propre_page_apercu():
    """
    The "Preview (visual check)" page served by /api/finalize embeds the
    redacted PDF in an <iframe src="/api/download/{job_id}">. The global
    security headers (frame-ancestors 'none' + X-Frame-Options: DENY)
    also applied to this response: the browser then refuses to display
    its OWN response inside its OWN iframe ("Firefox can't open this
    page"), even though the HTTP request itself succeeds (200 in the
    logs — the request goes through and the file is indeed returned,
    only the display is blocked on the browser side afterwards). Only
    the extensions served inline (PDF/PNG/JPG, see _INLINE_EXTENSIONS)
    need frame-ancestors relaxed to 'self' — a third-party site remains
    blocked, as do attachment downloads (.docx/.csv) which keep 'none'.
    """
    # download() is a SYNCHRONOUS endpoint (def, not async def): FastAPI
    # dispatches it via run_in_threadpool, a real suspension point that
    # _drive cannot traverse (see section header) — called directly, not
    # through _asgi_request, like any normal Python function. This
    # therefore only tests the headers set by the route itself: the
    # middleware (_asgi_request already covers it elsewhere, e.g. line
    # ~1043) is responsible for completing the rest (no-store,
    # nosniff...) in production.
    job_id = "ab" * 16
    out = main.WORKDIR / f"{job_id}-medical-anonymise.pdf"
    out.write_bytes(b"%PDF-1.4 fake")
    try:
        resp = main.download(job_id)
        assert resp.status_code == 200
        assert resp.headers.get("x-frame-options") == "SAMEORIGIN", (
            "X-Frame-Options: DENY prevents the preview page from framing its own PDF"
        )
        assert "frame-ancestors 'self'" in resp.headers.get("content-security-policy", ""), (
            "frame-ancestors 'none' prevents the preview page from framing its own PDF"
        )
    finally:
        out.unlink(missing_ok=True)


def test_download_csv_conserve_frame_ancestors_none():
    """Regression check: attachment downloads (no embedded preview, see
    main.py ~3712) have no need to be frameable — the route must not
    override anything, so the middleware can apply the strictest posture
    (frame-ancestors 'none' / DENY) in production."""
    job_id = "cd" * 16
    out = main.WORKDIR / f"{job_id}-compta-anonymise.csv"
    out.write_bytes(b"nom,ville\n")
    try:
        resp = main.download(job_id)
        assert resp.status_code == 200
        assert "x-frame-options" not in resp.headers
        assert "content-security-policy" not in resp.headers
    finally:
        out.unlink(missing_ok=True)


def test_page_erreur_html_echappe_le_detail():
    """
    http_exception_handler injected `exc.detail` as-is into an HTML page.
    At least one detail contains a value of external origin (threat name
    reported by the ICAP server, audit section 9/10): sanitized of
    Unicode control characters, but not of HTML tags. The detail must be
    escaped — regardless of its origin, present or future.
    """
    hostile = "<script>alert(1)</script>"
    resp = _drive(main.http_exception_handler(
        _FakeRequest({"accept": "text/html"}), HTTPException(status_code=400, detail=hostile)
    ))
    page = bytes(resp.body).decode("utf-8")
    assert hostile not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_fichiers_crees_par_le_service_ne_sont_pas_lisibles_par_les_autres():
    """
    Documents in transit in WORKDIR and the audit log must only be
    readable by the service's user (umask 077 set when main is
    imported) — the host-side bind mount would otherwise expose them as
    644 to any local user.
    """
    probe = main.WORKDIR / "umask-probe-test.tmp"
    try:
        with open(probe, "w") as f:
            f.write("x")
        mode = _stat.S_IMODE(_os.stat(probe).st_mode)
        assert mode & 0o077 == 0, f"file created with mode {oct(mode)}: readable by group/others"
    finally:
        probe.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Ownership check on /api/preview_image and /api/finalize
# ---------------------------------------------------------------------------

def _seed_image_job(job_id: str, user: str) -> None:
    with main._PENDING_JOBS_LOCK:
        main.PENDING_JOBS[job_id] = {
            "kind": "image", "image_format": "PNG", "raw_image": _make_solid_png(8, (255, 0, 0)),
            "detections": [], "clusters": {}, "theme": "", "filename_hash": "deadbeef",
            "user_email": user, "size_mb": 0.01, "created_at": _time.time(),
        }


def test_preview_image_refuse_le_job_d_un_autre_utilisateur():
    """The preview renders the ORIGINAL document: another authenticated
    user who knows the job_id must get a 404 indistinguishable from a
    nonexistent job, and the job must remain intact for its owner."""
    job_id = "11111111222222223333333344444444"
    _seed_image_job(job_id, "alice@hopital.fr")
    try:
        with pytest.raises(HTTPException) as exc:
            main.preview_image(job_id, 0, _FakeRequest({"x-auth-request-email": "mallory@hopital.fr"}))
        assert exc.value.status_code == 404
        assert job_id in main.PENDING_JOBS, "the owner's job was removed by a third party's attempt"
        resp = main.preview_image(job_id, 0, _FakeRequest({"x-auth-request-email": "alice@hopital.fr"}))
        assert resp.status_code == 200 and resp.media_type == "image/png"
    finally:
        main.PENDING_JOBS.pop(job_id, None)


def test_finalize_refuse_le_job_d_un_autre_utilisateur_sans_le_detruire():
    """A third party must neither finalize someone else's job, nor make
    it disappear from the queue by merely attempting to; the owner then
    finalizes normally afterwards."""
    job_id = "55555555666666667777777788888888"
    _seed_csv_job(job_id)
    with main._PENDING_JOBS_LOCK:
        main.PENDING_JOBS[job_id]["user_email"] = "alice@hopital.fr"
    try:
        with pytest.raises(HTTPException) as exc:
            _drive(main.finalize_document(
                _FakeRequest({"x-auth-request-email": "mallory@hopital.fr"}), job_id=job_id,
                excluded_ids="", manual_zones="[]", redacted_image_ids="", response_format="json",
            ))
        assert exc.value.status_code == 404
        assert job_id in main.PENDING_JOBS, "the job was destroyed by a third party's attempt"

        resp = _drive(main.finalize_document(
            _FakeRequest({"x-auth-request-email": "alice@hopital.fr"}), job_id=job_id,
            excluded_ids="", manual_zones="[]", redacted_image_ids="", response_format="json",
        ))
        assert resp.status_code == 200
        assert job_id not in main.PENDING_JOBS
        for out in main.WORKDIR.glob(f"{job_id}-*-anonymise.*"):
            out.unlink(missing_ok=True)
    finally:
        main.PENDING_JOBS.pop(job_id, None)


def test_identite_comparee_apres_le_meme_assainissement_qu_a_la_creation():
    """The header is sanitized when the job is created (3.5); the
    comparison must apply the same processing, otherwise a legitimate
    owner whose header contained a formatting character would be
    rejected."""
    job_id = "99999999aaaaaaaabbbbbbbbcccccccc"
    _seed_image_job(job_id, "alice@hopital.fr")
    try:
        resp = main.preview_image(job_id, 0, _FakeRequest({"x-auth-request-email": "alice‮@hopital.fr"}))
        assert resp.status_code == 200
    finally:
        main.PENDING_JOBS.pop(job_id, None)


# ---------------------------------------------------------------------------
# Section 3.7: shared gateway secret (Traefik -> app).
#
# The bypass (a neighboring container on the same Docker network joins
# `app` directly, outside Traefik, and forges X-Auth-Request-Email) was
# empirically confirmed. `_GatewaySecretMiddleware` requires, before any
# processing, a secret that only Traefik knows and injects. These tests
# go through the FULL ASGI stack via `_asgi_request`, exactly the level
# at which the middleware operates. The secret is overridden via a
# module attribute (read on every request).
# ---------------------------------------------------------------------------
_GATEWAY_TEST_SECRET = "s3cr3t-passerelle-de-test"


def test_gateway_requete_sans_le_secret_rejetee_401(monkeypatch):
    """A caller who did not go through Traefik (hence without the
    injected secret) is rejected with 401 before even reaching the code
    that reads X-Auth-Request-Email — this is precisely the Phase 1
    bypass."""
    monkeypatch.setattr(main, "GATEWAY_SECRET", _GATEWAY_TEST_SECRET)
    status, _headers, _body, consumed = _asgi_request(
        "POST", "/api/detect",
        headers={"x-auth-request-email": "admin@usurpe.fr"},
    )
    assert status == 401
    assert consumed == 0, "the body must not even start being read for a gateway 401"


def test_gateway_requete_secret_incorrect_rejetee_401(monkeypatch):
    """A present but wrong secret is rejected (constant-time comparison)."""
    monkeypatch.setattr(main, "GATEWAY_SECRET", _GATEWAY_TEST_SECRET)
    status, _headers, _body, _consumed = _asgi_request(
        "GET", "/",
        headers={"x-internal-gateway-secret": "mauvais-secret"},
    )
    assert status == 401


def _drive_gateway(path, headers=None, secret=_GATEWAY_TEST_SECRET, monkeypatch=None):
    """Drives `_GatewaySecretMiddleware` alone, around a trivial inner
    app (a 200 response in one step). Isolates the middleware's logic
    from FastAPI routing (synchronous endpoints like `/` or `/health` go
    through a threadpool that `_drive` cannot traverse). Returns
    (status, inner_app_reached)."""
    if monkeypatch is not None:
        monkeypatch.setattr(main, "GATEWAY_SECRET", secret)
    reached = {"v": False}

    async def inner(scope, receive, send):
        reached["v"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"inner-ok"})

    mw = main._GatewaySecretMiddleware(inner)
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "path": path, "headers": raw_headers,
             "method": "GET", "query_string": b""}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    _drive(mw(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, reached["v"]


def test_gateway_requete_secret_correct_passe(monkeypatch):
    """The legitimate path (Traefik injects the correct secret) passes
    through the middleware to the inner app: no degradation for a
    normal user."""
    status, reached = _drive_gateway(
        "/api/detect", {"x-internal-gateway-secret": _GATEWAY_TEST_SECRET}, monkeypatch=monkeypatch,
    )
    assert status == 200
    assert reached is True


def test_gateway_health_exempte_meme_sans_secret(monkeypatch):
    """/health remains accessible without the secret: any Docker
    HEALTHCHECK queries the container over loopback, not via Traefik."""
    status, reached = _drive_gateway("/health", headers={}, monkeypatch=monkeypatch)
    assert status == 200
    assert reached is True, "/health must reach the inner app even without the secret"


def test_gateway_desactive_si_aucun_secret_configure(monkeypatch):
    """Without a configured secret (dev/test, no /run/secrets mounted),
    the middleware is a no-op: compatibility of the rest of the suite is
    preserved."""
    status, reached = _drive_gateway("/api/detect", headers={}, secret="", monkeypatch=monkeypatch)
    assert status == 200
    assert reached is True


def test_gateway_401_enveloppe_par_les_entetes_de_securite(monkeypatch):
    """Even a gateway 401 carries the security headers (the headers
    middleware remains the outermost one)."""
    monkeypatch.setattr(main, "GATEWAY_SECRET", _GATEWAY_TEST_SECRET)
    status, headers, _body, _consumed = _asgi_request("GET", "/")
    assert status == 401
    assert headers.get("cache-control") == "no-store"
    assert headers.get("x-content-type-options") == "nosniff"


# ---------------------------------------------------------------------------
# i18n (app/i18n/*.json) — loading, fallback, UI_LANG
# ---------------------------------------------------------------------------

def test_i18n_fr_json_loads_correctly():
    """The real app/i18n/fr.json loads and is used as-is when UI_LANG=fr
    (the default, unless the test environment overrides it)."""
    strings = main._load_strings()
    assert strings["analyze_button"] == "Analyser"
    assert strings["home_page_title"] == "Obfusk8 - Anonymiseur de documents"


def test_i18n_en_json_loads_correctly(monkeypatch):
    """The real app/i18n/en.json loads correctly under UI_LANG=en."""
    monkeypatch.setattr(main, "UI_LANG", "en")
    strings = main._load_strings()
    assert strings["analyze_button"] == "Analyze"
    assert strings["home_page_title"] == "Obfusk8 - Document Anonymizer"


def test_i18n_fr_et_en_ont_exactement_les_memes_cles():
    """fr.json is the mandatory fallback base (see _load_strings): a key
    present in en.json but missing from fr.json would never be reachable
    as a fallback target, and a key missing from en.json falls back
    silently — but both files should stay in sync so nothing is
    accidentally left untranslated forever."""
    fr = json.loads((main.I18N_DIR / "fr.json").read_text(encoding="utf-8"))
    en = json.loads((main.I18N_DIR / "en.json").read_text(encoding="utf-8"))
    assert fr.keys() == en.keys()


def test_i18n_cle_manquante_dans_la_langue_cible_replie_sur_le_francais(tmp_path, monkeypatch):
    i18n_dir = tmp_path / "i18n"
    i18n_dir.mkdir()
    (i18n_dir / "fr.json").write_text(json.dumps({"a": "Bonjour", "b": "Salut"}), encoding="utf-8")
    (i18n_dir / "xx.json").write_text(json.dumps({"a": "Hello"}), encoding="utf-8")  # "b" missing
    monkeypatch.setattr(main, "I18N_DIR", i18n_dir)
    monkeypatch.setattr(main, "UI_LANG", "xx")

    strings = main._load_strings()

    assert strings["a"] == "Hello"
    assert strings["b"] == "Salut"  # silent fallback, never a raw key or empty text


def test_i18n_langue_inconnue_replie_entierement_sur_le_francais(tmp_path, monkeypatch):
    i18n_dir = tmp_path / "i18n"
    i18n_dir.mkdir()
    (i18n_dir / "fr.json").write_text(json.dumps({"a": "Bonjour"}), encoding="utf-8")
    monkeypatch.setattr(main, "I18N_DIR", i18n_dir)
    monkeypatch.setattr(main, "UI_LANG", "zz")  # zz.json does not exist at all

    strings = main._load_strings()

    assert strings == {"a": "Bonjour"}


def test_i18n_fichier_langue_illisible_replie_entierement_sur_le_francais(tmp_path, monkeypatch):
    """A malformed JSON file for the target language must never crash the
    application (same fail-safe logic as the rest of the project)."""
    i18n_dir = tmp_path / "i18n"
    i18n_dir.mkdir()
    (i18n_dir / "fr.json").write_text(json.dumps({"a": "Bonjour"}), encoding="utf-8")
    (i18n_dir / "xx.json").write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(main, "I18N_DIR", i18n_dir)
    monkeypatch.setattr(main, "UI_LANG", "xx")

    strings = main._load_strings()

    assert strings == {"a": "Bonjour"}


def test_i18n_theme_labels_chargent_et_replient_sur_le_francais(tmp_path, monkeypatch):
    i18n_dir = tmp_path / "i18n"
    i18n_dir.mkdir()
    (i18n_dir / "themes.json").write_text(
        json.dumps({"compta": {"fr": "Comptabilité", "en": "Accounting"}, "it": {"fr": "IT"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "I18N_DIR", i18n_dir)

    monkeypatch.setattr(main, "UI_LANG", "en")
    labels = main._load_theme_labels()
    assert labels["compta"] == "Accounting"
    assert labels["it"] == "IT"  # missing "en" entry -> falls back to "fr"


def test_i18n_page_accueil_texte_different_selon_ui_lang(monkeypatch):
    """End-to-end check requested by the audit: the home page
    (formulaire d'accueil) must actually render different text for
    UI_LANG=fr vs UI_LANG=en, using the real i18n files."""
    html_fr = main.upload_form()
    assert "Analyser" in html_fr
    assert 'lang="fr"' in html_fr
    assert "Analyze" not in html_fr

    en_strings = json.loads((main.I18N_DIR / "en.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(main, "STRINGS", en_strings)
    monkeypatch.setattr(main, "UI_LANG", "en")

    html_en = main.upload_form()
    assert "Analyze" in html_en
    assert 'lang="en"' in html_en
    assert html_en != html_fr
