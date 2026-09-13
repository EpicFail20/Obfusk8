"""
Tests unitaires — ne nécessitent AUCUN service externe (pas de Presidio, pas
de Traefik, pas de Keycloak). Rapides, à lancer à chaque modification de
main.py ou d'un fichier de thème.

Exécution : depuis /app dans le conteneur, `pytest` ou `pytest tests/ -v`.
"""
import re
import sys
from pathlib import Path

import pytest
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
