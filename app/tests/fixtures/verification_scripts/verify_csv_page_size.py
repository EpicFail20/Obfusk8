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
import sys, importlib.util, time, csv, io

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

n_cols = 15
n_rows = 1333  # ~20000 cells, stays well under the time budget (90s)
buf = io.StringIO()
w = csv.writer(buf)
w.writerow(["Nom", "Prenom", "Email", "Telephone", "Ville"] + [f"Col{j}" for j in range(n_cols-5)])
for i in range(n_rows):
    w.writerow([f"NOM{i}", f"Prenom{i}", f"user{i}@example.test", f"06{i:08d}", "Paris"] + [f"val{i}_{j}" for j in range(n_cols-5)])
raw = buf.getvalue().encode("utf-8")
print(f"CSV: {(n_rows+1)*n_cols} cellules, {len(raw)/1024:.0f} Ko", flush=True)

t0 = time.time()
resp = m._handle_detect_csv(raw, "medical", m.THEMES.get("medical"), "test-page-size", "hashpage", "test@example.com", len(raw)/1e6)
elapsed = time.time() - t0
body = resp.body if hasattr(resp, "body") else b""
print(f"Genere en {elapsed:.2f}s", flush=True)
print(f"Taille de la page HTML: {len(body)/1024/1024:.2f} Mo", flush=True)
print(f"<td>: {body.count(b'<td')}  <tr>: {body.count(b'<tr')}", flush=True)
