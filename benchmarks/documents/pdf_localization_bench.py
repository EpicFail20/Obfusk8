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
EXT-35 measurement: PDF detections that cannot be located on the page.

`_detect_pdf` (app/main.py) turns each entity found by Presidio into
redaction rectangles with `page.search_for(entity_text)`. When the search
finds nothing, no zone is created and nothing tells the reviewer: a silent
false negative. This bench counts, per PDF layout, how many detected
entities end up with no rectangle at all.

It runs the SAME steps as `_detect_pdf` pass 1, by importing app/main.py
and calling its own functions (normalizations, `_analyze_text` against the
real presidio-analyzer), then `page.search_for` on the same slice of the
page text — only the counting is added. Layouts (synthetic PDFs, fonts
embedded): several fonts (DejaVu Sans/Serif/Mono, base-14 Helvetica and
Times), ligatures (HarfBuzz shaping through insert_htmlbox), upper-case
text, values cut at the end of a line (narrow text box), two columns.
Content: prompts of the quality corpus (variants base, typographique,
majuscules, adresse_multiligne, plus the invisible-character variants for
the DejaVu fonts), so the values are synthetic.

Nothing detected is ever printed or written: only counts per entity type,
and per lost entity its type, length and the reason class (line break or
not) in the report.

Run it in a throwaway container of the app image, under the enforcing
seccomp profile, on the internal `backend` network (presidio-analyzer), with
the code under test mounted read-only and tmpfs instead of the volumes:
  docker run --rm --network obfusk8_backend --read-only --memory 1g \\
    --security-opt seccomp=seccomp/app-enforce.json --cap-drop ALL \\
    --tmpfs /tmp --tmpfs /data/tmp:uid=1000,gid=1000 --tmpfs /data/audit:uid=1000,gid=1000 \\
    -v "$PWD/app:/app-src:ro" -v "$PWD/benchmarks:/bench" -w /bench \\
    -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/app-src \\
    --entrypoint python ghcr.io/epicfail20/obfusk8-app:main documents/pdf_localization_bench.py
