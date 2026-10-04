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
First quality benchmark of the DOCUMENT flow (phase 2, step G): masking rate
per format, measured THROUGH THE FULL CHAIN (Traefik, oauth2-proxy,
Keycloak, app under the enforcing seccomp profile, presidio-analyzer).
It replaces the phase 1 assumption "the document flow shares the false
negatives of the text API" with a measurement.

Prompts of the main quality corpus (up to PROMPTS_PER_VARIANT per variant,
`code` category excluded, built in memory) are rendered as:
  - PDF: one prompt per page, DejaVu Sans embedded (insert_textbox);
  - DOCX: one paragraph per prompt;
  - CSV: one row per prompt ("Ligne;Texte").
Each document goes through /api/detect; the zones offered for review are
read from the review page (as doc_zones_snapshot.py does) and every
annotated value is checked:
  - DOCX/CSV: covered when the union of the zone intervals of its block
    covers all its characters;
  - PDF: the value's characters are located on the generated page
    (PyMuPDF character boxes); covered when each visible character has at
    least half of its box inside a zone (the in-app rule of EXT-35).
    A character that cannot be located on the page (glyph missing from the
    font) is reported, never counted as covered.
Jobs are never finalized (nothing in audit.log); uploads are paced under
the upload rate limit (DOC_PACE).

