import fitz
import re
import requests

def normalize_allcaps(text):
    return re.sub(r"\b[A-ZÀ-Ý]{2,}\b", lambda m: m.group(0).capitalize(), text)

_DASH_VARIANTS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
def normalize_dashes(text):
    return text.translate({ord(c): "-" for c in _DASH_VARIANTS})

doc = fitz.open("/data/tmp/test.pdf")
page = doc[0]
page_text = page.get_text()
normalized = normalize_dashes(normalize_allcaps(page_text))

allow_list = ["Positive", "Conclusion", "Dossier", "Veuillez", "Sexe"]

# Sans allow_list
r1 = requests.post("http://presidio-analyzer:3000/analyze", json={
    "text": normalized, "language": "fr", "score_threshold": 0.4
}, timeout=30)
e1 = r1.json()

# Avec allow_list
r2 = requests.post("http://presidio-analyzer:3000/analyze", json={
    "text": normalized, "language": "fr", "score_threshold": 0.4, "allow_list": allow_list
}, timeout=30)
e2 = r2.json()

idx = normalized.find("Veuillez")
print("Contexte:", repr(normalized[max(0,idx-10):idx+20]))
print()
print("SANS allow_list, entite couvrant 'Veuillez':", [e for e in e1 if e["start"] <= idx < e["end"]])
print("AVEC allow_list, entite couvrant 'Veuillez':", [e for e in e2 if e["start"] <= idx < e["end"]])
