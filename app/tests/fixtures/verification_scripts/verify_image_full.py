# Copyright (C) 2026 CARROLAGGI Xavier
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
"""
End-to-end verification of image support (PNG + JPEG): runs the REAL
pipeline (_handle_detect_image then _finalize_image_job, as
verify_pdf_full.py does for PDF) with a real OCR call (pytesseract)
and a real Presidio call (network required - to be run via `docker exec`
in the running `app` container, never offline via pytest).

Builds two test images (one PNG, one JPEG) containing a name and a
date in crisp text (vector rendering via PyMuPDF, upscaled for reliable
OCR, without depending on a system font), runs the real full pipeline,
then verifies that the name/date have indeed disappeared from the final
file:
  - at the PIXEL level (the zone where the text appeared has indeed
    become a solid black rectangle, not just "visually different");
  - by re-applying OCR to the output file and confirming that neither
    the name nor the date is recognized there anymore.
"""
import importlib.util
import io
import sys
import uuid

sys.path.insert(0, "/data/tmp/app_copy")  # so main.py finds metrics/antivirus/supervision
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
    # Explicit white background (a blank PyMuPDF page is
    # transparent/white by default depending on the renderer) + 3x
    # upscale for reliable OCR.
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

    # 1) Pixel check: every redacted rectangle must be black.
    # Tolerance for JPEG (lossy with quality=95): the black rectangle is
    # drawn into the pixels BEFORE DCT compression — the quantization
    # noise subsequently introduced by compression stays on the order of
    # a few levels (observed: max channel ~5/255 on this fixture, never
    # a value suggesting the light background or the original text
    # stroke), not a resurgence of the original content. Wide threshold
    # (32/255) to stay unambiguous versus a real visible residue.
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

    # 2) Re-OCR the output file: the name/date must no longer be readable.
    reocr_text = pytesseract.image_to_string(out_img, lang="fra")
    name_leaked = "Durand" in reocr_text
    date_leaked = "1980" in reocr_text
    print("Texte ré-OCRisé sur la sortie:", repr(reocr_text.strip()))
    print("'Durand' encore présent après re-OCR:", name_leaked)
    print("'1980' encore présent après re-OCR:", date_leaked)

    # 3) Metadata check (defense in depth, already covered in detail by
    # the unit tests): neither EXIF nor ICC must survive. The
    # "jfif*"/"dpi" keys are reinjected by Pillow itself on every JPEG
    # save (mandatory JFIF header of the format, density/version only)
    # — harmless noise with no personal data, not concerned by the
    # requested stripping (EXIF/GPS/thumbnail/tEXt/ICC).
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
