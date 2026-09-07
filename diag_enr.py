import fitz
import re
import requests

def normalize_allcaps(text):
    return re.sub(r"\b[A-ZÀ-Ý]{2,}\b", lambda m: m.group(0).capitalize(), text)

_DASH_VARIANTS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
def normalize_dashes(text):
    return text.translate({ord(c): "-" for c in _DASH_VARIANTS})

allow_list = ["Positive", "Conclusion", "Dossier", "Veuillez", "Sexe", "Enregistré"]

doc = fitz.open("/data/tmp/test.pdf")

for page_index, page in enumerate(doc):
    page_text = page.get_text()
    normalized = normalize_dashes(normalize_allcaps(page_text))

    if "nregistr" not in normalized.lower():
        continue

    # SANS allow_list
    r1 = requests.post("http://presidio-analyzer:3000/analyze", json={
        "text": normalized, "language": "fr", "score_threshold": 0.4
    }, timeout=30)
    e1 = r1.json()

    # AVEC allow_list (comme le fait vraiment l'app)
    r2 = requests.post("http://presidio-analyzer:3000/analyze", json={
        "text": normalized, "language": "fr", "score_threshold": 0.4, "allow_list": allow_list
    }, timeout=30)
    e2 = r2.json()

    idx = 0
    low = normalized.lower()
    while True:
        idx = low.find("nregistr", idx)
        if idx == -1:
            break
        context = normalized[max(0, idx-15):idx+35]
        print(f"Page {page_index} | contexte={context!r}")
        cov1 = [e for e in e1 if e["start"] <= idx < e["end"] or (e["start"]-3 <= idx <= e["end"]+3)]
        cov2 = [e for e in e2 if e["start"] <= idx < e["end"] or (e["start"]-3 <= idx <= e["end"]+3)]
        print("  SANS allow_list:", [(normalized[e['start']:e['end']], e['entity_type'], e['score']) for e in cov1])
        print("  AVEC allow_list:", [(normalized[e['start']:e['end']], e['entity_type'], e['score']) for e in cov2])
        idx += 1