"""

import json
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf as fitz

import main

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent / "quality" / "corpus.jsonl"
RESULTS = HERE.parent / "results"
DEJAVU_DIR = "/usr/share/fonts/truetype/dejavu"
DEJAVU = {"sans": "DejaVuSans.ttf", "serif": "DejaVuSerif.ttf", "mono": "DejaVuSansMono.ttf"}
LATIN_VARIANTS = ("base", "typographique", "majuscules", "adresse_multiligne")
ALL_VARIANTS = (*LATIN_VARIANTS, "espaces_insecables", "largeur_nulle", "nfd", "chiffres_espaces")
PROMPTS_PER_VARIANT = 6
# Same minimum as _detect_pdf: shorter stripped values are skipped there.
MIN_ENTITY_CHARS = 3
THEMES = {"aucun": None, "medical": main.THEMES.get("medical")}


def corpus(variants: tuple[str, ...]) -> list[str]:
    taken: Counter[str] = Counter()
    out = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        doc = json.loads(line)
        if doc["category"] == "code" or doc["variant"] not in variants or taken[doc["variant"]] >= PROMPTS_PER_VARIANT:
            continue
        taken[doc["variant"]] += 1
        out.append(doc["text"])
    return out


def _new_page(doc: Any) -> Any:
    return doc.new_page(width=595, height=842)


def textbox_pdf(texts: list[str], fontname: str, fontfile: str | None, width: float, upper: bool = False) -> bytes:
    """One prompt per page in a text box; a narrow `width` forces line
    breaks inside values."""
    doc = fitz.open()
    for text in texts:
        page = _new_page(doc)
        if fontfile:
            page.insert_font(fontname=fontname, fontfile=fontfile)
        page.insert_textbox(
            fitz.Rect(40, 40, 40 + width, 800), text.upper() if upper else text, fontname=fontname, fontsize=10
        )
    out: bytes = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def columns_pdf(texts: list[str]) -> bytes:
    """Two prompts side by side per band: lines of both columns share the
    same baselines."""
    doc = fitz.open()
    for i in range(0, len(texts), 2):
        page = _new_page(doc)
        page.insert_font(fontname="dvs", fontfile=f"{DEJAVU_DIR}/{DEJAVU['sans']}")
        for column, text in enumerate(texts[i : i + 2]):
            x = 40 + column * 270
            page.insert_textbox(fitz.Rect(x, 40, x + 250, 800), text, fontname="dvs", fontsize=10)
    out: bytes = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def ligature_pdf(texts: list[str], font: str) -> bytes:
    """insert_htmlbox shapes text with HarfBuzz: ff/fi/fl become ligature
    glyphs, as in PDFs produced by word processors."""
    # Font loaded from memory: opening the font DIRECTORY as an archive
    # (fz_open_directory) fails under the enforcing seccomp profile.
    archive = fitz.Archive()
    archive.add((Path(f"{DEJAVU_DIR}/{DEJAVU[font]}").read_bytes(), DEJAVU[font]))
    css = f"@font-face {{font-family: dv; src: url({DEJAVU[font]});}} * {{font-family: dv; font-size: 10pt;}}"
    doc = fitz.open()
    for text in texts:
        page = _new_page(doc)
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>")
        # Ligature-prone fictitious names, so every page has some.
        safe += "<br>Contact : Steffi Griffon, Raffaella Chauffier, Florian Affleck."
        page.insert_htmlbox(fitz.Rect(40, 40, 555, 800), safe, css=css, archive=archive)
    out: bytes = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


Scenario = tuple[str, Callable[[], bytes]]


def _textbox_builder(texts: list[str], fontname: str, fontfile: str) -> Callable[[], bytes]:
    def build() -> bytes:
        return textbox_pdf(texts, fontname, fontfile, 515)

    return build


def scenarios() -> list[Scenario]:
    latin, every = corpus(LATIN_VARIANTS), corpus(ALL_VARIANTS)
    out: list[Scenario] = []
    for key, filename in DEJAVU.items():
        out.append((f"dejavu-{key}", _textbox_builder(every, f"dv{key}", f"{DEJAVU_DIR}/{filename}")))
    out += [
        ("base14-helvetica", lambda: textbox_pdf(latin, "helv", None, 515)),
        ("base14-times", lambda: textbox_pdf(latin, "tiro", None, 515)),
        ("majuscules", lambda: textbox_pdf(every, "dvs", f"{DEJAVU_DIR}/{DEJAVU['sans']}", 515, upper=True)),
        ("coupure-fin-de-ligne", lambda: textbox_pdf(every, "dvs", f"{DEJAVU_DIR}/{DEJAVU['sans']}", 150)),
        ("colonnes", lambda: columns_pdf(every)),
        ("ligatures-serif", lambda: ligature_pdf(latin, "serif")),
        ("ligatures-sans", lambda: ligature_pdf(latin, "sans")),
    ]
    return out


def _page_chars(page: Any) -> list[tuple[str, Any]]:
    """Characters of the page with their boxes, in the order of
    page.get_text() (blocks, lines, spans; a line break after each line)."""
    chars: list[tuple[str, Any]] = []
    for block in page.get_text("rawdict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                chars.extend((ch["c"], fitz.Rect(ch["bbox"])) for ch in span["chars"])
            chars.append(("\n", None))
    return chars


def _align(text: str, chars: list[tuple[str, Any]]) -> list[Any]:
    """Box of each character of `text` (None for white space or when not
    found). Measured on 360 benchmark pages: the rebuilt text equals
    get_text() on 359; the exception is an extra line break, hence the
    tolerance on white space."""
    boxes: list[Any] = [None] * len(text)
    j = 0
    for i, c in enumerate(text):
        while j < len(chars) and chars[j][0] != c and chars[j][0].isspace():
            j += 1
        if j < len(chars) and chars[j][0] == c:
            boxes[i] = chars[j][1]
            j += 1
    return boxes


def _char_key(c: str, box: Any) -> tuple[str, float, float]:
    return (c, round(box.x0, 1), round(box.y0, 1))


def _survivors(pdf: bytes, page_number: int, value: list[tuple[str, Any]], rects: list[Any]) -> int:
    """Characters of the value still on the page after the SAME redaction as
    finalization (_apply_selected_redactions: add_redact_annot on each
    rectangle, then apply_redactions): same character at the same place.
    A value with survivors is partially exposed."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        page = doc[page_number]
        for rect in rects:
            page.add_redact_annot(rect, fill=(0, 0, 0))
        page.apply_redactions()
        remaining = {_char_key(c, box) for c, box in _page_chars(page) if box is not None}
    finally:
        doc.close()
    return sum(1 for c, box in value if box is not None and not c.isspace() and _char_key(c, box) in remaining)


