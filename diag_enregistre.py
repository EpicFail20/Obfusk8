import fitz
import re
import requests

def normalize_allcaps(text):
    return re.sub(r"\b[A-ZÀ-Ý]{2,}\b", lambda m: m.group(0).capitalize(), text)

_DASH_VARIANTS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
def normalize_dashes(text):
    return text.translate({ord(c): "-" for c in _DASH_VARIANTS})

doc = fitz.open("/data/tmp/test.pdf")

for page_index, page in enumerate(doc):
    page_text = page.get_text()
    normalized = normalize_dashes(normalize_allcaps(page_text))

    if "enregistr" not in normalized.lower():
        continue

    r = requests.post(
        "http://presidio-analyzer:3000/analyze",
        json={"text": normalized, "language": "fr", "score_threshold": 0.4},
        timeout=30,
    )
    entities = r.json()

    idx = 0
    low = normalized.lower()
    while True:
        idx = low.find("enregistr", idx)
        if idx == -1:
            break
        covering = [e for e in entities if e["start"] <= idx < e["end"]]
        context = normalized[max(0, idx-15):idx+30]
        print(f"Page {page_index} | contexte={context!r}")
        if covering:
            for e in covering:
                print(f"    -> ENTITE EXACTE={normalized[e['start']:e['end']]!r} type={e['entity_type']} score={e['score']}")
        else:
            print("    -> non detecte")
        idx += 1
