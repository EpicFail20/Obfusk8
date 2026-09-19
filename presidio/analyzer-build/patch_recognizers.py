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
Patches default_recognizers.yaml reliably via a real YAML parser,
rather than by raw text insertion (sed) which proved fragile with the
"zero sequence" indentation style used by Presidio.
"""
import yaml

PATH = "/app/presidio_analyzer/conf/default_recognizers.yaml"

with open(PATH, encoding="utf-8") as f:
    data = yaml.safe_load(f)

# Declares French in the registry (needed in addition to
# default_analyzer.yaml, otherwise Presidio refuses to start)
langs = data.get("supported_languages", [])
if "fr" not in langs:
    langs.append("fr")
data["supported_languages"] = langs

# Adds our custom recognizer for patient identifiers
#
# History: the "digits+letter+digits" pattern (e.g. 22D0755084) and the
# abbreviated label "n°" (e.g. "Dossier n° : ...") were missing here and
# were only covered by a similar pattern in themes/medical.json — so
# active only when that theme was explicitly selected. A file/record
# number remains sensitive regardless of the chosen theme (or even with
# no theme): these patterns are now hardcoded in, hence always active,
# independent of the theme.
data.setdefault("recognizers", []).append(
    {
        "name": "PatientDossierNumberRecognizer",
        "supported_language": "fr",
        "patterns": [
            {
                "name": "dossier patient (lettre + chiffres)",
                "regex": r"\b[A-Z]\d{8,12}\b",
                "score": 0.6,
            },
            {
                "name": "dossier patient (chiffres + lettre + chiffres, ex: 22D0755084)",
                "regex": r"\b\d{1,4}[A-Z]\d{6,10}\b",
                "score": 0.6,
            },
            {
                "name": "dossier n° + valeur (couvre aussi le purement numérique)",
                "regex": r"\b[Dd]ossier\s?[Nn][o°]\.?\s?[:.\-]?\s?[A-Za-z0-9]{4,}\b",
                "score": 0.65,
            },
            {
                "name": "n° dossier + valeur, ordre inversé (couvre aussi le purement numérique)",
                "regex": r"\b[Nn][o°]\.?\s?(?:de\s?)?[Dd]ossier\s?[:.\-]?\s?[A-Za-z0-9]{4,}\b",
                "score": 0.65,
            },
        ],
        "context": ["dossier", "numero", "numéro", "patient", "identifiant"],
        "supported_entity": "PATIENT_ID",
        "type": "custom",
    }
)

with open(PATH, "w", encoding="utf-8") as f:
    yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

print("default_recognizers.yaml patché avec succès (fr + PatientDossierNumberRecognizer)")
