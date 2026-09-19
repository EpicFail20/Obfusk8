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
import sys, importlib.util, csv, io

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# 2500 rows, a single column with a different name per row -> verifies
# that a row BEYOND the display threshold (2000) is still redacted at
# finalization time, even if never visible in the preview.
buf = io.StringIO()
w = csv.writer(buf)
w.writerow(["Nom"])
for i in range(2500):
    w.writerow([f"Dupont{i:04d} Test"])
raw = buf.getvalue().encode("utf-8")

resp = m._handle_detect_csv(raw, "medical", m.THEMES.get("medical"), "test-trunc", "hashtrunc", "test@example.com", len(raw)/1e6)
job = m.PENDING_JOBS["test-trunc"]
print(f"{len(job['detections'])} detections au total (sur 2500 lignes)")

# Does row 2400 (index 2399, well beyond the 2000 display cap) have a detection?
row_2400_detected = any(d["block_id"] == "2399:0" for d in job["detections"])
print("Ligne 2400 (au-dela de l'apercu) a une detection:", row_2400_detected)

summary, output_path, _ = m._finalize_csv_job(job, "test-trunc", set())
out_text = open(output_path, encoding="utf-8-sig").read()
lines = out_text.splitlines()
print("Ligne 2400 dans le fichier final:", repr(lines[2400]))
print("Contient encore 'Dupont2399' en clair:", "Dupont2399" in out_text)
