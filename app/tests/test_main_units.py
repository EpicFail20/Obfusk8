"""
Tests unitaires — ne nécessitent AUCUN service externe (pas de Presidio, pas
de Traefik, pas de Keycloak). Rapides, à lancer à chaque modification de
main.py ou d'un fichier de thème.

Exécution : depuis /app dans le conteneur, `pytest` ou `pytest tests/ -v`.
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
        ("N°123", "N°123"),  # une seule lettre majuscule : jamais touchée
    ],
)
def test_normalize_allcaps(text, expected):
    assert main._normalize_allcaps(text) == expected


# ---------------------------------------------------------------------------
# Regex des thèmes — exemples qui DOIVENT matcher / ne doivent PAS matcher
# ---------------------------------------------------------------------------

def _get_pattern_regex(theme_key: str, pattern_name: str) -> str:
    """Retrouve le regex d'un pattern précis dans un thème réellement chargé
    (source unique de vérité : le même JSON que celui envoyé à Presidio)."""
    theme = main.THEMES[theme_key]
    for recognizer in theme["ad_hoc_recognizers"]:
        for pattern in recognizer["patterns"]:
            if pattern["name"] == pattern_name:
                return pattern["regex"]
    raise KeyError(f"Pattern {pattern_name!r} introuvable dans le thème {theme_key!r}")


IEP_PATTERN_NAME = "IEP (zéro(s) en tête + 8 chiffres min)"

IEP_SHOULD_MATCH = [
    "IEP: 00123456",     # 8 chiffres pile, 1 zéro en tête — cas limite
    "IEP 000456789",     # 9 chiffres, 3 zéros en tête
    "iep:0012345678",    # casse basse, sans espace
]
IEP_SHOULD_NOT_MATCH = [
    "IEP: 12345678",     # pas de zéro en tête
    "IEP: 0012345",      # 7 chiffres seulement (8 attendus minimum)
]


@pytest.mark.parametrize("text", IEP_SHOULD_MATCH)
def test_iep_pattern_matches_valid_examples(text):
    regex = _get_pattern_regex("medical", IEP_PATTERN_NAME)
    assert re.search(regex, text) is not None, f"aurait dû matcher: {text!r}"


@pytest.mark.parametrize("text", IEP_SHOULD_NOT_MATCH)
def test_iep_pattern_rejects_invalid_examples(text):
    regex = _get_pattern_regex("medical", IEP_PATTERN_NAME)
    assert re.search(regex, text) is None, f"n'aurait pas dû matcher: {text!r}"


# ---------------------------------------------------------------------------
# _cluster_detections — fusion des zones superposées (bug corrigé ce soir)
# ---------------------------------------------------------------------------

def test_cluster_detections_merges_overlapping_rects():
    """Deux détections quasi au même endroit (ex: NER générique + pattern
    personnalisé sur le même nom) doivent fusionner en une seule zone
    cliquable, sinon un clic n'en exclut qu'une et l'autre reste caviardée."""
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
    """Deux détections clairement à des endroits différents ne doivent
    jamais être fusionnées à tort."""
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
# _check_page_images_sane — défense en profondeur CVE-2026-3308 (dimensions
# d'image PDF absurdes avant tout appel à page.get_pixmap()). Un simple objet
# factice avec get_images(full=True) suffit : la fonction ne lit que les
# indices 2 (largeur) et 3 (hauteur) du tuple, comme le fait PyMuPDF pour
# chaque image incrustée référencée par une page.
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
    main._check_page_images_sane(page)  # ne doit pas lever


def test_check_page_images_sane_allows_page_without_images():
    page = _FakePage([])
    main._check_page_images_sane(page)  # ne doit pas lever


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
# _send_alert / _run_antivirus_scan — une alerte défaillante ne doit jamais
# casser le flux principal qu'elle surveille (revue de sécurité, section 10
# de plan_audit_consolide.md, même principe que la revue antivirus/ICAP)
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
    main._send_alert(main.Alert(severity=main.AlertSeverity.WARNING, source="test", message="x"))  # ne doit pas lever


