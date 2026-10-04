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
