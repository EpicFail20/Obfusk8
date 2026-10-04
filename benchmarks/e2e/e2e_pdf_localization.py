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
"""
EXT-35 / D-026 end to end, THROUGH THE FULL CHAIN (Traefik, oauth2-proxy,
Keycloak, app under the enforcing seccomp profile, presidio-analyzer): a
synthetic PDF whose name contains a glyph with no Unicode mapping is
detected, the search for it is truncated by the NUL, and the D-026 fallback
must still cover the whole name. Checked on the DOWNLOADED redacted PDF: the
name must be gone, the surrounding words kept. No residual loss is expected,
so no reviewer warning. Each case is finalized: audit.log gets one metadata
line each (job ids printed; the first one carries pdf_localization_issues
with a "recovered" count), check it on the host.

Run it like e2e_document_flow.py (throwaway client container of the app
image, BENCH_* variables, E2E_PACE under the upload rate limit). Exit code 1
on any failure.
"""

import os
import re
import sys
import time
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from obfusk8_client import BASE_URL as BASE
from obfusk8_client import login

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
PACE = float(os.environ.get("E2E_PACE", "13"))
# Synthetic name. U+200B has no glyph in DejaVu Sans Mono: extracted as U+0000. Followed by a
# space, the analyzer still finds the whole name (measured in the EXT-35 bench, variant largeur_nulle),
# but the NUL truncates page.search_for: only "Chloé" gets a rectangle.
CASES = {
    "glyphe_non_mappe": "Compte rendu. Le patient Louis Gauthier est suivi par le Dr Chloé\u200b Boyer.",
    "texte_ordinaire": "Compte rendu. Le patient Louis Gauthier est suivi par le Dr Chloé Boyer.",
}
HTTP_OK = 200


def make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="mono", fontfile=MONO)
    page.insert_text((50, 80), text, fontname="mono", fontsize=10)
    out: bytes = doc.tobytes()
    doc.close()
    return out


session = login()
failures = 0
for name, text in CASES.items():
    time.sleep(PACE)
    response = session.post(
        f"{BASE}/api/detect", files={"file": (f"{name}.pdf", make_pdf(text))}, data={"theme": ""}, timeout=120
    )
    job = re.search(r'name="job_id" value="([0-9a-f]{32})"', response.text)
    warned = "localization-warning" in response.text
    time.sleep(PACE)
    final = session.post(
        f"{BASE}/api/finalize", data={"job_id": job.group(1) if job else "", "format": "json"}, timeout=120
    )
    redacted = ""
    if final.status_code == HTTP_OK:
        download = session.get(f"{BASE}{final.json()['download_url']}", timeout=60)
        doc = fitz.open(stream=download.content, filetype="pdf")
        redacted = "".join(page.get_text() for page in doc)
        doc.close()
    covered = "Boyer" not in redacted and "Chlo" not in redacted and "Compte rendu" in redacted
    ok = response.status_code == HTTP_OK and bool(job) and not warned and covered
    failures += not ok
    print(
        f"{name:18} detect={response.status_code} warning={warned} finalize={final.status_code} "
        f"name_gone={covered} job_id={job.group(1) if job else '-'} {'OK' if ok else 'FAIL'}"
    )
sys.exit(1 if failures else 0)