def test_antivirus_indisponible_renvoie_503_meme_si_alerte_echoue(monkeypatch):
    """Avant correctif : une exception dans get_alert_sink()/.send() empêchait
    d'atteindre le HTTPException 503 attendu, remontant un 500 générique à la
    place — masquant la vraie cause (antivirus indisponible)."""
    monkeypatch.setattr(main, "get_scanner", lambda: (_ for _ in ()).throw(main.AntivirusUnavailableError("down")))
    monkeypatch.setattr(main, "get_alert_sink", _raising_alert_sink)
    monkeypatch.delenv("AV_ENFORCE", raising=False)  # défaut = bloquant

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
    """Même risque de spoofing visuel par caractère de formatage Unicode
    (RTL override...) que celui trouvé et corrigé sur le journal d'audit
    (3.5) — threat_name vient du serveur ICAP, pas du fichier uploadé, mais
    assaini par précaution avant de rejoindre la réponse HTTP et le journal
    syslog."""
    threat_with_rtl_override = "Trojan‮exe.pdf"
    monkeypatch.setattr(main, "get_scanner", lambda: _FakeScanner(is_clean=False, threat_name=threat_with_rtl_override))
    monkeypatch.setattr(main, "get_alert_sink", _raising_alert_sink)
    monkeypatch.delenv("AV_ENFORCE", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        main._run_antivirus_scan(b"raw", "fichier.pdf", "abc123")
    assert "‮" not in exc_info.value.detail


# ---------------------------------------------------------------------------
# Caviardage manuel d'une image PDF (_apply_manual_redactions) — la zone
# tracée à la main sur une image doit être réellement irrécupérable dans le
# fichier de sortie, pas seulement masquée visuellement. Promu en test
# permanent (auparavant vérifié uniquement via un script jetable, voir
# app/tests/fixtures/verification_scripts/probe_image_redaction.py et
# plan_audit_consolide.md section 8) : exerce le VRAI point d'entrée du
# projet plutôt qu'une réimplémentation du mécanisme PyMuPDF.
# ---------------------------------------------------------------------------

RED = (255, 0, 0)
BLUE = (0, 0, 255)


def _build_two_color_pdf() -> bytes:
    """PDF à une page avec une image 200x200 : moitié haute rouge (à
    caviarder), moitié basse bleue (à conserver telle quelle)."""
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
    """Balaie TOUS les objets image du document (pas seulement ceux
    atteignables depuis l'arbre de pages courant) — même méthode que celle
    qui avait révélé la fuite d'objets orphelins post-caviardage (section 7)."""
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

    # Zone manuelle en coordonnées d'aperçu (display_rect = coordonnées PDF *
    # PREVIEW_ZOOM), couvrant uniquement la moitié ROUGE de l'image insérée.
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
    assert not _color_present_in_any_image_object(out_bytes, RED), "le rouge caviardé est encore récupérable"
    assert _color_present_in_any_image_object(out_bytes, BLUE), "le bleu non caviardé a disparu à tort"


# ---------------------------------------------------------------------------
# Caviardage manuel d'une image DOCX entière (_apply_docx_image_redactions) —
# comble le trou identifié lors de la revue précédente (aucun mécanisme
# n'existait pour les images DOCX). Granularité "image entière" et non "zone
# pixel précise" comme pour le PDF : DOCX n'a pas de mise en page fixe en
# coordonnées sans moteur de rendu complet.
# ---------------------------------------------------------------------------

def _make_solid_png(size: int, rgb: tuple) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, size, size))
    pix.set_rect(pix.irect, rgb)
    return pix.tobytes("png")


