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
import sys, importlib.util, time

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("MAX_DETECTION_SECONDS:", m.MAX_DETECTION_SECONDS, flush=True)
theme = m.THEMES.get("medical")

# Enough blocks to largely exceed the budget if the safeguard did not
# work (at ~0.3s per batch of 200 blocks, ~300+ batches are needed to
# exceed 90s -> 60000+ blocks).
n_blocks = 70000
block_texts = [(i, f"NOM{i} Prenom{i} user{i}@example.test 06{i:08d} Paris") for i in range(n_blocks)]

t0 = time.time()
try:
    dets = m._detect_text_blocks(block_texts, theme=theme)
    elapsed = time.time() - t0
    print(f"Termine sans erreur en {elapsed:.1f}s ({len(dets)} detections) -- INATTENDU, le budget aurait du interrompre avant", flush=True)
except Exception as exc:
    elapsed = time.time() - t0
    print(f"Interrompu apres {elapsed:.1f}s: {type(exc).__name__}: {exc}", flush=True)
