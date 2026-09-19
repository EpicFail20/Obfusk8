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

# 300000 ALMOST EMPTY cells (a single non-empty one per row) -> detection
# skips almost all cells (nothing to analyze), so this should NOT trigger
# the time budget -- isolates the pure cost of HTML page rendering,
# independent of detection.
n_cols = 15
n_rows = min(m.MAX_CSV_ROWS - 1, m.MAX_CSV_CELLS // n_cols - 1)
buf = io.StringIO()
w = csv.writer(buf)
w.writerow(["Nom"] + [f"Col{j}" for j in range(n_cols-1)])
for i in range(n_rows):
    w.writerow([f"x{i}"] + [""] * (n_cols - 1))
raw = buf.getvalue().encode("utf-8")
print(f"CSV: {(n_rows+1)*n_cols} cellules (quasi vides), {len(raw)/1024:.0f} Ko", flush=True)

t0 = time.time()
try:
    resp = m._handle_detect_csv(raw, "medical", m.THEMES.get("medical"), "test-empty", "hashempty", "test@example.com", len(raw)/1e6)
    elapsed = time.time() - t0
    body = resp.body if hasattr(resp, "body") else b""
    print(f"Genere en {elapsed:.2f}s", flush=True)
    print(f"Taille de la page HTML: {len(body)/1024/1024:.2f} Mo", flush=True)
    print(f"<td>: {body.count(b'<td')}  <tr>: {body.count(b'<tr')}", flush=True)
except Exception as exc:
    print(f"Exception apres {time.time()-t0:.2f}s: {exc}", flush=True)
