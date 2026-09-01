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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402


# ---------------------------------------------------------------------------
# _normalize_allcaps
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("CARROLAGGI Xavier", "Carrolaggi Xavier"),
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
