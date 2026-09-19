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

PII_STRINGS = [
    "Isabelle FONTAINE", "03/11/1980", "06 11 22 33 44",
    "Gregoire VASSEUR", "Sylvie MERCIER",
    "Olivier ROUSSEAU", "olivier.rousseau@example-fictif.test",
    "Frederic LAMBERT", "22/02/1990",
    "Camille GIRARD",
    "Antoine BOUCHER", "F778899123",
    "Valerie ANDRE", "07 55 44 33 22",
    "Nicolas PETIT", "Julie MOREL",
]

m._handle_detect_docx(raw, "medical", m.THEMES.get("medical"), job_id, "hashdocx", "test@example.com", len(raw) / 1e6)
job = m.PENDING_JOBS[job_id]
print(f"{len(job['detections'])} détections")
for d in job["detections"][:5]:
    print("  ", d)

summary, output_path, _ = m._finalize_docx_job(job, job_id, set())
print("\nRésumé caviardage:", summary)
print("Fichier de sortie:", output_path)

print("\n=== Balayage EXHAUSTIF de toutes les entrées du zip de sortie ===")
leaks = []
with zipfile.ZipFile(output_path) as z:
    for name in z.namelist():
        data = z.read(name)
        try:
            text = data.decode("utf-8", errors="ignore")
        except Exception:
            text = ""
        for pii in PII_STRINGS:
            if pii in text or pii.encode("utf-8") in data:
                leaks.append((name, pii))

if leaks:
    print(f"FUITES TROUVÉES ({len(leaks)}):")
    for name, pii in leaks:
        print(f"  {name} -> {pii!r}")
else:
    print("AUCUNE fuite trouvée dans aucune entrée du zip (toutes parties confondues).")

print("\n=== Vérification métadonnées (docProps/core.xml) ===")
with zipfile.ZipFile(output_path) as z:
    if "docProps/core.xml" in z.namelist():
        print(z.read("docProps/core.xml").decode("utf-8"))
    else:
        print("(absent)")

print("\n=== Liste complète des entrées du zip de sortie ===")
with zipfile.ZipFile(output_path) as z:
    for name in z.namelist():
        print(" ", name)
