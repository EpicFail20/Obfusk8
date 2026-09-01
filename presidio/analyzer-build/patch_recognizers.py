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
data.setdefault("recognizers", []).append(
    {
        "name": "PatientDossierNumberRecognizer",
        "supported_language": "fr",
        "patterns": [
            {
                "name": "dossier patient (lettre + chiffres)",
                "regex": r"\b[A-Z]\d{8,12}\b",
                "score": 0.6,
            }
        ],
        "context": ["dossier", "numero", "numéro", "patient", "identifiant"],
        "supported_entity": "PATIENT_ID",
        "type": "custom",
    }
)

with open(PATH, "w", encoding="utf-8") as f:
    yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

print("default_recognizers.yaml patché avec succès (fr + PatientDossierNumberRecognizer)")
