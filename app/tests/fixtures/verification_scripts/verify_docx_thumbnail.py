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
import sys, importlib.util, uuid, zipfile

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FIXTURE = "/data/tmp/docx_fixture.docx"
raw = open(FIXTURE, "rb").read()
job_id = str(uuid.uuid4())

with zipfile.ZipFile(FIXTURE) as z:
    print("Fixture d'entrée contient thumbnail:", "docProps/thumbnail.jpeg" in z.namelist())

m._handle_detect_docx(raw, "medical", m.THEMES.get("medical"), job_id, "hashdocx", "test@example.com", len(raw) / 1e6)
job = m.PENDING_JOBS[job_id]
print(f"{len(job['detections'])} détections")

summary, output_path, _ = m._finalize_docx_job(job, job_id, set())
print("Résumé caviardage:", summary)
print("Fichier de sortie:", output_path)

with zipfile.ZipFile(output_path) as z:
    names = z.namelist()
    print("\nSortie contient thumbnail:", "docProps/thumbnail.jpeg" in names)
    print("Relations racine (_rels/.rels):")
    print(z.read("_rels/.rels").decode("utf-8"))
    assert "docProps/thumbnail.jpeg" not in names, "REGRESSION: thumbnail still present"
    assert "thumbnail" not in z.read("_rels/.rels").decode("utf-8"), "REGRESSION: thumbnail relation still present"

# Reopen with python-docx to verify the file remains valid
from docx import Document as WordDocument
doc2 = WordDocument(str(output_path))
print("\nRéouverture OK, paragraphes non vides:", sum(1 for p in doc2.paragraphs if p.text.strip()))

print("\n=== OK: miniature retirée, fichier toujours ouvrable, détections/caviardage inchangés ===")
