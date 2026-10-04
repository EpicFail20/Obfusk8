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
EXT-35 end to end, THROUGH THE FULL CHAIN (Traefik, oauth2-proxy, Keycloak,
app under the enforcing seccomp profile, presidio-analyzer): a synthetic PDF
whose name contains a glyph with no Unicode mapping must produce the
reviewer warning; a plain PDF must not. Both are finalized, so audit.log
gets one metadata line each: check them on the host afterwards (the job ids
are printed), e.g. `grep <job_id> /var/log/anonymiseur-audit/audit.log`.

Run it like e2e_document_flow.py (throwaway client container of the app
image, BENCH_* variables, E2E_PACE under the upload rate limit). Exit code 1
on any failure.
"""

import html
import json
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
STRINGS = json.loads(
    (Path(__file__).resolve().parents[2] / "app" / "i18n" / f"{os.environ.get('UI_LANG', 'fr')}.json").read_text(
        encoding="utf-8"
    )
)
PACE = float(os.environ.get("E2E_PACE", "13"))
# Synthetic name. U+200B has no glyph in DejaVu Sans Mono: extracted as U+0000. Followed by a
# space, the analyzer still finds the whole name (measured in the EXT-35 bench, variant largeur_nulle),
# but the NUL truncates page.search_for: only "Chloé" gets a rectangle.
CASES = {
    "glyphe_non_mappe": ("Compte rendu. Le patient Louis Gauthier est suivi par le Dr Chloé\u200b Boyer.", True),
    "texte_ordinaire": ("Compte rendu. Le patient Louis Gauthier est suivi par le Dr Chloé Boyer.", False),
}


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
marker = html.escape(STRINGS["pdf_localization_warning"].split("{types}")[1])
for name, (text, expect_warning) in CASES.items():
    time.sleep(PACE)
    response = session.post(
        f"{BASE}/api/detect", files={"file": (f"{name}.pdf", make_pdf(text))}, data={"theme": ""}, timeout=120
    )
    job = re.search(r'name="job_id" value="([0-9a-f]{32})"', response.text)
    warned = "localization-warning" in response.text and marker in response.text
    # The PDF review page shows page images, never the document text: the
    # name must appear nowhere in it (warning included).
    leaked = "Boyer" in response.text
    time.sleep(PACE)
    final = session.post(
        f"{BASE}/api/finalize", data={"job_id": job.group(1) if job else "", "format": "json"}, timeout=120
    )
    ok = (
        response.status_code == 200
        and bool(job)
        and warned == expect_warning
        and not leaked
        and final.status_code == 200
    )
    failures += not ok
    print(
        f"{name:18} detect={response.status_code} warning={warned} (expected {expect_warning}) "
        f"finalize={final.status_code} job_id={job.group(1) if job else '-'} {'OK' if ok else 'FAIL'}"
    )
sys.exit(1 if failures else 0)