def _docx_media_pixel_colors(raw_docx: bytes) -> list:
    """Couleur du pixel (0,0) de chaque image trouvée dans word/media/ du zip
    de sortie — balayage direct du zip plutôt que du graphe de relations
    python-docx, pour vérifier qu'aucune entrée orpheline ne subsiste non
    plus (contrairement au PDF, une sauvegarde DOCX/OPC réécrit toujours
    l'intégralité du paquet depuis le graphe vivant, mais autant vérifier
    plutôt que supposer)."""
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

    assert (255, 0, 0) not in out_colors, "le rouge caviardé est encore récupérable dans le zip de sortie"
    assert (0, 0, 255) in out_colors, "le bleu non caviardé a disparu à tort"
    assert (0, 0, 0) in out_colors, "le carré noir de remplacement est absent"


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
    """Même pour un format non réencodable à l'identique (gif...), le
    placeholder produit (PNG malgré tout) doit rester une image valide."""
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
    assert "base64," in html_out  # petite image : aperçu intégré


def test_build_docx_images_review_section_grosse_image_sans_apercu(monkeypatch):
    monkeypatch.setattr(main, "MAX_DOCX_IMAGE_PREVIEW_BYTES", 10)  # force le seuil à être dépassé
    doc = WordDocument()
    doc.add_picture(io.BytesIO(_make_solid_png(20, RED)))
    buf = io.BytesIO()
    doc.save(buf)
    reopened = WordDocument(io.BytesIO(buf.getvalue()))

    html_out = main._build_docx_images_review_section(reopened)
    assert "Aperçu indisponible" in html_out
    assert 'class="docx-image-checkbox"' in html_out  # reste sélectionnable malgré tout


def test_build_docx_images_review_section_vide_si_aucune_image():
    doc = WordDocument()
    doc.add_paragraph("aucune image ici")
    buf = io.BytesIO()
    doc.save(buf)
    reopened = WordDocument(io.BytesIO(buf.getvalue()))

    assert main._build_docx_images_review_section(reopened) == ""


class _FakeImagePart:
    """content_type et partname viennent du fichier .docx uploadé (déclarés
    dans [Content_Types].xml / les cibles de relation) — donc contrôlables
    par un attaquant. Vérifie qu'un payload XSS dans l'un ou l'autre ne
    survit pas tel quel dans la page de révision rendue au navigateur."""
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
# Support image (PNG/JPEG) - validation d'entrée, OCR, caviardage, métadonnées
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
    # Mode "1" (bilevel) : ~8 Mo pour 8000x8000 = 64 000 000 pixels, très
    # au-dessus de MAX_IMAGE_PIXELS (40 000 000 par défaut) — vérifie que le
    # rejet se fait bien sur les dimensions déclarées, sans jamais décoder
    # les pixels d'une vraie image de cette taille.
    img = Image.new("1", (8000, 8000))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    with pytest.raises(HTTPException) as exc_info:
        main._open_and_validate_image(buf.getvalue())
    assert exc_info.value.status_code == 400


def test_open_and_validate_image_rejette_bombe_au_dela_du_double_du_seuil():
    # Au-delà de 2x MAX_IMAGE_PIXELS, Pillow lève lui-même
    # DecompressionBombError depuis Image.open() — vérifie que cette
    # exception interne est bien convertie en rejet propre (400), pas une
    # trace brute qui remonterait telle quelle.
    img = Image.new("1", (13000, 13000))  # 169 000 000 pixels
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
    truncated = raw[:-30]  # en-tête (IHDR) intact, données IDAT tronquées

    # L'en-tête reste lisible : Image.open() (Phase 1) ne détecte pas encore
    # le problème, seul un décodage complet (.load(), comme fait par
    # _handle_detect_image après validation) le révèle — reproduit le
    # chemin réel plutôt qu'un raccourci de test.
    img = main._open_and_validate_image(truncated)
    with pytest.raises(OSError):
        img.load()


def test_detect_image_sans_texte_renvoie_liste_vide():
    # Image blanche unie : aucun mot OCR trouvé -> aucune erreur, liste
    # vide directement (ne doit jamais appeler Presidio dans ce cas).
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    assert main._detect_image(img) == []


def test_tesseract_cmd_est_un_chemin_absolu_pas_une_recherche_path():
    # Défense en profondeur contre un détournement de $PATH (voir main.py) :
    # jamais la valeur par défaut de pytesseract ("tesseract" seul).
    assert main.pytesseract.pytesseract.tesseract_cmd == "/usr/bin/tesseract"


