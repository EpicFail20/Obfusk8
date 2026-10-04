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
Families of false negatives fixed in phase 2, step C, for EVERY flow (text
API and documents, with and without theme):
  - EXT-08 / EXT-23: payment card, NIR and email with any domain, until now
    on the text routes only (app/themes/extension/identifiers.json), moved to
    app/themes/common.json (decision Q4 of 2026-10-04); the NIR stays a
    reference to the medical theme, never a copy.

Values are fictitious (checksums computed at run time where needed).
"""

import json
import time

import pytest

import main
import text_api

IDENTIFIERS = {"PaymentCardNumberRecognizer", "AnyDomainEmailRecognizer", "FrenchNirRecognizer"}


def _names(recognizers: list[dict]) -> list[str]:
    return [r.get("name") for r in recognizers]


def test_reconnaisseurs_communs_incluent_carte_courriel_et_nir():
    assert set(_names(main.COMMON_RECOGNIZERS)) >= IDENTIFIERS
    nir = next(r for r in main.COMMON_RECOGNIZERS if r["name"] == "FrenchNirRecognizer")
    medical_nir = next(r for r in main.THEMES["medical"]["ad_hoc_recognizers"] if r["name"] == "FrenchNirRecognizer")
    assert nir == medical_nir, "referenced from the medical theme, not copied"


def test_l_extension_ne_garde_que_ses_propres_reconnaisseurs():
    extension = text_api.load_extension_recognizers(main.THEMES_DIR / "extension", main.THEMES)
    assert not IDENTIFIERS & set(_names(extension))
    assert not (main.THEMES_DIR / "extension" / "identifiers.json").exists()


def _sent_recognizers(monkeypatch: pytest.MonkeyPatch, theme: dict | None) -> list[str]:
    """Names of the ad hoc recognizers in the request _analyze_text sends."""
    sent: dict = {}

    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> list:
            return []

    def fake_post(url: str, json: dict, timeout: float) -> _Response:
        sent.update(json)
        return _Response()

    monkeypatch.setattr(main.requests, "post", fake_post)
    main._analyze_text("Bonjour", theme=theme)
    return _names(sent["ad_hoc_recognizers"])


@pytest.mark.parametrize("theme_key", [None, *sorted(main.THEMES)])
def test_tous_les_flux_envoient_les_identifiants_sans_doublon(monkeypatch, theme_key):
    """Document flow (and text API, which goes through the same call), with
    and without theme. The medical theme also defines the NIR: sent once."""
    names = _sent_recognizers(monkeypatch, main.THEMES.get(theme_key) if theme_key else None)
    assert set(names) >= IDENTIFIERS
    assert len(names) == len(set(names)), names


def test_inclusion_introuvable_dans_les_reconnaisseurs_communs_echoue(tmp_path):
    """A silently missing recognizer is a silent false negative: an unknown
    included name fails startup, as for the extension files."""
    path = tmp_path / "common.json"
    path.write_text(json.dumps({"include_theme_recognizers": {"medical": ["Inexistant"]}, "ad_hoc_recognizers": []}))
    with pytest.raises(text_api.TextApiConfigError):
        main._load_common_recognizers(path, main.THEMES)


def test_inclusion_par_reference_dans_les_reconnaisseurs_communs(tmp_path):
    path = tmp_path / "common.json"
    path.write_text(
        json.dumps(
            {"include_theme_recognizers": {"medical": ["FrenchNirRecognizer"]}, "ad_hoc_recognizers": [{"name": "X"}]}
        )
    )
    loaded = main._load_common_recognizers(path, main.THEMES)
    assert _names(loaded) == ["FrenchNirRecognizer", "X"]


# ---------------------------------------------------------------------------
# EXT-29 to EXT-31: patterns of app/themes/common.json, compiled EXACTLY as
# presidio-analyzer compiles ad hoc recognizers (`regex` module of the
# analyzer's version, IGNORECASE | DOTALL | MULTILINE, D-017), on the text the
# analyzer receives (common normalization, then upper-case words and dashes).
# ---------------------------------------------------------------------------

regex = pytest.importorskip("regex")

from text_normalization import normalize_for_analysis  # noqa: E402

PRESIDIO_FLAGS = regex.DOTALL | regex.MULTILINE | regex.IGNORECASE
COMPILED = [
    (r["supported_entity"], regex.compile(p["regex"], PRESIDIO_FLAGS))
    for r in main.COMMON_RECOGNIZERS
    for p in r["patterns"]
]


def _spans(text: str) -> list[tuple[str, int, int]]:
    normalized = normalize_for_analysis(text)
    analyzed = main._normalize_dashes(main._normalize_allcaps(normalized.text))
    out = []
    for entity, compiled in COMPILED:
        for m in compiled.finditer(analyzed):
            start, end = normalized.to_original(m.start(), m.end())
            out.append((entity, start, end))
    return out


def _covered(text: str, value: str) -> bool:
    """The value is entirely covered by the union of the matches (masking)."""
    start = text.index(value)
    covered = [False] * len(value)
    for _, s, e in _spans(text):
        for i in range(max(s, start), min(e, start + len(value))):
            covered[i - start] = True
    return all(covered[i] for i, c in enumerate(value) if not c.isspace())


def _matched(text: str) -> set[str]:
    return {text[s:e] for _, s, e in _spans(text)}


# Fictitious identifiers with valid checksums, computed here (never real ones).
def _nir() -> str:
    base = "1840575123456"
    return base + f"{97 - int(base) % 97:02d}"


def _iban() -> str:
    bban = "30001007941234567890185"
    check = 98 - int(bban + "1527" + "00") % 97
    return f"FR{check:02d}{bban}"


def _card() -> str:
    digits = [int(c) for c in "497010123456789"]
    total = sum((d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 == 0 else d for i, d in enumerate(reversed(digits)))
    return "".join(map(str, digits)) + str((10 - total % 10) % 10)


def _one_per_group(raw: str) -> str:
    return " ".join(raw)


def _groups(raw: str, sizes: list[int], sep: str) -> str:
    out, i = [], 0
    for size in sizes:
        out.append(raw[i : i + size])
        i += size
    return sep.join(p for p in [*out, raw[i:]] if p)


@pytest.mark.parametrize(
    "text, value",
    [
        ("Le patient demeure au 35 rue de la Paix, 21000 Dijon.", "35 rue de la Paix, 21000 Dijon"),
        ("Adresse : 12 avenue Jean Jaurès 69003 Lyon", "12 avenue Jean Jaurès 69003 Lyon"),
        ("Écrire au 7 impasse du Moulin,\n29200 Brest avant vendredi.", "7 impasse du Moulin,\n29200 Brest"),
        ("Domicile : 3 allée des Pins\n06000 Nice", "3 allée des Pins\n06000 Nice"),
        ("Transféré à 33000 Bordeaux hier.", "33000 Bordeaux"),
        ("Né à 2A004 Ajaccio, vit à 97400 Saint-Denis.", "97400 Saint-Denis"),
    ],
)
def test_ext29_code_postal_dans_l_adresse(text, value):
    assert _covered(text, value), _matched(text)


@pytest.mark.parametrize(
    "text",
    ["Total 12345 euros hors taxes.", "Référence 75011 en attente.", "Il reste 21000 dossiers."],
)
def test_ext29_pas_de_code_postal_sans_ville(text):
    assert not any(e == "STREET_ADDRESS" for e, _, _ in _spans(text)), _matched(text)


@pytest.mark.parametrize(
    "form",
    [
        lambda raw: _groups(raw, [1, 2, 2, 2, 3, 3], " "),
        _one_per_group,
        lambda raw: _groups(raw, [1, 2, 2, 2, 3, 3], "."),
        lambda raw: _groups(raw, [1, 2, 2, 2, 3, 3], "\u00a0"),
        lambda raw: raw,
    ],
    ids=["groupes", "un_par_un", "points", "insecables", "sans_separateur"],
)
def test_ext30_nir_toutes_formes(form):
    value = form(_nir())
    text = f"NIR de l'assuré : {value}."
    assert _covered(text, value), _matched(text)


@pytest.mark.parametrize(
    "form",
    [lambda raw: _groups(raw, [4] * 7, " "), _one_per_group, lambda raw: _groups(raw, [4] * 7, "-"), lambda raw: raw],
    ids=["groupes", "un_par_un", "tirets", "sans_separateur"],
)
def test_ext30_iban_toutes_formes(form):
    value = form(_iban())
    text = f"Virement sur l'IBAN {value} demain."
    assert _covered(text, value), _matched(text)


@pytest.mark.parametrize(
    "form",
    [lambda raw: _groups(raw, [4] * 4, " "), _one_per_group, lambda raw: _groups(raw, [4] * 4, "-"), lambda raw: raw],
    ids=["groupes", "un_par_un", "tirets", "sans_separateur"],
)
def test_ext30_carte_toutes_formes(form):
    value = form(_card())
    text = f"Payé avec la carte {value}, expiration 12/29."
    assert _covered(text, value), _matched(text)


@pytest.mark.parametrize(
    "text",
    [
        "Rappeler le 06 39 98 12 34 demain.",
        "Rappeler le 0 6 3 9 9 8 1 2 3 4 demain.",
        "Né le 12 04 1985 à Lyon.",
        "Montant : 1 234 567,89 euros.",
        "Version 1.2.3.4.5 du logiciel.",
    ],
)
def test_ext30_pas_d_identifiant_dans_des_nombres_ordinaires(text):
    found = [(e, text[s:x]) for e, s, x in _spans(text) if e in ("FR_NIR", "IBAN_CODE", "CREDIT_CARD")]
    assert not found, found


@pytest.mark.parametrize(
    "value",
    [
        "14 mai 1981",
        "1er janvier 2024",
        "lundi 3 mars 2025",
        "21 février 1972",
        "3 févr. 2025",
        "7 sept. 2019",
        "31 décembre 1999",
        "March 3, 2025",
        "Monday, March 3, 2025",
        "3rd of March 2025",
        "3 March 2025",
        "Sept. 21, 2001",
    ],
)
def test_ext31_dates_en_toutes_lettres(value):
    """Typographic-dash dates (en dash, em dash) are not here: the existing
    dash normalization hands them to Presidio's own DateRecognizer, measured
    end to end by the supplementary corpus (family date_tiret_typographique)."""
    text = f"Rendez-vous fixé au {value}, merci."
    assert _covered(text, value), _matched(text)


@pytest.mark.parametrize(
    "text",
    [
        "Mars est une planète.",
        "May I help you?",
        "Nous sommes en mai.",
        "Il a 12 mois de retard.",
        "Le 1er arrivé gagne.",
    ],
)
def test_ext31_pas_de_date_sans_jour_et_mois(text):
    found = [text[s:e] for ent, s, e in _spans(text) if ent == "DATE_TIME"]
    assert not found, found


CAP = text_api.TextApiSettings().max_text_chars
ADVERSARIAL = [
    "1 " * (CAP // 2),
    "1." * (CAP // 2),
    "F R " + "1 " * (CAP // 2 - 2),
    "FR" + "1-" * (CAP // 2 - 1),
    "4 " * (CAP // 2),
    "lundi " * (CAP // 6),
    "3 mars " * (CAP // 7),
    "Monday, " * (CAP // 8),
    "3rd of " * (CAP // 7),
    "12345 Aa" * (CAP // 8),
    "1 rue " + "a " * (CAP // 2 - 3),
    "1 rue a, 21000 " + "Aa-" * (CAP // 3 - 6),
]


@pytest.mark.parametrize("probe", range(len(ADVERSARIAL)))
def test_ext29_31_pas_de_retour_arriere_catastrophique(probe):
    text = ADVERSARIAL[probe][:CAP]
    started = time.perf_counter()
    _spans(text)
    assert time.perf_counter() - started < 1.0
