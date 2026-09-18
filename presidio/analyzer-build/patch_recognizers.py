"""
Patch default_recognizers.yaml de façon fiable via un vrai parseur YAML,
plutôt que par insertion de texte brut (sed) qui s'est révélée fragile
avec le style d'indentation "séquence zéro" utilisé par Presidio.
"""
import yaml

PATH = "/app/presidio_analyzer/conf/default_recognizers.yaml"

with open(PATH, encoding="utf-8") as f:
    data = yaml.safe_load(f)

# Déclare le français dans le registry (nécessaire en plus de
# default_analyzer.yaml, sinon Presidio refuse de démarrer)
langs = data.get("supported_languages", [])
if "fr" not in langs:
    langs.append("fr")
data["supported_languages"] = langs

# Ajoute notre recognizer personnalisé pour les identifiants patient
#
# Historique : le motif "chiffres+lettre+chiffres" (ex: 22D0755084) et le
# libellé abrégé "n°" (ex: "Dossier n° : ...") manquaient ici et n'étaient
# couverts que par un motif similaire dans themes/medical.json — donc
# actifs seulement quand ce thème était explicitement sélectionné. Un
# numéro de dossier reste sensible quel que soit le thème choisi (ou même
# sans thème) : ces motifs sont désormais intégrés en dur, donc toujours
# actifs, indépendamment du thème.
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