def test_run_ocr_sous_processus_reellement_tue_au_depassement_du_timeout(monkeypatch):
    """
    Vérifie le VRAI sous-processus tesseract (pas un mock) : avec un timeout
    absurdement court, le sous-processus doit être terminé proprement
    (SIGTERM/SIGKILL côté pytesseract, voir sa fonction kill()) et l'appelant
    doit recevoir une erreur propre — jamais une requête bloquée
    indéfiniment, jamais une trace brute. Reproduit le scénario qui a motivé
    l'ajout de MAX_OCR_SECONDS : un `subprocess.Popen` sans borne de temps
    peut tourner indéfiniment sur une image pathologique.
    """
    monkeypatch.setattr(main, "MAX_OCR_SECONDS", 0.001)
    img = Image.new("RGB", (200, 200), (255, 255, 255))

    with pytest.raises(HTTPException) as exc_info:
        main._run_ocr(img)
    assert exc_info.value.status_code == 400
    assert "temps imparti" in exc_info.value.detail


def test_run_ocr_timeout_error_distinct_de_tesseract_error(monkeypatch):
    """`TesseractError` hérite de `RuntimeError` — vérifie que le except
    plus spécifique intercepte bien en premier (pas de faux message de
    timeout pour une vraie erreur moteur)."""
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

    assert len(detections) == 2  # un rectangle par mot recouvert
    assert all(d["entity_type"] == "PERSON" for d in detections)
    assert all(d["page"] == 0 for d in detections)
    rects = sorted(d["page_rect"] for d in detections)
    assert rects == [[0.0, 0.0, 30.0, 10.0], [35.0, 0.0, 75.0, 10.0]]
    # "habite" (hors de l'empan [0,11)) ne doit produire aucune détection
    assert all(d["page_rect"][0] < 76 for d in detections)


def test_zone_manuelle_image_est_irrecuperable():
    """Même exigence que pour le PDF (voir
    test_zone_manuelle_sur_image_pdf_est_irrecuperable) : une zone tracée
    manuellement sur UNE moitié d'une image doit rendre cette moitié
    irrécupérable au niveau des pixels, sans toucher à l'autre moitié."""
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
    assert out_img.getpixel((10, 10)) == (0, 0, 0), "la zone rouge caviardée n'est pas devenue noire"
    assert out_img.getpixel((W - 10, 10)) == BLUE, "le bleu non caviardé a été altéré à tort"
    # Balayage exhaustif : aucun pixel rouge résiduel nulle part dans le fichier de sortie.
    assert RED not in out_img.get_flattened_data(), "le rouge caviardé est encore récupérable quelque part dans l'image"


def test_strip_image_metadata_removes_png_text_chunks():
    img = Image.new("RGB", (20, 20), RED)
    pnginfo = PngInfo()
    pnginfo.add_text("Comment", "donnee-sensible")
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=pnginfo)
    reopened = Image.open(io.BytesIO(buf.getvalue()))
    assert "Comment" in reopened.text  # précondition : le chunk est bien présent

    out_bytes = main._strip_image_metadata_and_encode(reopened, "PNG")
    out_img = Image.open(io.BytesIO(out_bytes))
    assert not getattr(out_img, "text", {}), "un chunk de texte PNG a survécu au dépouillement"
    assert b"donnee-sensible" not in out_bytes