def _app_rects(pdf: bytes, theme: dict[str, Any] | None) -> tuple[dict[int, list[Any]], dict[str, Any]]:
    """Rectangles the application itself proposes (_detect_pdf: search,
    D-026 fallback, propagation), per page, and its localization report."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        detections, report = main._detect_pdf(doc, theme=theme)
    finally:
        doc.close()
    per_page: dict[int, list[Any]] = {}
    for detection in detections:
        per_page.setdefault(detection["page"], []).append(fitz.Rect(detection["page_rect"]))
    return per_page, report


def measure(pdf: bytes, theme: dict[str, Any] | None) -> dict[str, Any]:
    app_rects, app_report = _app_rects(pdf, theme)
    app_exposed: Counter[str] = Counter()
    doc = fitz.open(stream=pdf, filetype="pdf")
    detected: Counter[str] = Counter()
    lost: Counter[str] = Counter()
    partial: Counter[str] = Counter()
    lost_details: list[dict[str, Any]] = []
    partial_details: list[dict[str, Any]] = []
    try:
        for page_number in range(len(doc)):
            page = doc[page_number]
            page_text = page.get_text()
            boxes = _align(page_text, _page_chars(page))
            entities = main._analyze_text(main._normalize_dashes(main._normalize_allcaps(page_text)), theme=theme)
            for entity in entities:
                start, end = entity["start"], entity["end"]
                entity_text = page_text[start:end]
                if len(entity_text.strip()) < MIN_ENTITY_CHARS:
                    continue
                entity_type = entity.get("entity_type", "UNKNOWN")
                detected[entity_type] += 1
                value = list(zip(entity_text, boxes[start:end], strict=True))
                if _survivors(pdf, page.number, value, app_rects.get(page.number, [])):
                    app_exposed[entity_type] += 1
                rects = page.search_for(entity_text) or []
                detail = {"type": entity_type, "len": len(entity_text), "line_break": "\n" in entity_text.strip()}
                if not rects:
                    lost[entity_type] += 1
                    lost_details.append(detail)
                    continue
                left = _survivors(pdf, page.number, value, rects)
                if left:
                    partial[entity_type] += 1
                    partial_details.append({**detail, "chars_left": left})
        pages = len(doc)
    finally:
        doc.close()
    total, lost_total = sum(detected.values()), sum(lost.values())
    return {
        "pages": pages,
        "detected": total,
        "lost": lost_total,
        "lost_rate": round(lost_total / total, 4) if total else None,
        "detected_by_type": dict(sorted(detected.items())),
        "lost_by_type": dict(sorted(lost.items())),
        "lost_with_line_break": sum(1 for d in lost_details if d["line_break"]),
        "lost_details": lost_details,
        "partial": sum(partial.values()),
        "partial_by_type": dict(sorted(partial.items())),
        "partial_with_line_break": sum(1 for d in partial_details if d["line_break"]),
        "partial_details": partial_details,
        "app_exposed": sum(app_exposed.values()),
        "app_exposed_by_type": dict(sorted(app_exposed.items())),
        "app_report": app_report,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# EXT-35 — détections PDF non localisées ({report['generated_at']})",
        "",
        "Même chaîne que `_detect_pdf` (passe 1) : texte de la page, normalisations, `_analyze_text` vers le vrai "
        "`presidio-analyzer`, puis `page.search_for` sur la tranche du texte de la page. Une entité **perdue** est "
        "une entité détectée pour laquelle `search_for` ne renvoie aucun rectangle : aucune zone de caviardage, "
        "aucun avertissement. Seuls des comptes sont publiés.",
        "",
        "Une entité **partiellement exposée** a des rectangles, mais après le même caviardage que la finalisation "
        "(`add_redact_annot` puis `apply_redactions`), au moins un caractère de la valeur est encore sur la page, "
        "au même endroit : le caviardage laisse fuir ce reste.",
        "",
        "**Exposées avec l'application** : mêmes valeurs détectées, mais caviardées avec **toutes** les zones que "
        "`_detect_pdf` propose (recherche, repli par boîtes de caractères D-026, propagation) ; une valeur est exposée "
        "si l'un de ses caractères reste sur la page. Rapport de l'application : localisation absente, incomplète, "
        "rattrapée par le repli.",
        "",
        "| Scénario | Thème | Pages | Détectées | Perdues | Taux | Perdues avec saut de ligne | Perdues par type "
        "| Partielles | Partielles avec saut de ligne | Partielles par type | Exposées avec l'application "
        "| Rapport de l'application (absentes / incomplètes / rattrapées) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, by_theme in report["scenarios"].items():
        for theme, r in by_theme.items():
            per_type = ", ".join(f"{k} {v}" for k, v in r["lost_by_type"].items()) or "—"
            lines.append(
                f"| {name} | {theme} | {r['pages']} | {r['detected']} | {r['lost']} | {r['lost_rate']} | "
                f"{r['lost_with_line_break']} | {per_type} | {r['partial']} | {r['partial_with_line_break']} | "
                f"{', '.join(f'{k} {v}' for k, v in r['partial_by_type'].items()) or '—'} | {r['app_exposed']} | "
                f"{sum(r['app_report']['unlocated'].values())} / {sum(r['app_report']['incomplete'].values())} / "
                f"{sum(r['app_report']['recovered'].values())} |"
            )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pymupdf": fitz.VersionBind,
        "scenarios": {},
    }
    for name, build in scenarios():
        pdf = build()
        report["scenarios"][name] = {label: measure(pdf, theme) for label, theme in THEMES.items()}
        r = report["scenarios"][name]["aucun"]
        print(
            f"{name:22} detected={r['detected']:4} lost={r['lost']:3} partial={r['partial']:3} "
            f"app_exposed={r['app_exposed']} app_report={r['app_report']}",
            flush=True,
        )
    stamp = time.strftime("%Y%m%dT%H%M%S")
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"pdf-localization-{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (RESULTS / f"pdf-localization-{stamp}.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
    sys.exit(0)
