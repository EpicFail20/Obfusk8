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
Common Unicode normalization before detection (D-014, phase 2 step B).

Measured in phase 1 (benchmarks/results/quality-20261002T091416.md, masking
recall per variant): zero-width characters 0.646, non-breaking spaces 0.789,
NFD 0.810, against 0.919 without variant. The normalization must help the
analyzer WITHOUT moving any position: every interval found on the normalized
text maps back to the text received, and a value is never cut in two by an
invisible character it contains.
"""

import time
import unicodedata

import pytest
from text_normalization import NormalizedText, normalize_for_analysis

ZWSP, ZWNJ, ZWJ, WJ, BOM = "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"
SHY, LRO, RLO, PDF_ = "\u00ad", "\u202d", "\u202e", "\u202c"
NBSP, NNBSP, THIN, FIGURE = "\u00a0", "\u202f", "\u2009", "\u2007"
RSQUO, LSQUO, MODIFIER_APOS = "\u2019", "\u2018", "\u02bc"
EMOJI, FLAG = "\U0001f600", "\U0001f1eb\U0001f1f7"


def _original(result: NormalizedText, original: str, start: int, end: int) -> str:
    s, e = result.to_original(start, end)
    return original[s:e]


def test_texte_sans_rien_a_normaliser_identite():
    text = "Camille Martin, 06 39 98 12 34, camille.martin@example.com"
    result = normalize_for_analysis(text)
    assert result.text == text
    assert result.to_original(8, 14) == (8, 14)
    assert result.to_original(0, len(text)) == (0, len(text))


def test_texte_accentue_deja_nfc_identite():
    text = "Élodie Lefèvre habite à Besançon"
    result = normalize_for_analysis(text)
    assert result.text == text
    assert result.to_original(0, 6) == (0, 6)


@pytest.mark.parametrize("invisible", [ZWSP, ZWNJ, ZWJ, WJ, BOM, SHY, LRO, RLO, PDF_])
def test_caracteres_de_format_supprimes_et_valeur_couverte(invisible):
    original = f"Patient Camille{invisible}Martin suivi"
    result = normalize_for_analysis(original)
    assert result.text == "Patient CamilleMartin suivi"
    start = result.text.index("CamilleMartin")
    # The value found on the normalized text covers the invisible character.
    assert _original(result, original, start, start + len("CamilleMartin")) == f"Camille{invisible}Martin"


@pytest.mark.parametrize("space", [NBSP, NNBSP, THIN, FIGURE, "\u3000", "\u205f"])
def test_espaces_speciales_remplacees(space):
    original = f"Camille{space}Martin"
    result = normalize_for_analysis(original)
    assert result.text == "Camille Martin"
    assert _original(result, original, 0, 14) == original


def test_nul_et_controles_remplaces_par_une_espace():
    """EXT-38: U+0000 is what PyMuPDF extracts for a glyph without a Unicode
    mapping; between two words it hid the name from the NER."""
    original = "Dr Chloé\x00Boyer\x07fin\ttab\nligne\r"
    result = normalize_for_analysis(original)
    assert result.text == "Dr Chloé Boyer fin\ttab\nligne\r"
    assert _original(result, original, 3, 14) == "Chloé\x00Boyer"


def test_separateurs_de_ligne_et_de_paragraphe():
    result = normalize_for_analysis("a\u2028b\u2029c")
    assert result.text == "a\nb\nc"


def test_nfd_recompose():
    original = unicodedata.normalize("NFD", "Élodie Lefèvre")
    assert len(original) == 16
    result = normalize_for_analysis(original)
    assert result.text == "Élodie Lefèvre"
    assert _original(result, original, 0, 6) == original[:7], "the combining accent belongs to the name"
    assert _original(result, original, 7, 14) == original[8:]


def test_ligatures_developpees():
    original = "Steﬀi Griﬃn"
    result = normalize_for_analysis(original)
    assert result.text == "Steffi Griffin"
    assert _original(result, original, 0, 6) == "Steﬀi"
    # An interval ending in the middle of an expanded ligature still covers it.
    assert _original(result, original, 0, 4) == "Steﬀ"


@pytest.mark.parametrize("apostrophe", [RSQUO, LSQUO, MODIFIER_APOS])
def test_apostrophes_typographiques(apostrophe):
    original = f"l{apostrophe}hôpital d{apostrophe}Aubagne"
    result = normalize_for_analysis(original)
    assert result.text == "l'hôpital d'Aubagne"
    assert len(result.text) == len(original)


def test_combinaison_de_variantes():
    original = unicodedata.normalize("NFD", "Élodie") + ZWSP + NNBSP + "Lef" + SHY + "èvre"
    result = normalize_for_analysis(original)
    assert result.text == "Élodie Lefèvre"
    assert _original(result, original, 0, len(result.text)) == original


@pytest.mark.parametrize("prefix", ["", EMOJI, EMOJI * 3, FLAG, EMOJI + ZWJ + EMOJI])
@pytest.mark.parametrize("suffix", ["", EMOJI, FLAG])
def test_emojis_avant_et_apres(prefix, suffix):
    original = f"{prefix} Camille{ZWSP}Martin {suffix}"
    result = normalize_for_analysis(original)
    start = result.text.index("CamilleMartin")
    assert _original(result, original, start, start + 13) == f"Camille{ZWSP}Martin"


def test_uniquement_des_caracteres_de_format():
    original = ZWSP * 5 + RLO + BOM
    result = normalize_for_analysis(original)
    assert result.text == ""
    assert result.to_original(0, 0) == (len(original), len(original))


def test_texte_vide():
    result = normalize_for_analysis("")
    assert result.text == ""
    assert result.to_original(0, 0) == (0, 0)


def test_bornes_d_intervalle_invalides_refusees():
    result = normalize_for_analysis(f"a{ZWSP}b")
    for start, end in ((-1, 1), (0, 3), (2, 1)):
        with pytest.raises(ValueError):
            result.to_original(start, end)


def test_caractere_invisible_en_bordure_hors_de_la_valeur():
    """Invisible characters just outside a value are not pulled into it."""
    original = f"x {ZWSP}Camille Martin{ZWSP} y"
    result = normalize_for_analysis(original)
    start = result.text.index("Camille")
    assert _original(result, original, start, start + 14) == "Camille Martin"


def test_nfc_par_grappe_comme_nfc_global_sur_le_corpus_de_variantes():
    """The cluster-by-cluster recomposition gives the same text as a global
    NFC on Latin text with combining marks."""
    for word in ("Élodie", "Lefèvre", "Noël", "Çağlar", "Ångström", "Dvořák", "Nguyễn"):
        original = unicodedata.normalize("NFD", f"{word} {word.upper()}")
        assert normalize_for_analysis(original).text == unicodedata.normalize("NFC", original)


def _timed(text: str) -> float:
    best = float("inf")
    for _ in range(5):
        started = time.perf_counter()
        normalize_for_analysis(text)
        best = min(best, time.perf_counter() - started)
    return best


def test_cout_lineaire_au_plafond():
    """MAX_TEXT_CHARS (20 000): worst case (every character to normalize)
    stays linear and fast — the analyzer itself takes ~900 ms at that size."""
    unit = unicodedata.normalize("NFD", "é") + ZWSP + NNBSP + "ﬀ" + "a"
    small = (unit * (10_000 // len(unit)))[:10_000]
    large = (unit * (20_000 // len(unit)))[:20_000]
    t_small, t_large = _timed(small), _timed(large)
    assert t_large < 0.25, f"{t_large:.3f}s at 20 000 characters"
    assert t_large < 3 * t_small, f"not linear: {t_small:.4f}s -> {t_large:.4f}s"


@pytest.mark.parametrize(
    "original, expected",
    [
        ("Dr Chloé\x00 Boyer", "Dr Chloé Boyer"),
        ("Dr Chloé \x00Boyer", "Dr Chloé Boyer"),
        ("Camille\u00a0\u00a0Martin", "Camille Martin"),
        ("Camille \u202fMartin", "Camille Martin"),
        ("Camille\u00a0\nMartin", "Camille\nMartin"),
        ("a\x00b", "a b"),
    ],
)
def test_pas_d_espace_double_creee_par_la_normalisation(original, expected):
    """Regression (phase 2, end-to-end EXT-35 test): "Chloé<NUL> Boyer"
    became "Chloé  Boyer" (two spaces) and the real NER no longer found the
    name, while it finds "Chloé Boyer". A character turned into a space is
    dropped when it touches white space already; the value still maps back
    over the whole original span."""
    result = normalize_for_analysis(original)
    assert result.text == expected
    assert _original(result, original, 0, len(result.text)) == original