def test_strip_image_metadata_removes_icc_profile():
    img = Image.new("RGB", (20, 20), RED)
    buf = io.BytesIO()
    img.save(buf, format="PNG", icc_profile=b"FAKE-ICC-PROFILE-DATA")
    reopened = Image.open(io.BytesIO(buf.getvalue()))
    assert "icc_profile" in reopened.info  # précondition

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
    Construit à la main un segment APP1/EXIF complet (coordonnées GPS +
    miniature JPEG intégrée dans l'IFD1, comme le ferait un vrai appareil
    photo/smartphone) et l'insère juste après le marqueur SOI du JPEG
    principal — reproduit fidèlement la structure EXIF réelle plutôt qu'une
    approximation, pour que le test de non-fuite (voir
    test_image_exif_gps_et_miniature_ne_survivent_pas_au_caviardage) porte
    sur un cas réaliste.
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
    Point de vigilance explicitement demandé : si l'image principale est
    caviardée mais que la miniature EXIF garde les pixels d'origine, c'est
    une fuite directe. Construit une image avec une VRAIE miniature EXIF de
    contenu différent (bleu) de l'image principale (rouge) pour détecter
    sans ambiguïté toute survivance.
    """
    main_bytes = _make_solid_jpeg(64, RED)
    thumb_bytes = _make_solid_jpeg(32, BLUE)
    raw = _embed_exif_gps_and_thumbnail(main_bytes, thumb_bytes)

    # Préconditions : EXIF, GPS et miniature bien présents en entrée.
    opened = Image.open(io.BytesIO(raw))
    assert "exif" in opened.info
    gps_ifd = opened.getexif().get_ifd(0x8825)
    assert gps_ifd, "précondition invalide : pas de coordonnées GPS dans le fixture"
    assert thumb_bytes in raw, "précondition invalide : la miniature n'est pas embarquée telle quelle"
    assert raw.count(b"\xff\xd8\xff") == 2, "précondition invalide : deux flux JPEG (principal + miniature) attendus"

    job = {"raw_image": raw, "image_format": "JPEG", "detections": [], "theme": ""}
    summary, output_path, manual_count = main._finalize_image_job(
        job, "deadbeefcafebabedeadbeefcafebabe", set(), []
    )
    try:
        out_bytes = output_path.read_bytes()
        reopened = Image.open(io.BytesIO(out_bytes))

        assert "exif" not in reopened.info, "l'EXIF (donc le GPS et la miniature) a survécu au caviardage"
        assert b"\xff\xe1" not in out_bytes, "un segment APP1/EXIF résiduel est présent dans le fichier de sortie"
        assert out_bytes.count(b"\xff\xd8\xff") == 1, "un second flux JPEG (la miniature) est encore présent"
        assert thumb_bytes not in out_bytes, "les octets bruts de la miniature sont encore récupérables"
    finally:
        output_path.unlink(missing_ok=True)



# ---------------------------------------------------------------------------
# Passe de vérification de sécurité finale — champ de formulaire `theme` non
# fiable (spoofing du journal d'audit + débordement du nom de fichier) et gap
# de gestion d'erreur RecursionError sur `manual_zones`. Tests appelant les
# VRAIS endpoints (fonctions asynchrones), pas des fonctions internes isolées.
#
# Les coroutines sont pilotées manuellement (`_drive`) plutôt que via
# `asyncio.run()` : créer une nouvelle boucle d'événements appelle un syscall
# de sélecteur (epoll) absent du profil seccomp de blocage réel du service
# (`app-enforce.json`), ce qui ferait échouer ces tests dans le conteneur
# durci — le reste de la suite n'utilise jamais asyncio. Les deux endpoints
# n'ont aucun point de suspension réel (finalize : zéro `await` ; detect :
# seulement `await file.read()`, résolu ici par un upload à lecture
# synchrone), donc un unique `.send(None)` les mène à terme sans boucle.
# ---------------------------------------------------------------------------
import time as _time  # noqa: E402
import unicodedata  # noqa: E402


def _drive(coro):
    """Exécute une coroutine sans point de suspension réel jusqu'à son retour,
    sans créer de boucle d'événements (voir en-tête de section)."""
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    raise AssertionError("la coroutine ne s'est pas terminée en une étape (await réel inattendu)")


class _FakeRequest:
    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


