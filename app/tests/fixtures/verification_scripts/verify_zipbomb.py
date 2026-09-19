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

import sys, importlib.util, uuid, time, resource

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

raw = open("/data/tmp/zipbomb_entries.docx", "rb").read()
print(f"Taille du fichier: {len(raw)/1024/1024:.2f} Mo (limite MAX_UPLOAD_MB={m.MAX_UPLOAD_MB} Mo)")

mem_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

print("\n=== _validate_docx_zip seule (la fonction de protection anti zip-bomb) ===")
t0 = time.time()
try:
    kind = m._validate_docx_zip(raw)
    elapsed = time.time() - t0
    print(f"OK en {elapsed:.3f}s, kind={kind}")
except Exception as exc:
    elapsed = time.time() - t0
    print(f"Exception après {elapsed:.3f}s: {type(exc).__name__}: {exc}")

print("\n=== _detect_file_kind (vrai point d'entrée, appelé sur chaque upload) ===")
t0 = time.time()
try:
    kind = m._detect_file_kind(raw)
    elapsed = time.time() - t0
    print(f"OK en {elapsed:.3f}s, kind={kind}")
except Exception as exc:
    elapsed = time.time() - t0
    print(f"Exception après {elapsed:.3f}s: {type(exc).__name__}: {exc}")

print("\n=== Pipeline complet _handle_detect_docx (vrai point d'entrée HTTP) ===")
job_id = str(uuid.uuid4())
t0 = time.time()
try:
    m._handle_detect_docx(raw, "medical", m.THEMES.get("medical"), job_id, "hashzip", "test@example.com", len(raw)/1e6)
    elapsed = time.time() - t0
    print(f"OK en {elapsed:.3f}s")
except Exception as exc:
    elapsed = time.time() - t0
    print(f"Exception après {elapsed:.3f}s: {type(exc).__name__}: {exc}")

mem_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(f"\nRSS max avant: {mem_before/1024:.1f} Mo, après: {mem_after/1024:.1f} Mo, delta: {(mem_after-mem_before)/1024:.1f} Mo")
