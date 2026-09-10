import sys, importlib.util, uuid, zlib

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

import fitz

raw = open("/data/tmp/test_med.pdf", "rb").read()
job_id = str(uuid.uuid4())

m._handle_detect_pdf(raw, "medical", m.THEMES.get("medical"), job_id, "hashpdf", "test@example.com", len(raw)/1e6)
job = m.PENDING_JOBS[job_id]

orig_doc = fitz.open(stream=raw, filetype="pdf")
pii_values = set()
for d in job["detections"]:
    page = orig_doc[d["page"]]
    txt = page.get_text("text", clip=fitz.Rect(d["page_rect"])).strip()
    if len(txt) >= 4:
        pii_values.add(txt)
orig_doc.close()
print(f"{len(job['detections'])} détections, {len(pii_values)} chaînes PII réelles extraites")

summary, output_path, _ = m._finalize_pdf_job(job, job_id, set(), [])
print("Résumé:", summary)

fixed = fitz.open(str(output_path))
print("Metadata:", fixed.metadata)
print("xref_length:", fixed.xref_length())
print("XMP présent:", bool(fixed.xref_xml_metadata()))

raw_out = open(str(output_path), "rb").read()
doc2 = fitz.open(stream=raw_out, filetype="pdf")
leaks = []
for x in range(1, doc2.xref_length()):
    try:
        s = doc2.xref_stream_raw(x)
    except Exception:
        s = None
    if not s:
        continue
    obj_dict = doc2.xref_object(x, compressed=False) or ""
    blob = s
    if "FlateDecode" in obj_dict:
        try:
            blob = zlib.decompress(s)
        except Exception:
            pass
    try:
        blob_txt = blob.decode("latin-1", errors="replace")
    except Exception:
        continue
    for val in pii_values:
        if val in blob_txt:
            leaks.append((x, val))

print("\nFuites (tous objets, pas seulement pages visibles):", leaks if leaks else "AUCUNE")

# Vérifie aussi qu'il n'y a pas de mises à jour incrémentales (anciennes
# révisions du fichier avec plusieurs %%EOF/trailer, autre vecteur classique)
eof_count = raw_out.count(b"%%EOF")
print("Occurrences de %%EOF dans le fichier de sortie:", eof_count, "(1 attendu pour un fichier sans historique incrémental)")