class _SyncUpload:
    """Duck-type d'UploadFile : detect_document n'utilise que `.filename` et
    `await .read()`. Lecture synchrone (async def sans await) pour ne jamais
    suspendre la coroutine appelante."""
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
    Un tableau JSON profondément imbriqué ("[[[[...") d'à peine ~200 Ko (donc
    SOUS la limite de partie multipart de Starlette, 1 Mo) fait dépasser la
    profondeur de récursion du décodeur `json` — une RecursionError, non
    couverte par `json.JSONDecodeError`, qui remontait jusqu'à un 500
    générique non maîtrisé. Doit désormais être traitée comme une entrée
    malformée ordinaire : aucune zone manuelle, finalisation menée à bien.
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
    """Non-régression : un JSON simplement invalide reste traité comme
    'aucune zone', sans échec — le nouveau `except` ne resserre pas ce
    comportement déjà en place."""
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
    Le champ `theme` est librement contrôlé par le client. Sans traitement,
    (a) un caractère de formatage bidirectionnel Unicode (U+202E) y passe
    intact jusqu'au journal d'audit (même spoofing que 3.5, corrigé pour
    `user` mais pas pour `theme`), et (b) une valeur très longue déborde le
    nom du fichier de sortie (Errno 36) et fait échouer la finalisation avec
    un message trompeur. Vérifie via les vrais endpoints /api/detect puis
    /api/finalize que la valeur stockée est nettoyée et bornée, et que le
    cycle complet aboutit.
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
            "un caractère de contrôle/format Unicode a survécu dans le theme stocké"
        )
        assert len(stored) <= main.MAX_THEME_CHARS, "theme non borné en longueur"

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
        assert out.exists(), "fichier de sortie absent (débordement de nom probable)"
        out.unlink(missing_ok=True)
    finally:
        main.PENDING_JOBS.pop(job_id, None)


# ---------------------------------------------------------------------------
# Passe de revue de sécurité complémentaire : plafond de taille de requête,
# en-têtes de sécurité, échappement de la page d'erreur, umask.
#
# Ces tests traversent la pile ASGI COMPLÈTE de l'application (middlewares,
# routage, parsing de formulaire FastAPI, gestionnaire d'exception), pas
# seulement les fonctions d'endpoint — c'est précisément le niveau où les
# deux middlewares agissent. Pilotage manuel de la coroutine (voir `_drive`),
# donc uniquement des chemins sans point de suspension réel : formulaire
# multipart tenu en mémoire (< 1 Mo, au-delà Starlette passe par un thread),
# endpoints asynchrones (/api/detect, /api/finalize).
# ---------------------------------------------------------------------------
import os as _os  # noqa: E402
import stat as _stat  # noqa: E402


def _asgi_request(method, path, headers=None, body_chunks=(), content_length=None):
    """Joue une requête HTTP à travers `main.app` (pile ASGI complète) et
    renvoie (statut, en-têtes, corps, nombre de fragments de corps
    réellement consommés par l'application)."""
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
    Un Content-Length au-delà du plafond doit être rejeté AVANT toute lecture
    du corps : zéro fragment consommé. Sans ce garde-fou, l'intégralité du
    corps était reçue (tmpfs = mémoire du conteneur) puis relue en mémoire
    avant le contrôle MAX_UPLOAD_MB — un seul upload suffisait à tuer le
    conteneur par OOM (reproduit : 700 Mo chunkés → exit 137).
    """
    huge = 200 * 1024 * 1024
    status, headers, body, consumed = _asgi_request(
        "POST", "/api/detect",
        headers={"content-type": "multipart/form-data; boundary=XBOUNDARYX", "accept": "application/json"},
        body_chunks=[b"x" * 1024] * 4, content_length=huge,
    )
    assert status == 413
    assert consumed == 0, "le corps ne doit pas être lu du tout"
    assert "Requête trop volumineuse" in json.loads(body)["detail"]
    # Le 413 émis directement par le middleware porte lui aussi les en-têtes
    # de sécurité (ordre d'empilement des middlewares vérifié).
    for name, value in _EXPECTED_SECURITY_HEADERS.items():
        assert headers.get(name) == value, f"en-tête {name} absent/incorrect sur le 413"


def test_upload_chunke_sans_content_length_interrompu_au_plafond(monkeypatch):
    """
    Sans Content-Length (transfert chunké), le plafond doit s'appliquer au fil
    de la réception : la lecture s'arrête dès le dépassement, sans consommer
    le reste du corps, et la réponse est un 413 propre (pas un 400 générique
    « error parsing the body », ni un 500).
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
    assert consumed < len(chunks), "tout le corps a été consommé malgré le plafond"
    assert consumed <= 5, f"lecture poursuivie bien au-delà du plafond ({consumed} fragments de 16 Ko)"
    assert headers.get("cache-control") == "no-store"


