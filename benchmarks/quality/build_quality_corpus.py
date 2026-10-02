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
Builds the annotated prompt corpus of the quality benchmark (phase 1,
step E): corpus.jsonl, one prompt per line,
{"id", "lang", "category", "variant", "text", "entities": [{"start", "end", "type"}]}.

How it is produced (see benchmarks/README.md):
- Prompts are written as templates, filled from pools of FICTITIOUS values
  with a fixed seed: the corpus is identical on every run.
- Each prompt is a list of segments (plain text or an entity of a known
  type); offsets are computed by concatenation, never by searching the
  text, so the annotation is exact by construction.
- Adversarial variants transform the segments one by one (typographic
  dashes and apostrophes, non-breaking and zero-width spaces, NFD, spaced
  digits, capitals, multi-line addresses, dense context) and the offsets
  are recomputed the same way, even when a transformation changes lengths.

Why the values are fictitious:
- phone numbers only in the ranges the French numbering plan reserves for
  fiction (ARCEP decision 2019-0954, s.2.5.12 "Numéros pour œuvres
  audiovisuelles": 01 99 00, 02 61 91, 03 53 01, 04 65 71, 05 36 49, 06 39 98);
- email domains reserved for documentation (example.com, example.org,
  RFC 2606), plus an internal-looking domain under .invalid (RFC 2606);
- names: common first names and surnames combined at random — no real
  person is described; IBAN, NIR and card numbers are generated with a
  valid checksum (so validating recognizers can fire) from random digits;
- secrets: provider documentation examples or strings spelled FAKE.

Usage: python3 benchmarks/quality/build_quality_corpus.py
"""

import json
import random
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20261002
# Below this length a value is left without a zero-width space (nothing to split).
MIN_SPLIT_CHARS = 4
# Templates of the first rounds also get the dense-context prompt.
DENSE_ROUNDS = 2

Segment = tuple[str, str | None]  # (text, entity type or None)

FIRST_FR = [
    "Camille",
    "Lucas",
    "Léa",
    "Hugo",
    "Chloé",
    "Louis",
    "Manon",
    "Gabriel",
    "Inès",
    "Arthur",
    "Zoé",
    "Jules",
    "Élodie",
    "Théo",
    "Margaux",
    "Nathan",
    "Anaïs",
    "Mathis",
    "Océane",
    "Raphaël",
    "Jean-Baptiste",
    "Marie-Hélène",
    "Rose",
    "Pierre",
]
LAST_FR = [
    "Martin",
    "Bernard",
    "Dubois",
    "Durand",
    "Leroy",
    "Moreau",
    "Laurent",
    "Lefèvre",
    "Michel",
    "Garcia",
    "Bertrand",
    "Roux",
    "Fournier",
    "Morel",
    "Girard",
    "Mercier",
    "Guérin",
    "Boyer",
    "Garnier",
    "Chevalier",
    "Legrand",
    "Gauthier",
    "Blanc",
    "Petit",
    "Lemaire",
    "Rousseau",
]
FIRST_EN = ["Emily", "James", "Olivia", "Jack", "Sophie", "Harry", "Grace", "Oliver"]
LAST_EN = ["Carter", "Walker", "Hughes", "Turner", "Bennett", "Collins", "Foster", "Hayes"]
CITIES = [
    ("35000", "Rennes"),
    ("69003", "Lyon"),
    ("44000", "Nantes"),
    ("59000", "Lille"),
    ("33000", "Bordeaux"),
    ("67000", "Strasbourg"),
    ("38000", "Grenoble"),
    ("21000", "Dijon"),
    ("29200", "Brest"),
    ("57000", "Metz"),
    ("14000", "Caen"),
    ("06000", "Nice"),
]
STREETS = [
    "rue des Lilas",
    "avenue Jean Jaurès",
    "boulevard de la Liberté",
    "impasse du Moulin",
    "allée des Pins",
    "rue Victor Hugo",
    "place de la République",
    "chemin des Vignes",
    "quai de la Loire",
    "rue de la Paix",
]
FICTION_ROOTS = ["01 99 00", "02 61 91", "03 53 01", "04 65 71", "05 36 49", "06 39 98"]
MONTHS_FR = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]
SECRETS = [
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_FAKEfakeFAKEfakeFAKEfake0123456789",
    "sk_test_FAKEFAKEFAKEFAKE1234",
    "xoxb-FICTIF-NON-VALIDE-FAKEFAKEFAKEFAKEFAKE000",
    "glpat-FakeFakeFake0123456789",
    "AIzaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGewQe",
]


class Values:
    """Fictitious values, from a seeded generator."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def person(self, lang: str = "fr") -> str:
        first, last = (FIRST_EN, LAST_EN) if lang == "en" else (FIRST_FR, LAST_FR)
        return f"{self.rng.choice(first)} {self.rng.choice(last)}"

    def surname(self) -> str:
        return self.rng.choice(LAST_FR)

    def city(self) -> str:
        return self.rng.choice(CITIES)[1]

    def address(self) -> tuple[str, str]:
        number = self.rng.randint(1, 120)
        postcode, city = self.rng.choice(CITIES)
        return f"{number} {self.rng.choice(STREETS)}", f"{postcode} {city}"

    def phone(self) -> str:
        return f"{self.rng.choice(FICTION_ROOTS)} {self.rng.randint(0, 99):02d} {self.rng.randint(0, 99):02d}"

    def email(self, person: str) -> str:
        local = unicodedata.normalize("NFKD", person.lower()).encode("ascii", "ignore").decode().replace(" ", ".")
        return f"{local}@{self.rng.choice(['example.com', 'example.org', 'intranet.hopital.invalid'])}"

    def birth_date(self, style: str = "slash") -> str:
        d, m, y = self.rng.randint(1, 28), self.rng.randint(1, 12), self.rng.randint(1940, 2005)
        if style == "text":
            return f"{d} {MONTHS_FR[m - 1]} {y}"
        return f"{d:02d}/{m:02d}/{y}"

    def iban(self) -> str:
        bban = "".join(str(self.rng.randint(0, 9)) for _ in range(23))
        digits = bban + "1527" + "00"  # F=15, R=27, check digits placeholder (ISO 13616 mod 97)
        check = 98 - int(digits) % 97
        raw = f"FR{check:02d}{bban}"
        return " ".join(raw[i : i + 4] for i in range(0, len(raw), 4))

    def nir(self) -> str:
        sex, yy, mm = self.rng.choice("12"), self.rng.randint(40, 99), self.rng.randint(1, 12)
        dept, commune, order = self.rng.randint(1, 95), self.rng.randint(1, 999), self.rng.randint(1, 999)
        base = f"{sex}{yy:02d}{mm:02d}{dept:02d}{commune:03d}{order:03d}"
        key = 97 - int(base) % 97
        return f"{base[0]} {base[1:3]} {base[3:5]} {base[5:7]} {base[7:10]} {base[10:13]} {key:02d}"

    def card(self) -> str:
        digits = [4, 9, 7, 0] + [self.rng.randint(0, 9) for _ in range(11)]
        total = 0
        for i, d in enumerate(reversed(digits)):  # Luhn check digit
            v = d * 2 if i % 2 == 0 else d
            total += v - 9 if v > 9 else v  # noqa: PLR2004 - Luhn: digit sum of a doubled digit
        digits.append((10 - total % 10) % 10)
        s = "".join(map(str, digits))
        return " ".join(s[i : i + 4] for i in range(0, 16, 4))

    def patient_id(self) -> str:
        return f"{self.rng.randint(10, 26)}D{self.rng.randint(1000000, 9999999)}"

    def secret(self) -> str:
        return self.rng.choice(SECRETS)


def _templates(v: Values) -> list[tuple[str, str, list[Segment]]]:
    """(lang, category, segments). One call = one fresh set of values."""
    p1, p2, p3 = v.person(), v.person(), v.person()
    en1, en2 = v.person("en"), v.person("en")
    street, city_line = v.address()
    return [
        (
            "fr",
            "courriel",
            [
                ("Peux-tu rendre ce courriel plus poli ?\n\nBonjour ", None),
                (p1, "PERSON"),
                (",\nje n'ai toujours pas reçu le devis. Rappelle-moi au ", None),
                (v.phone(), "PHONE"),
                (" ou écris-moi à ", None),
                (v.email(p2), "EMAIL"),
                (".\nCordialement,\n", None),
                (p2, "PERSON"),
            ],
        ),
        (
            "fr",
            "courriel",
            [
                ("Résume ce message : « Suite à notre échange, ", None),
                (p1, "PERSON"),
                (" passera à l'agence de ", None),
                (v.city(), "LOCATION"),
                (" jeudi. Son adresse personnelle est ", None),
                (street, "ADDRESS"),
                (", ", None),
                (city_line, "ADDRESS"),
                (". »", None),
            ],
        ),
        (
            "fr",
            "notes",
            [
                ("Notes de réunion à mettre en forme : présents ", None),
                (p1, "PERSON"),
                (", ", None),
                (p2, "PERSON"),
                (" et ", None),
                (p3, "PERSON"),
                (". Action : ", None),
                (p2, "PERSON"),
                (" relance le fournisseur avant vendredi.", None),
            ],
        ),
        (
            "fr",
            "contrat",
            [
                ("Corrige l'orthographe : Entre les soussignés, ", None),
                (p1, "PERSON"),
                (", né le ", None),
                (v.birth_date(), "DATE"),
                (" à ", None),
                (v.city(), "LOCATION"),
                (", demeurant ", None),
                (street, "ADDRESS"),
                (", ", None),
                (city_line, "ADDRESS"),
                (", ci-après dénommé le Bailleur, d'une part ; et la société Fictive SAS, d'autre part.", None),
            ],
        ),
        (
            "fr",
            "clinique",
            [
                ("Rédige une lettre de liaison à partir de ces notes. Patient : ", None),
                (p1, "PERSON"),
                (", né le ", None),
                (v.birth_date(), "DATE"),
                (". NIR ", None),
                (v.nir(), "NIR"),
                (". Dossier n° ", None),
                (v.patient_id(), "PATIENT_ID"),
                (". Adressé par le Dr ", None),
                (p2, "PERSON"),
                (" pour bilan d'une dyspnée d'effort. Antécédents : HTA, diabète de type 2. Joignable au ", None),
                (v.phone(), "PHONE"),
                (".", None),
            ],
        ),
        (
            "fr",
            "clinique",
            [
                ("Synthétise : Mme ", None),
                (v.surname(), "PERSON"),
                (", 67 ans, hospitalisée du 3 au 9 mars pour pneumopathie. Sa fille, ", None),
                (p2, "PERSON"),
                (", souhaite être appelée au ", None),
                (v.phone(), "PHONE"),
                (" avant la sortie.", None),
            ],
        ),
        (
            "fr",
            "rh",
            [
                ("Prépare l'avenant pour ", None),
                (p1, "PERSON"),
                (", salarié depuis le ", None),
                (v.birth_date("text"), "DATE"),
                (". Virement du salaire sur l'IBAN ", None),
                (v.iban(), "IBAN"),
                (". Responsable : ", None),
                (p2, "PERSON"),
                (".", None),
            ],
        ),
        (
            "fr",
            "rh",
            [
                ("Rends ce message plus neutre : « ", None),
                (p1, "PERSON"),
                (" est en arrêt maladie, merci de ne pas en parler à ", None),
                (p2, "PERSON"),
                (". Son numéro de sécurité sociale est le ", None),
                (v.nir(), "NIR"),
                (". »", None),
            ],
        ),
        (
            "fr",
            "ticket",
            [
                ("Aide-moi à répondre à ce ticket : l'utilisateur ", None),
                (p1, "PERSON"),
                (" (", None),
                (v.email(p1), "EMAIL"),
                (") ne peut plus se connecter au VPN depuis ce matin. Son mot de passe est ", None),
                ("Soleil2026!Fictif", "SECRET"),
                (" et il appelle du ", None),
                (v.phone(), "PHONE"),
                (".", None),
            ],
        ),
        (
            "fr",
            "ticket",
            [
                ("Explique cette erreur : la carte ", None),
                (v.card(), "CARD"),
                (" a été refusée pour la commande du client ", None),
                (p1, "PERSON"),
                (".", None),
            ],
        ),
        (
            "fr",
            "code",
            [
                (
                    "Pourquoi ce script échoue-t-il ?\n```python\nimport boto3\nclient = boto3.client(\n"
                    "    's3',\n    aws_access_key_id='",
                    None,
                ),
                (v.secret(), "SECRET"),
                ("',\n)\nprint(client.list_buckets())\n```\nL'erreur arrive chez ", None),
                (p1, "PERSON"),
                (".", None),
            ],
        ),
        (
            "fr",
            "code",
            [
                ("Optimise cette requête : SELECT * FROM clients WHERE email = '", None),
                (v.email(p1), "EMAIL"),
                ("' AND ville = '", None),
                (v.city(), "LOCATION"),
                ("';", None),
            ],
        ),
        (
            "en",
            "email",
            [
                ("Make this email shorter: Hi ", None),
                (en1, "PERSON"),
                (", please call me back on ", None),
                (v.phone(), "PHONE"),
                (" or write to ", None),
                (v.email(en2), "EMAIL"),
                (". Best, ", None),
                (en2, "PERSON"),
            ],
        ),
        (
            "en",
            "ticket",
            [
                ("Summarize this ticket: user ", None),
                (en1, "PERSON"),
                (" reports that the token ", None),
                (v.secret(), "SECRET"),
                (" leaked in a public repository.", None),
            ],
        ),
        (
            "mixte",
            "notes",
            [
                ("Translate into English: ", None),
                (p1, "PERSON"),
                (" a rencontré ", None),
                (en1, "PERSON"),
                (" à ", None),
                (v.city(), "LOCATION"),
                (" ; follow-up call at ", None),
                (v.phone(), "PHONE"),
                (".", None),
            ],
        ),
        (
            "mixte",
            "clinique",
            [
                ("Write a referral letter in English. Patient ", None),
                (p1, "PERSON"),
                (", born ", None),
                (v.birth_date(), "DATE"),
                (", NIR ", None),
                (v.nir(), "NIR"),
                (", lives at ", None),
                (street, "ADDRESS"),
                (", ", None),
                (city_line, "ADDRESS"),
                (".", None),
            ],
        ),
    ]


# --- Adversarial variants: segment-wise transformations ------------------------


def _typographic(text: str, kind: str | None) -> str:
    text = text.replace("'", "\u2019")
    if kind == "DATE":
        return text.replace("/", "\u2013")
    if kind == "PHONE":
        return text.replace(" ", "\u2011")  # non-breaking hyphen between groups
    return text


def _nbsp(text: str, kind: str | None) -> str:
    if kind in ("PHONE", "IBAN", "NIR", "CARD", "DATE"):
        return text.replace(" ", "\u00a0")
    if kind in ("PERSON", "ADDRESS"):
        return text.replace(" ", "\u202f")
    return text


def _zero_width(text: str, kind: str | None) -> str:
    if kind in ("PERSON", "EMAIL", "PHONE") and len(text) >= MIN_SPLIT_CHARS:
        middle = len(text) // 2
        return text[:middle] + "\u200b" + text[middle:]
    return text


def _nfd(text: str, kind: str | None) -> str:
    return unicodedata.normalize("NFD", text)


def _spaced_digits(text: str, kind: str | None) -> str:
    if kind in ("PHONE", "IBAN", "NIR", "CARD"):
        return " ".join(text.replace(" ", ""))
    return text


def _capitals(text: str, kind: str | None) -> str:
    if kind == "PERSON":
        parts = text.split(" ")
        return " ".join([parts[-1].upper(), *parts[:-1]]) if len(parts) > 1 else text.upper()
    return text


def _multiline_address(text: str, kind: str | None) -> str:
    return text.replace(", ", ",\n") if kind is None else text


VARIANTS: dict[str, Callable[[str, str | None], str]] = {
    "typographique": _typographic,
    "espaces_insecables": _nbsp,
    "largeur_nulle": _zero_width,
    "nfd": _nfd,
    "chiffres_espaces": _spaced_digits,
    "majuscules": _capitals,
    "adresse_multiligne": _multiline_address,
}


def _dense(v: Values) -> tuple[str, str, list[Segment]]:
    """Names in a dense context: a staff list with roles, no sentence."""
    segments: list[Segment] = [("Mets ce tableau de garde en liste à puces :\n", None)]
    for role in ("IDE", "AS", "Interne", "Cadre", "IDE", "Médecin", "AS", "Secrétaire"):
        segments += [
            (f"{role} ", None),
            (v.person(), "PERSON"),
            (" \u2013 poste ", None),
            (str(v.rng.randint(1000, 9999)), None),
            (" ; ", None),
        ]
    return "fr", "clinique", segments


@dataclass
class Prompt:
    lang: str
    category: str
    variant: str
    text: str
    entities: list[dict[str, int | str]]
    segments: list[Segment] = field(default_factory=list)
    id: str = ""

    def record(self) -> dict[str, object]:
        """The JSONL line: segments are a build detail, not part of the corpus."""
        return {
            "id": self.id,
            "lang": self.lang,
            "category": self.category,
            "variant": self.variant,
            "text": self.text,
            "entities": self.entities,
        }


def _render(
    segments: list[Segment], transform: Callable[[str, str | None], str] | None
) -> tuple[str, list[dict[str, int | str]]]:
    text: list[str] = []
    entities: list[dict[str, int | str]] = []
    pos = 0
    for chunk, kind in segments:
        out = transform(chunk, kind) if transform else chunk
        if kind is not None:
            entities.append({"start": pos, "end": pos + len(out), "type": kind})
        text.append(out)
        pos += len(out)
    return "".join(text), entities


def build() -> list[Prompt]:
    rng = random.Random(SEED)  # noqa: S311 - reproducible synthetic data, not cryptography
    values = Values(rng)
    docs: list[Prompt] = []
    for round_ in range(4):  # 4 fills of every template: 64 base prompts
        templates = [*_templates(values), _dense(values)] if round_ < DENSE_ROUNDS else _templates(values)
        for lang, category, segments in templates:
            text, entities = _render(segments, None)
            docs.append(Prompt(lang, category, "base", text, entities, segments))
    base = list(docs)
    for name, transform in VARIANTS.items():
        for d in base[::2]:  # every other base prompt, per variant
            text, entities = _render(d.segments, transform)
            if text != d.text:
                docs.append(Prompt(d.lang, d.category, name, text, entities))
    for i, d in enumerate(docs, 1):
        d.id = f"q-{i:04d}"
    return docs


def main() -> None:
    docs = build()
    for d in docs:  # self-check: annotations point at non-empty spans of the text
        for e in d.entities:
            if not d.text[int(e["start"]) : int(e["end"])].strip():
                raise ValueError(f"empty annotation in {d.id}")
    with open(HERE / "corpus.jsonl", "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d.record(), ensure_ascii=False) + "\n")
    by_variant = Counter(d.variant for d in docs)
    entities = sum(len(d.entities) for d in docs)
    print(f"{len(docs)} prompts, {entities} entités annotées ; par variante : {dict(by_variant)}")


if __name__ == "__main__":
    main()
