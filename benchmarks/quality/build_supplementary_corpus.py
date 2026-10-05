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
Supplementary corpus of the quality benchmark (phase 2, decision Q6 of
2026-10-04): the families of false negatives EXT-29 to EXT-31 with at least
30 examples each. The main corpus has too few of them to measure a target
(5 spaced NIR, 3 IBAN, 1 card; no English date, no weekday) and stays
unchanged for comparability.

Built IN MEMORY at run time, never written to the repository: the values
have the real formats (checksummed IBAN, NIR and card numbers, decision Q5
of 2026-10-04). Deterministic (fixed seed), from the fictitious value
generators of build_quality_corpus.py. Each prompt annotates ONE value of
its family, as a whole (an address includes its postal code and city).

Families (variant field):
  nir_espace, iban_espace, carte_espace   EXT-30: one character per group,
      dots or dashes as separators, or no separator at all;
  adresse_code_postal                     EXT-29: street, postal code and
      city on one line, after a comma, or on the next line;
  date_lettres_fr, date_lettres_en         EXT-31: month in letters, with
      and without weekday, ordinal day, abbreviated month;
  date_tiret_typographique                 EXT-31: en dash, em dash,
      non-breaking hyphen;
  noms_minuscules                          EXT-18 (D-027, 2026-10-05): names
      typed in lower case, chat style ("prénom nom", "nom prénom", no
      accents), the case the PROPN filter of the patched spaCy recognizer
      could drop (no token tagged as a proper noun).