Run it in a throwaway client container of the app image, as
doc_zones_snapshot.py; writes benchmarks/results/doc-quality-<timestamp>.*.
"""

import csv
import io
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import docx
import pymupdf as fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "quality"))
sys.path.insert(0, str(HERE))
from build_quality_corpus import records  # noqa: E402
from doc_zones_snapshot import _BLOCK_RE, FONT, pixel_zones, text_zones  # noqa: E402

from obfusk8_client import BASE_URL, HTTP_OK, login  # noqa: E402

RESULTS = HERE.parent / "results"
PROMPTS_PER_VARIANT = 8
THEMES = ["", "medical"]
PACE = float(os.environ.get("DOC_PACE", "13"))
COVERED_CHAR_FRACTION = 0.5
_PAGE_WIDTH_RE = re.compile(r'<div class="page-container" data-page="(\d+)" style="position:relative; width:(\d+)px;')


def selection() -> list[dict[str, Any]]:
    taken: Counter[str] = Counter()
    out = []
    for doc in records():
        if doc["category"] == "code" or taken[doc["variant"]] >= PROMPTS_PER_VARIANT:
            continue
        taken[doc["variant"]] += 1
        out.append(doc)
    return out


def make_pdf(texts: list[str]) -> bytes:
    doc = fitz.open()
    for text in texts:
        page = doc.new_page()
        page.insert_font(fontname="dejavu", fontfile=FONT)
        page.insert_textbox(
            fitz.Rect(50, 50, page.rect.width - 50, page.rect.height - 50), text, fontname="dejavu", fontsize=10
        )
    out: bytes = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def make_docx(texts: list[str]) -> bytes:
    document = docx.Document()
    for text in texts:
        document.add_paragraph(text)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def make_csv(texts: list[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Ligne", "Texte"])
    for index, text in enumerate(texts, 1):
        writer.writerow([str(index), text])
    return buf.getvalue().encode("utf-8")


def _text_coverage(doc: dict[str, Any], zones: list[dict[str, Any]], block: int) -> list[tuple[str, bool]]:
    spans = [(z["start"], z["end"]) for z in zones if z["block"] == block]
    results = []
    for entity in doc["entities"]:
        start, end = entity["start"], entity["end"]
        covered = all(any(s <= i < e for s, e in spans) for i in range(start, end) if not doc["text"][i].isspace())
        results.append((entity["type"], covered))
    return results


def _page_chars(page: Any) -> list[tuple[str, Any]]:
    chars: list[tuple[str, Any]] = []
    for block in page.get_text("rawdict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                chars.extend((ch["c"], fitz.Rect(ch["bbox"])) for ch in span["chars"])
            chars.append(("\n", None))
    return chars


def _locate(prompt: str, chars: list[tuple[str, Any]]) -> list[Any]:
    """Box of each character of the prompt on its page (None when invisible
    or not found). Tolerates what rendering changes: line wrapping (space
    or line feed), glyphs missing from the font (extracted as U+0000),
    format characters and combining marks."""
    boxes: list[Any] = [None] * len(prompt)
    j = 0
    for i, c in enumerate(prompt):
        if unicodedata.category(c) in ("Cf", "Mn"):
            if j < len(chars) and chars[j][0] in (c, "\x00"):
                boxes[i] = chars[j][1]
                j += 1
            continue
        if c.isspace():
            # A blank of the prompt consumes a blank of the page if there is
            # one (wrapping turns spaces into line feeds; an empty line of
            # the prompt is not extracted), never a visible character.
            if j < len(chars) and chars[j][0].isspace():
                j += 1
            continue
        while j < len(chars) and chars[j][0] not in (c, "\x00"):
            j += 1
        if j < len(chars):
            boxes[i] = chars[j][1]
            j += 1
    return boxes


def _pdf_coverage(doc: dict[str, Any], page: Any, rects: list[Any]) -> list[tuple[str, bool | None]]:
    boxes = _locate(doc["text"], _page_chars(page))
    results: list[tuple[str, bool | None]] = []
    for entity in doc["entities"]:
        verdict: bool | None = True
        for i in range(entity["start"], entity["end"]):
            c = doc["text"][i]
            if c.isspace() or unicodedata.category(c) in ("Cf", "Mn"):
                continue
            box = boxes[i]
            if box is None:
                verdict = None if verdict else verdict
                continue
            area = box.get_area()
            if area and not any((box & r).get_area() >= COVERED_CHAR_FRACTION * area for r in rects):
                verdict = False
                break
        results.append((entity["type"], verdict))
    return results


def _blocks(page: str, expected: int, name: str) -> None:
    """Guard: block i of the review page must be what the evaluation thinks
    (DOCX: paragraph i; CSV: cell 2i+3 after the header row)."""
    found = len(_BLOCK_RE.findall(page))
    if found != expected:
        raise RuntimeError(f"{name}: {found} rendered blocks, {expected} expected")


def _detect(session: Any, name: str, data: bytes, theme: str) -> str:
    time.sleep(PACE)
    response = session.post(f"{BASE_URL}/api/detect", files={"file": (name, data)}, data={"theme": theme}, timeout=300)
    if response.status_code != HTTP_OK or 'name="job_id"' not in response.text:
        raise RuntimeError(f"detect {name} theme={theme or '-'}: HTTP {response.status_code}")
    return response.text


def run() -> dict[str, Any]:
    docs = selection()
    texts = [d["text"] for d in docs]
    pdf = make_pdf(texts)
    session = login()
    results: dict[str, Any] = {}
    for theme in THEMES:
        verdicts: dict[str, list[tuple[dict[str, Any], str, bool | None]]] = {}
        page = _detect(session, "bench.docx", make_docx(texts), theme)
        _blocks(page, len(docs), "docx")
        zones = text_zones(page)
        verdicts["docx"] = [(d, t, v) for i, d in enumerate(docs) for t, v in _text_coverage(d, zones, i)]
        page = _detect(session, "bench.csv", make_csv(texts), theme)
        _blocks(page, 2 * (len(docs) + 1), "csv")
        zones = text_zones(page)
        verdicts["csv"] = [(d, t, v) for i, d in enumerate(docs) for t, v in _text_coverage(d, zones, 2 * i + 3)]
        page = _detect(session, "bench.pdf", pdf, theme)
        widths = {int(p): int(w) for p, w in _PAGE_WIDTH_RE.findall(page)}
        zones = pixel_zones(page)
        rendered = fitz.open(stream=pdf, filetype="pdf")
        try:
            pdf_verdicts = []
            for i, d in enumerate(docs):
                p = rendered[i]
                zoom = widths[i] / p.rect.width
                rects = [
                    fitz.Rect(
                        z["rect"][0] / zoom,
                        z["rect"][1] / zoom,
                        (z["rect"][0] + z["rect"][2]) / zoom,
                        (z["rect"][1] + z["rect"][3]) / zoom,
                    )
                    for z in zones
                    if z["page"] == i
                ]
                pdf_verdicts += [(d, t, v) for t, v in _pdf_coverage(d, p, rects)]
        finally:
            rendered.close()
        verdicts["pdf"] = pdf_verdicts
        results[theme or "aucun"] = {fmt: _summarize(v) for fmt, v in verdicts.items()}
        print(f"theme={theme or '-'} " + " ".join(f"{f}={r['masking']}" for f, r in results[theme or "aucun"].items()))
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "prompts": len(docs),
        "values": sum(len(d["entities"]) for d in docs),
        "variants": dict(Counter(d["variant"] for d in docs)),
        "results": results,
    }


def _summarize(verdicts: list[tuple[dict[str, Any], str, bool | None]]) -> dict[str, Any]:
    by_variant: dict[str, Counter[str]] = defaultdict(Counter)
    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    for doc, entity_type, verdict in verdicts:
        key = "unverifiable" if verdict is None else ("covered" if verdict else "exposed")
        totals[key] += 1
        by_variant[doc["variant"]][key] += 1
        by_type[entity_type][key] += 1
    checked = totals["covered"] + totals["exposed"]
    return {
        "masking": round(totals["covered"] / checked, 3) if checked else None,
        "totals": dict(totals),
        "by_variant": {k: dict(v) for k, v in sorted(by_variant.items())},
        "by_type": {k: dict(v) for k, v in sorted(by_type.items())},
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Banc d'essai — qualité du flux documents ({report['generated_at']})",
        "",
        f"{report['prompts']} invites du corpus principal, {report['values']} valeurs annotées, rendues en PDF, "
        "DOCX et CSV et passées par `/api/detect` à travers toute la pile. **Masquage** : la valeur est entièrement "
        "couverte par les zones proposées à la révision (rappel, sans type). « Non vérifiables » : valeurs PDF dont "
        "un caractère n'a pas pu être situé sur la page (glyphe absent de la police), exclues du taux.",
        "",
        "| Thème | Format | Masquage | Couvertes | Exposées | Non vérifiables |",
        "|---|---|---|---|---|---|",
    ]
    for theme, formats in report["results"].items():
        for fmt, r in formats.items():
            t = r["totals"]
            lines.append(
                f"| {theme} | {fmt} | {r['masking']} | {t.get('covered', 0)} | {t.get('exposed', 0)} | "
                f"{t.get('unverifiable', 0)} |"
            )
    lines += ["", "## Valeurs exposées par variante et par type (sans thème)", ""]
    for fmt, r in report["results"].get("aucun", {}).items():
        variants = ", ".join(f"{k} {v.get('exposed', 0)}" for k, v in r["by_variant"].items() if v.get("exposed"))
        types = ", ".join(f"{k} {v.get('exposed', 0)}" for k, v in r["by_type"].items() if v.get("exposed"))
        lines.append(f"- **{fmt}** : variantes — {variants or 'aucune'} ; types — {types or 'aucun'}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    report = run()
    RESULTS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    (RESULTS / f"doc-quality-{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (RESULTS / f"doc-quality-{stamp}.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