def test_upload_sous_le_plafond_passe_normalement(monkeypatch):
    """Non-régression : un upload ordinaire (petit CSV, Content-Length exact)
    traverse les deux middlewares et aboutit à la page de révision, qui porte
    les en-têtes de sécurité — dont no-store, essentiel sur cette page qui
    affiche le contenu détecté EN CLAIR pour révision."""
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
            assert headers.get(name) == value, f"en-tête {name} absent/incorrect"
        assert "frame-ancestors 'none'" in headers.get("content-security-policy", "")
        assert "connect-src 'self'" in headers.get("content-security-policy", "")
        assert headers.get("strict-transport-security", "").startswith("max-age=")
    finally:
        with main._PENDING_JOBS_LOCK:
            main.PENDING_JOBS.clear()


def test_reponse_erreur_de_l_application_porte_les_entetes_de_securite():
    """Les en-têtes doivent aussi couvrir les réponses d'erreur produites par
    http_exception_handler (ici un 404 de /api/finalize sur un job inconnu)."""
    form = b"job_id=00000000000000000000000000000000"
    status, headers, _, _ = _asgi_request(
        "POST", "/api/finalize",
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "application/json"},
        body_chunks=[form], content_length=len(form),
    )
    assert status == 404
    for name, value in _EXPECTED_SECURITY_HEADERS.items():
        assert headers.get(name) == value, f"en-tête {name} absent/incorrect sur le 404"


def test_download_pdf_autorise_le_cadrage_par_sa_propre_page_apercu():
    """
    La page "Aperçu (contrôle visuel)" servie par /api/finalize embarque le
    PDF caviardé dans un <iframe src="/api/download/{job_id}">. Les en-têtes
    de sécurité globaux (frame-ancestors 'none' + X-Frame-Options: DENY)
    s'appliquaient aussi à cette réponse : le navigateur refuse alors
    d'afficher sa PROPRE réponse dans SA PROPRE iframe ("Firefox ne peut
    ouvrir cette page"), alors même que la requête HTTP réussit (200 dans les
    journaux — la requête part et le fichier est bien renvoyé, seul l'affichage
    est bloqué côté navigateur après coup). Seules les extensions servies en
    inline (PDF/PNG/JPG, voir _INLINE_EXTENSIONS) ont besoin d'assouplir
    frame-ancestors à 'self' — un site tiers reste bloqué, comme les
    téléchargements en pièce jointe (.docx/.csv) qui gardent 'none'.
    """
    # download() est un endpoint SYNCHRONE (def, pas async def) : FastAPI le
    # dispatche via run_in_threadpool, un vrai point de suspension que _drive
    # ne peut pas traverser (voir en-tête de section) — appelée directement,
    # pas à travers _asgi_request, comme toute fonction Python normale. Cela
    # ne teste donc que les en-têtes posés par la route elle-même : le
    # middleware (_asgi_request le couvre déjà ailleurs, ex. ligne ~1043) est
    # responsable de compléter le reste (no-store, nosniff...) en production.
    job_id = "ab" * 16
    out = main.WORKDIR / f"{job_id}-medical-anonymise.pdf"
    out.write_bytes(b"%PDF-1.4 fake")
    try:
        resp = main.download(job_id)
        assert resp.status_code == 200
        assert resp.headers.get("x-frame-options") == "SAMEORIGIN", (
            "X-Frame-Options: DENY empêche la page d'aperçu de cadrer son propre PDF"
        )
        assert "frame-ancestors 'self'" in resp.headers.get("content-security-policy", ""), (
            "frame-ancestors 'none' empêche la page d'aperçu de cadrer son propre PDF"
        )
    finally:
        out.unlink(missing_ok=True)


