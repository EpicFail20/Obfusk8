import fitz
import re
import requests

def normalize_allcaps(text):
    return re.sub(r"\b[A-ZÀ-Ý]{2,}\b", lambda m: m.group(0).capitalize(), text)

_DASH_VARIANTS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
def normalize_dashes(text):
    return text.translate({ord(c): "-" for c in _DASH_VARIANTS})

MEDICAL_RECOGNIZERS = [
    {
        "name": "PatientDossierNumberRecognizer",
        "supported_language": "fr",
        "patterns": [{"name": "dossier patient", "regex": r"\b[A-Z]\d{8,12}\b", "score": 0.6}],
        "context": ["dossier", "numero", "patient", "identifiant"],
        "supported_entity": "PATIENT_ID",
    },
]

doc = fitz.open("/data/tmp/test.pdf")

targets = ["BIOLOGIE", "Positive", "Conclusion", "Dossier", "SARS", "22D0755084"]

for page_index, page in enumerate(doc):
    page_text = page.get_text()
    normalized = normalize_dashes(normalize_allcaps(page_text))

    r = requests.post(
        "http://presidio-analyzer:3000/analyze",
        json={"text": normalized, "language": "fr", "score_threshold": 0.4, "ad_hoc_recognizers": MEDICAL_RECOGNIZERS},
        timeout=30,
    )
    entities = r.json()

    for target in targets:
        idx = normalized.find(target)
        idx_titled = normalized.find(target.title()) if idx == -1 else idx
        real_idx = idx if idx != -1 else idx_titled
        if real_idx == -1:
            continue
        covered = [e for e in entities if e["start"] <= real_idx < e["end"]]
        print(f"Page {page_index} | {target!r} trouvé à {real_idx} | texte normalisé environ: {normalized[max(0,real_idx-5):real_idx+25]!r}")
        if covered:
            for e in covered:
                print(f"    -> DETECTE comme {e['entity_type']} (score {e['score']})")
        else:
            print("    -> non detecte")
