import fitz
import re
import json
import requests

def normalize_allcaps(text):
    return re.sub(r"\b[A-ZÀ-Ý]{2,}\b", lambda m: m.group(0).capitalize(), text)

_DASH_VARIANTS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
def normalize_dashes(text):
    return text.translate({ord(c): "-" for c in _DASH_VARIANTS})

# Charge les VRAIS fichiers de thème, comme le fait main.py
with open("/app/themes/medical.json", encoding="utf-8") as f:
    medical_theme = json.load(f)

common_path = "/app/themes/common.json"
common_recognizers = []
try:
    with open(common_path, encoding="utf-8") as f:
        common_recognizers = json.load(f).get("ad_hoc_recognizers", [])
except FileNotFoundError:
    pass

print("allow_list charge:", medical_theme.get("allow_list"))
print("excluded_entity_types charge:", medical_theme.get("excluded_entity_types"))
print("nombre de recognizers communs:", len(common_recognizers))
print("nombre de recognizers medical:", len(medical_theme.get("ad_hoc_recognizers", [])))
print()

doc = fitz.open("/data/tmp/test.pdf")
page = doc[2]  # page Cerba
page_text = page.get_text()
normalized = normalize_dashes(normalize_allcaps(page_text))

# Reconstruit EXACTEMENT le payload que _analyze_text enverrait
ad_hoc = list(common_recognizers) + list(medical_theme.get("ad_hoc_recognizers", []))
payload = {"text": normalized, "language": "fr"}
if ad_hoc:
    payload["ad_hoc_recognizers"] = ad_hoc
if medical_theme.get("allow_list"):
    payload["allow_list"] = medical_theme["allow_list"]
if medical_theme.get("score_threshold") is not None:
    payload["score_threshold"] = medical_theme["score_threshold"]

r = requests.post("http://presidio-analyzer:3000/analyze", json=payload, timeout=30)
entities = r.json()

# Applique le filtrage excluded_entity_types comme le fait main.py
excluded = set(medical_theme.get("excluded_entity_types", []))
entities_filtered = [e for e in entities if e.get("entity_type") not in excluded]

idx = normalized.lower().find("nregistr")
print("Contexte:", repr(normalized[max(0,idx-15):idx+35]))
print()
print("Entites AVANT filtrage excluded_entity_types, autour de cette position:")
for e in entities:
    if e["start"] <= idx <= e["end"] + 5:
        print(" ", repr(normalized[e["start"]:e["end"]]), e["entity_type"], e["score"])
print()
print("Entites APRES filtrage (ce que finalize/detect utilisent vraiment):")
for e in entities_filtered:
    if e["start"] <= idx <= e["end"] + 5:
        print(" ", repr(normalized[e["start"]:e["end"]]), e["entity_type"], e["score"])