"""

import random
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_quality_corpus import CITIES, MONTHS_FR, STREETS, Values

SEED = 20261004
PER_FORM = 12  # 3 forms per family at least: >= 36 examples per family
MONTHS_EN = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
MONTHS_FR_ABBR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
WEEKDAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ORDINAL_EN = {1: "st", 2: "nd", 3: "rd", 21: "st", 22: "nd", 23: "rd", 31: "st"}

Form = Callable[[Values], str]


def _digits(value: str) -> str:
    return value.replace(" ", "")


def _one_per_group(value: str) -> str:
    return " ".join(_digits(value))


def _regroup(value: str, sizes: list[int], sep: str) -> str:
    raw, out, i = _digits(value), [], 0
    for size in sizes:
        out.append(raw[i : i + size])
        i += size
    if i < len(raw):
        out.append(raw[i:])
    return sep.join(part for part in out if part)


def _date_parts(v: Values) -> tuple[int, int, int, int]:
    day, month, year = v.rng.randint(1, 28), v.rng.randint(1, 12), v.rng.randint(1940, 2026)
    return day, month, year, v.rng.randint(0, 6)


def _fr_letters(v: Values) -> str:
    day, month, year, _ = _date_parts(v)
    return f"{day} {MONTHS_FR[month - 1]} {year}"


def _fr_weekday(v: Values) -> str:
    day, month, year, weekday = _date_parts(v)
    return f"{WEEKDAYS_FR[weekday]} {'1er' if day == 1 else day} {MONTHS_FR[month - 1]} {year}"


def _fr_abbr(v: Values) -> str:
    day, month, year, _ = _date_parts(v)
    return f"{day} {MONTHS_FR_ABBR[month - 1]} {year}"


def _en_us(v: Values) -> str:
    day, month, year, _ = _date_parts(v)
    return f"{MONTHS_EN[month - 1]} {day}, {year}"


def _en_weekday(v: Values) -> str:
    day, month, year, weekday = _date_parts(v)
    return f"{WEEKDAYS_EN[weekday]}, {MONTHS_EN[month - 1]} {day}, {year}"


def _en_ordinal(v: Values) -> str:
    day, month, year, _ = _date_parts(v)
    return f"{day}{ORDINAL_EN.get(day, 'th')} of {MONTHS_EN[month - 1]} {year}"


def _typo_date(dash: str) -> Form:
    def form(v: Values) -> str:
        day, month, year, _ = _date_parts(v)
        return f"{day:02d}{dash}{month:02d}{dash}{year}"

    return form


def _lower_name(order: str) -> Form:
    def form(v: Values) -> str:
        first, last = v.person().split(" ", 1)
        name = f"{last} {first}" if order == "last_first" else f"{first} {last}"
        if order == "ascii":
            name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        return name.lower()

    return form


def _address(sep: str) -> Form:
    def form(v: Values) -> str:
        postcode, city = v.rng.choice(CITIES)
        return f"{v.rng.randint(1, 120)} {v.rng.choice(STREETS)}{sep}{postcode} {city}"

    return form


FAMILIES: dict[str, tuple[str, str, list[Form], list[str]]] = {
    # family: (annotated type, language, forms, sentence templates with {v})
    "nir_espace": (
        "NIR",
        "fr",
        [
            lambda v: _one_per_group(v.nir()),
            lambda v: _regroup(v.nir(), [1, 2, 2, 2, 3, 3, 2], "."),
            lambda v: _digits(v.nir()),
        ],
        [
            "Numéro de sécurité sociale : {v}.",
            "Merci de vérifier le NIR {v} avant envoi.",
            "Assuré {v}, dossier à compléter.",
        ],
    ),
    "iban_espace": (
        "IBAN",
        "fr",
        [lambda v: _one_per_group(v.iban()), lambda v: _regroup(v.iban(), [4] * 7, "-"), lambda v: _digits(v.iban())],
        ["Virement sur l'IBAN {v} demain.", "Coordonnées bancaires : {v}", "Peux-tu vérifier ce RIB {v} ?"],
    ),
    "carte_espace": (
        "CARD",
        "fr",
        [
            lambda v: _one_per_group(v.card()),
            lambda v: _regroup(v.card(), [4, 4, 4, 4], "-"),
            lambda v: _digits(v.card()),
        ],
        ["Carte {v}, expiration 12/29.", "Le client a payé avec la carte {v}.", "Numéro de CB : {v}"],
    ),
    "adresse_code_postal": (
        "ADDRESS",
        "fr",
        [_address(", "), _address(" "), _address("\n")],
        ["Le patient demeure au {v}.", "Adresse de livraison : {v}", "Merci d'écrire au {v} avant vendredi."],
    ),
    "date_lettres_fr": (
        "DATE",
        "fr",
        [_fr_letters, _fr_weekday, _fr_abbr],
        ["Né le {v} à Rennes.", "Rendez-vous fixé au {v}.", "Date d'embauche : {v}."],
    ),
    "date_lettres_en": (
        "DATE",
        "en",
        [_en_us, _en_weekday, _en_ordinal],
        ["Born on {v} in Leeds.", "The appointment is on {v}.", "Hired {v}, probation ended later."],
    ),
    "noms_minuscules": (
        "PERSON",
        "fr",
        [_lower_name("first_last"), _lower_name("last_first"), _lower_name("ascii")],
        [
            "salut, tu peux relancer {v} pour le rdv de demain ?",
            "{v} a appelé ce matin, il faut le rappeler",
            "dis à {v} que c'est ok pour jeudi",
        ],
    ),
    "date_tiret_typographique": (
        "DATE",
        "fr",
        [_typo_date("\u2013"), _typo_date("\u2014"), _typo_date("\u2011")],
        ["Né le {v} à Lyon.", "Consultation du {v}.", "Date de sortie : {v}."],
    ),
}


def build() -> list[dict[str, Any]]:
    """Prompts in the main corpus format: {id, lang, category, variant,
    text, entities: [{start, end, type}]}; offsets by construction."""
    values = Values(random.Random(SEED))  # noqa: S311 - reproducible synthetic data, not cryptography
    docs: list[dict[str, Any]] = []
    for family, (entity_type, lang, forms, sentences) in FAMILIES.items():
        for form in forms:
            for i in range(PER_FORM):
                value = form(values)
                before, after = sentences[i % len(sentences)].split("{v}")
                docs.append(
                    {
                        "id": f"s-{len(docs) + 1:04d}",
                        "lang": lang,
                        "category": "complementaire",
                        "variant": family,
                        "text": before + value + after,
                        "entities": [{"start": len(before), "end": len(before) + len(value), "type": entity_type}],
                    }
                )
    return docs


if __name__ == "__main__":
    from collections import Counter

    corpus = build()
    print(len(corpus), "prompts:", dict(Counter(d["variant"] for d in corpus)))