def test_download_csv_conserve_frame_ancestors_none():
    """Non-régression : les téléchargements en pièce jointe (pas d'aperçu
    intégré, voir main.py ~3712) n'ont aucun besoin d'être cadrables — la
    route ne doit rien surcharger, pour laisser le middleware appliquer la
    posture la plus stricte (frame-ancestors 'none' / DENY) en production."""
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
    http_exception_handler injectait `exc.detail` tel quel dans une page HTML.
    Un seul détail contient une valeur d'origine externe (nom de menace
    remonté par le serveur ICAP, section 9/10 de l'audit) : assainie des
    caractères de contrôle Unicode, mais pas des balises HTML. Le détail doit
    être échappé — quelle que soit sa provenance, présente ou future.
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
    Les documents en transit dans WORKDIR et le journal d'audit ne doivent
    être lisibles que par l'utilisateur du service (umask 077 posé à l'import
    de main) — le bind mount côté hôte les exposait sinon en 644 à tout
    utilisateur local.
    """
    probe = main.WORKDIR / "umask-probe-test.tmp"
    try:
        with open(probe, "w") as f:
            f.write("x")
        mode = _stat.S_IMODE(_os.stat(probe).st_mode)
        assert mode & 0o077 == 0, f"fichier créé en {oct(mode)} : lisible par le groupe/les autres"
    finally:
        probe.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Contrôle de propriétaire sur /api/preview_image et /api/finalize
# ---------------------------------------------------------------------------

def _seed_image_job(job_id: str, user: str) -> None:
    with main._PENDING_JOBS_LOCK:
        main.PENDING_JOBS[job_id] = {
            "kind": "image", "image_format": "PNG", "raw_image": _make_solid_png(8, (255, 0, 0)),
            "detections": [], "clusters": {}, "theme": "", "filename_hash": "deadbeef",
            "user_email": user, "size_mb": 0.01, "created_at": _time.time(),
        }


def test_preview_image_refuse_le_job_d_un_autre_utilisateur():
    """L'aperçu rend le document ORIGINAL : un autre utilisateur authentifié
    connaissant le job_id doit obtenir un 404 indiscernable d'un job inexistant,
    et le job doit rester intact pour son propriétaire."""
    job_id = "11111111222222223333333344444444"
    _seed_image_job(job_id, "alice@hopital.fr")
    try:
        with pytest.raises(HTTPException) as exc:
            main.preview_image(job_id, 0, _FakeRequest({"x-auth-request-email": "mallory@hopital.fr"}))
        assert exc.value.status_code == 404
        assert job_id in main.PENDING_JOBS, "le job du propriétaire a été retiré par la tentative d'un tiers"
        resp = main.preview_image(job_id, 0, _FakeRequest({"x-auth-request-email": "alice@hopital.fr"}))
        assert resp.status_code == 200 and resp.media_type == "image/png"
    finally:
        main.PENDING_JOBS.pop(job_id, None)


def test_finalize_refuse_le_job_d_un_autre_utilisateur_sans_le_detruire():
    """Un tiers ne doit ni finaliser le job d'un autre, ni le faire disparaître
    de la file par sa simple tentative ; le propriétaire finalise ensuite
    normalement."""
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
        assert job_id in main.PENDING_JOBS, "le job a été détruit par la tentative d'un tiers"

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
    """L'en-tête est assaini à la création du job (3.5) ; la comparaison doit
    appliquer le même traitement, sinon un propriétaire légitime dont
    l'en-tête contiendrait un caractère de formatage serait rejeté."""
    job_id = "99999999aaaaaaaabbbbbbbbcccccccc"
    _seed_image_job(job_id, "alice@hopital.fr")
    try:
        resp = main.preview_image(job_id, 0, _FakeRequest({"x-auth-request-email": "alice‮@hopital.fr"}))
        assert resp.status_code == 200
    finally:
        main.PENDING_JOBS.pop(job_id, None)
