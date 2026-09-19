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
import sys, importlib.util, uuid, csv, io

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FIXTURE = "/data/tmp/csv_fixture.csv"
raw = open(FIXTURE, "rb").read()
job_id = str(uuid.uuid4())

PII_STRINGS = [
    "DUPUIS", "Marc", "marc.dupuis@example-fictif.test", "06 22 33 44 55", "14/03/1972", "M772233114",
    "LEFEBVRE", "Anne", "anne.lefebvre@example-fictif.test", "07 88 99 00 11", "29/07/1965", "A650729987",
    "NOEL", "Chantal", "chantal.noel@example-fictif.test", "06 00 11 22 33", "01/01/1990", "N900101555",
    "ROY", "Bernard", "bernard.roy@example-fictif.test", "06 44 55 66 77", "12/12/1955", "R551212333",
]

m._handle_detect_csv(raw, "medical", m.THEMES.get("medical"), job_id, "hashcsv", "test@example.com", len(raw) / 1e6)
job = m.PENDING_JOBS[job_id]
print(f"{len(job['detections'])} détections, délimiteur={job['csv_delimiter']!r}")
for d in job["detections"]:
    print("  ", d["block_id"], d["entity_type"], d.get("score"))

summary, output_path, _ = m._finalize_csv_job(job, job_id, set())
print("\nRésumé caviardage:", summary)
print("Fichier de sortie:", output_path)

raw_out = open(output_path, "rb").read()
text_out = raw_out.decode("utf-8-sig")
print("\n=== Contenu intégral du CSV de sortie ===")
print(text_out)

print("=== Balayage exhaustif (octets bruts) ===")
leaks = [pii for pii in PII_STRINGS if pii.encode("utf-8") in raw_out]
print("FUITES:", leaks if leaks else "AUCUNE")

print("\n=== Vérification round-trip (nombre de lignes/colonnes cohérent) ===")
rows_out = list(csv.reader(io.StringIO(text_out)))
for r in rows_out:
    print(r)
