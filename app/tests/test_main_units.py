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
