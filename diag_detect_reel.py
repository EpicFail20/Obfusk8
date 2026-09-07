import sys
sys.path.insert(0, "/app")
import fitz
import main

doc = fitz.open("/data/tmp/test.pdf")
theme = main.THEMES.get("medical")

detections = main._detect_pdf(doc, theme=theme)

# Reconstruit le texte de chaque page pour localiser "enr" dans les coordonnées
for d in detections:
    page = doc[d["page"]]
    x0, y0, x1, y1 = d["page_rect"]
    rect = fitz.Rect(x0, y0, x1, y1)
    text_in_zone = page.get_textbox(rect)
    if "enr" in text_in_zone.lower() or "nregistr" in text_in_zone.lower():
        print(f"Page {d['page']} | texte de la zone={text_in_zone!r} | type={d['entity_type']} | group_key={d.get('group_key')!r}")

print()
print(f"Total detections: {len(detections)}")
