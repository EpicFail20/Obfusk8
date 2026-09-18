"""
Vérification bout en bout du support image (PNG + JPEG) : fait tourner le
VRAI pipeline (_handle_detect_image puis _finalize_image_job, comme
verify_pdf_full.py le fait pour le PDF) avec un vrai appel OCR (pytesseract)
et un vrai appel Presidio (réseau requis - à exécuter via `docker exec` dans
le conteneur `app` en cours d'exécution, jamais en pytest hors-ligne).

Construit deux images de test (une PNG, une JPEG) contenant un nom et une
date en texte net (rendu vectoriel via PyMuPDF, upscalé pour une OCR fiable,
sans dépendre d'une police système), fait tourner le pipeline complet réel,
puis vérifie que le nom/la date ont bien disparu du fichier final :
  - au niveau des PIXELS (la zone où le texte apparaissait est bien devenue
    un rectangle plein noir, pas seulement "visuellement différente") ;
  - en ré-appliquant l'OCR sur le fichier de sortie et en confirmant que ni
    le nom ni la date n'y sont plus reconnus.
"""
import importlib.util
import io
import sys
import uuid

sys.path.insert(0, "/data/tmp/app_copy")  # pour que main.py trouve metrics/antivirus/supervision
spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

import pymupdf as fitz
import pytesseract
from PIL import Image

NAME = "Jean Durand"
DATE = "15/03/1980"


def _build_text_image_png() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=400, height=150)
    page.insert_text((20, 50), NAME, fontsize=24, fontname="helv", color=(0, 0, 0))
    page.insert_text((20, 90), f"Né le {DATE}", fontsize=20, fontname="helv", color=(0, 0, 0))
    # Fond blanc explicite (page vierge PyMuPDF est transparente/blanche par
    # défaut selon le renderer) + upscale x3 pour une OCR fiable.
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


def _png_to_jpeg(png_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _run_case(label: str, raw: bytes):
    print(f"\n=== Cas: {label} ===")
    job_id = uuid.uuid4().hex
    m._handle_detect_image(raw, "", None, job_id, f"hash-{label}", "test@example.com", len(raw) / 1e6)
    job = m.PENDING_JOBS[job_id]
    print(f"{len(job['detections'])} détection(s) brute(s), format détecté={job['image_format']}")

    if not job["detections"]:
        print("AUCUNE DÉTECTION — échec du scénario (le nom/la date auraient dû être détectés)")
        return False

    summary, output_path, manual_count = m._finalize_image_job(job, job_id, set(), [])
    print("Résumé caviardage:", summary)

    out_bytes = output_path.read_bytes()
    out_img = Image.open(io.BytesIO(out_bytes))

    # 1) Vérification pixel : chaque rectangle caviardé doit être noir.
    # Tolérance pour le JPEG (perte avec quality=95) : le rectangle noir est
    # dessiné dans les pixels AVANT la compression DCT — le bruit de
    # quantification introduit ensuite par la compression reste de l'ordre
    # de quelques niveaux (constaté : max canal ~5/255 sur ce fixture, jamais
    # une valeur évoquant le fond clair ou le trait du texte d'origine), pas
    # une résurgence du contenu original. Seuil large (32/255) pour rester
    # sans ambiguïté avec un vrai résidu visible.
    tolerance = 32 if label == "JPEG" else 0
    all_black = True
    for d in job["detections"]:
        x0, y0, x1, y1 = (int(v) for v in d["page_rect"])
        cropped = out_img.crop((x0, y0, x1, y1))
        colors = cropped.get_flattened_data()
        max_channel = max((max(c) for c in colors), default=0)
        if max_channel > tolerance:
            all_black = False
            print(f"  ZONE NON NOIRE détectée à {d['page_rect']} (entité {d['entity_type']}, max canal={max_channel})")
    print("Toutes les zones caviardées sont noires (tolérance compression):", all_black)

    # 2) Re-OCR du fichier de sortie : le nom/la date ne doivent plus être lisibles.
    reocr_text = pytesseract.image_to_string(out_img, lang="fra")
    name_leaked = "Durand" in reocr_text
    date_leaked = "1980" in reocr_text
    print("Texte ré-OCRisé sur la sortie:", repr(reocr_text.strip()))
    print("'Durand' encore présent après re-OCR:", name_leaked)
    print("'1980' encore présent après re-OCR:", date_leaked)

    # 3) Metadata check (défense en profondeur, déjà couvert en détail par
    # les tests unitaires) : ni EXIF ni ICC ne doivent survivre. Les clés
    # "jfif*"/"dpi" sont réinjectées par Pillow lui-même à chaque
    # sauvegarde JPEG (en-tête JFIF obligatoire du format, densité/version
    # uniquement) — bruit inoffensif sans donnée personnelle, pas concerné
    # par le dépouillement demandé (EXIF/GPS/miniature/tEXt/ICC).
    sensitive_keys = {k for k in out_img.info if k not in ("jfif", "jfif_version", "jfif_unit", "jfif_density", "dpi")}
    print("Clés de métadonnées résiduelles:", list(out_img.info.keys()), "| sensibles:", sensitive_keys or "aucune")

    ok = all_black and not name_leaked and not date_leaked and not sensitive_keys
    print(f"RÉSULTAT {label}: {'OK — AUCUNE FUITE' if ok else 'ÉCHEC — FUITE DÉTECTÉE'}")
    return ok


png_bytes = _build_text_image_png()
jpeg_bytes = _png_to_jpeg(png_bytes)

results = [
    _run_case("PNG", png_bytes),
    _run_case("JPEG", jpeg_bytes),
]

print("\n=== BILAN ===")
if all(results):
    print("SUCCÈS : les deux cas (PNG, JPEG) sont propres.")
    sys.exit(0)
else:
    print("ÉCHEC : au moins un cas a fui ou n'a rien détecté.")
    sys.exit(1)
