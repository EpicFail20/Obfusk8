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
import sys, importlib.util, uuid

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

with open("/data/tmp/xxe_canary_secret.txt", "w") as f:
    f.write("SECRET_CANARY_VALUE_1234567890")

raw = open("/data/tmp/xxe_fixture.docx", "rb").read()
job_id = str(uuid.uuid4())

print("=== Détection (via _handle_detect_docx, vrai point d'entrée) ===")
try:
    m._handle_detect_docx(raw, "medical", m.THEMES.get("medical"), job_id, "hashxxe", "test@example.com", len(raw) / 1e6)
    job = m.PENDING_JOBS[job_id]
    print(f"OK, {len(job['detections'])} détection(s), pas de crash")
    for d in job["detections"]:
        print("  ", d)
except Exception as exc:
    print(f"EXCEPTION: {type(exc).__name__}: {exc}")
    job = None

if job is not None:
    summary, output_path, _ = m._finalize_docx_job(job, job_id, set())
    print("\nRésumé:", summary)
    print("Sortie:", output_path)

    import zipfile
    with zipfile.ZipFile(output_path) as z:
        doc_xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        foot_xml = z.read("word/footnotes.xml").decode("utf-8", errors="replace") if "word/footnotes.xml" in z.namelist() else ""

    print("\n--- document.xml (sortie) ---")
    print(doc_xml)
    print("\n--- footnotes.xml (sortie) ---")
    print(foot_xml)

    print("\nCanary présent dans document.xml:", "SECRET_CANARY_VALUE" in doc_xml)
    print("Canary présent dans footnotes.xml:", "SECRET_CANARY_VALUE" in foot_xml)
    print("Contenu /etc/passwd présent:", "root:" in doc_xml)
