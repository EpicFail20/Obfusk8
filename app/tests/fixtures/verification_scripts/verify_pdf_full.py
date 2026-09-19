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
import sys, importlib.util, uuid, zlib

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

import fitz

# Path to the input PDF passed as an argument — MUST BE a fictitious
# document: this script was historically run on a real lab report,
# later found versioned in the repository and copied into the
# production work directory (audit, section 11ter.7). Never again let
# real patient data enter the repository or this VM.
raw = open(sys.argv[1], "rb").read()
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

# Also checks that there are no incremental updates (old revisions of
# the file with multiple %%EOF/trailer, another classic vector)
eof_count = raw_out.count(b"%%EOF")
print("Occurrences de %%EOF dans le fichier de sortie:", eof_count, "(1 attendu pour un fichier sans historique incrémental)")
