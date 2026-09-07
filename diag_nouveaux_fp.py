import fitz
import re
import requests

def normalize_allcaps(text):
    return re.sub(r"\b[A-ZÀ-Ý]{2,}\b", lambda m: m.group(0).capitalize(), text)

_DASH_VARIANTS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
def normalize_dashes(text):
    return text.translate({ord(c): "-" for c in _DASH_VARIANTS})

doc = fitz.open("/data/tmp/test.pdf")

targets_words = ["Veuillez", "enregistr", "Sexe", "sexe"]

for page_index, page in enumerate(doc):
    page_text = page.get_text()
    normalized = normalize_dashes(normalize_allcaps(page_text))

    r = requests.post(
        "http://presidio-analyzer:3000/analyze",
        json={"text": normalized, "language": "fr", "score_threshold": 0.4},
        timeout=30,
    )
    entities = r.json()

    for word in targets_words:
        idx = 0
        while True:
            idx = normalized.find(word, idx)
            if idx == -1:
                break
            covering = [e for e in entities if e["start"] <= idx < e["end"]]
            for e in covering:
                exact_text = normalized[e["start"]:e["end"]]
                print(f"Page {page_index} | mot cherche={word!r} | ENTITE EXACTE={exact_text!r} | type={e['entity_type']} score={e['score']}")
            idx += 1
