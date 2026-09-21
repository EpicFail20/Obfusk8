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
Obfusk8 - document anonymization tool (PDF, DOCX, CSV, image) - test application (Proxmox lab)
------------------------------------------------------------------------------
Common flow for all formats: detection of sensitive data via
presidio-analyzer, human review (zone exclusion), then actual
redaction (the original content is removed from the file structure, not
just visually masked):

  - PDF   : image rendering of each page + clickable zones in pixels
            (PyMuPDF redact_annot: the underlying text is removed).
  - DOCX  : inline highlighting of text per paragraph/table cell/
            header-footer (python-docx): the run's text is replaced
            in the XML, not just visually dressed up.
  - CSV   : HTML table with highlighted cells; the cell text
            is replaced the same way as for DOCX.
  - Image : text extracted via OCR (pytesseract) with per-word position, same
            pixel review screen as PDF (clickable zones + manual
            drawing), redaction via opaque rectangles drawn directly
            into the pixels (Pillow), metadata fully stripped.

KNOWN LIMITATION (DOCX): python-docx does not give access to text contained
in text boxes, shapes, SmartArt, or embedded OLE objects — sensitive
data placed in one of these elements escapes detection.
Documented to the user on the review screen, to be explicitly covered
in the future security audit dedicated to these two new formats.

This is not a production tool: no queue, no retry,
minimal error handling. Sufficient to validate functionality before a
possible hardening pass.
"""

import csv
import hashlib
import base64
import hmac
import html
import io
import json
import logging
import os
import re
import shutil
import struct
import threading
import time
import unicodedata
import uuid
import warnings
import zipfile
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pymupdf as fitz  # PyMuPDF — alias 'fitz' kept, 'import fitz' is deprecated
import pytesseract
import requests
import metrics
from antivirus import AntivirusUnavailableError, get_scanner, is_av_enforced
from docx import Document as WordDocument
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from lxml import etree
from PIL import Image, ImageDraw, UnidentifiedImageError as PILUnidentifiedImageError
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from supervision import Alert, AlertSeverity, get_alert_sink

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("anonymiseur")

ANALYZER_URL = os.environ.get("PRESIDIO_ANALYZER_URL", "http://presidio-analyzer:3000")
ANONYMIZER_URL = os.environ.get("PRESIDIO_ANONYMIZER_URL", "http://presidio-anonymizer:3000")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
FILE_TTL_SECONDS = int(os.environ.get("FILE_TTL_SECONDS", "600"))
JOB_REVIEW_TTL_SECONDS = int(os.environ.get("JOB_REVIEW_TTL_SECONDS", "900"))
MAX_PENDING_JOBS = int(os.environ.get("MAX_PENDING_JOBS", "20"))
MAX_PDF_PAGES = int(os.environ.get("MAX_PDF_PAGES", "200"))
MAX_MANUAL_ZONES = int(os.environ.get("MAX_MANUAL_ZONES", "500"))
# Maximum length allowed for the `theme` form field (see
# detect_document): a legitimate theme is a short identifier
# (medical/it/accounting); defensive bound against an arbitrarily
# long value that would overflow the output filename or the audit log.
MAX_THEME_CHARS = int(os.environ.get("MAX_THEME_CHARS", "64"))
MAX_EXCLUDED_IDS = int(os.environ.get("MAX_EXCLUDED_IDS", "2000"))
# Max number of distinct DOCX images offered for manual redaction — same
# anti-abuse guardrail spirit as MAX_MANUAL_ZONES for PDF.
MAX_DOCX_IMAGES = int(os.environ.get("MAX_DOCX_IMAGES", "100"))
# Beyond this, no embedded base64 preview on the review page (only
# size/format shown) — still selectable for redaction, only
# the visual preview is skipped, to avoid excessively bloating the page.
MAX_DOCX_IMAGE_PREVIEW_BYTES = int(os.environ.get("MAX_DOCX_IMAGE_PREVIEW_BYTES", str(500 * 1024)))
LANGUAGE = os.environ.get("ANALYZER_LANGUAGE", "fr")
# Minimum confidence threshold applied by default when the theme does not
# define one explicitly. Until now, in the absence of a theme (or with a
# theme silent on this point), NO filtering took place: Presidio returned
# all of its hypotheses, including the most uncertain ones — an isolated
# capitalized word at the start of a bullet/title, with no surrounding
# sentence, typically gets a lower NER confidence score than a real name
# in a full sentence; this threshold filters out those weak hypotheses.
# Reasonable starting value, to be adjusted (env var or per-theme
# score_threshold) based on the actual observed false positive/negative
# rate.
DEFAULT_SCORE_THRESHOLD = float(os.environ.get("DEFAULT_SCORE_THRESHOLD", "0.5"))

# --- DOCX thresholds ---
# Same product reasoning as MAX_CSV_CELLS: finalization remains a human
# review. At 20000 (original value), the limit was 10x
# MAX_REVIEW_ROWS (2000) — up to 90% of a legitimate document could have
# been redacted "blindly", never shown to the user for review,
# even though the actual redaction remained correct (job["detections"]
# always covers everything, see 14.3). Brought down to an order of
# magnitude consistent with what is actually reviewable, aligned with
# MAX_CSV_CELLS.
MAX_DOCX_PARAGRAPHS = int(os.environ.get("MAX_DOCX_PARAGRAPHS", "5000"))
# Anti "zip-bomb" protection: a .docx is a ZIP archive, an archive of a
# few KB can theoretically decompress into several GB. We bound the
# total decompressed size and the compression ratio per entry before
# letting python-docx/lxml open anything at all.
MAX_DOCX_UNCOMPRESSED_MB = int(os.environ.get("MAX_DOCX_UNCOMPRESSED_MB", "200"))
MAX_DOCX_ZIP_RATIO = int(os.environ.get("MAX_DOCX_ZIP_RATIO", "100"))
# Neither the total decompressed size nor the compression ratio bounds
# the NUMBER of entries — a zip with many tiny (or even empty) files
# stays under both thresholds above while still being expensive just to
# walk the file table. Confirmed by real test: ~24 MB (just under
# MAX_UPLOAD_MB) with ~260,000 minimal entries passes both existing
# checks in ~1.2s CPU and grows the process memory by ~150 MB for this
# single request — on a single-worker service (no `--workers` in the
# Dockerfile), so one request blocks the event loop for all users, and
# the container's memory limit (1 GB, docker-compose.yml) is reachable
# with only a few requests of this type. A real .docx rarely exceeds a
# few dozen entries (content + styles/rels/media); 5000 leaves a wide
# margin.
MAX_DOCX_ZIP_ENTRIES = int(os.environ.get("MAX_DOCX_ZIP_ENTRIES", "5000"))

# --- CSV thresholds ---
MAX_CSV_ROWS = int(os.environ.get("MAX_CSV_ROWS", "20000"))
# Ceiling set by actual usage, not just by what the pipeline can
# technically handle: finalization remains a human review
# (the user must be able to review/correct detections before
# validating), a CSV with several tens or hundreds of thousands of
# cells is never actually reviewable in practice anyway.
# Also reduces the leeway of the "resource cost" angles 9.6.4
# (detection time budget) and 9.6.5 (review page size):
# at 5000 cells, both remain safety nets that normally never
# trigger, rather than the only real protection.
MAX_CSV_CELLS = int(os.environ.get("MAX_CSV_CELLS", "5000"))
# A single abnormally long cell can consume CPU/memory
# disproportionately during analysis; we explicitly set this limit
# rather than relying on the csv module's default (which varies
# depending on the platform/Python version), consistent with the
# explicit pinning policy adopted for dependencies.
MAX_CSV_FIELD_CHARS = int(os.environ.get("MAX_CSV_FIELD_CHARS", "100000"))
csv.field_size_limit(MAX_CSV_FIELD_CHARS)

# Limits the number of rows RENDERED on the review page (not the
# redaction itself, which always covers job["detections"] in full at
# finalization time, even rows beyond this limit). A CSV with nearly
# empty cells bypasses the detection time budget (nothing to
# analyze -> fast) while producing, without this limit, an HTML page
# of several dozen MB with hundreds of thousands of <td> elements —
# confirmed by real test: 300,000 nearly empty cells (419 KB file)
# -> 16.6 MB page. Costly for the server (generation) and for the
# client's browser (rendering), independent of MAX_DETECTION_SECONDS.
MAX_REVIEW_ROWS = int(os.environ.get("MAX_REVIEW_ROWS", "2000"))

# Global time budget for the detection phase (PDF/DOCX/CSV): each
# individual call to Presidio has its own timeout (30s, see _analyze_text),
# but until now nothing bounded the number of sequential batches for a
# document close to the size limits — confirmed by real test: a CSV
# at the exact MAX_CSV_CELLS limit (300,000) requires ~1500 sequential
# calls at ~0.3s each, ~490s total, on a single-worker service
# (no `--workers` in the Dockerfile) which would therefore block
# the application for everyone for over 8 minutes. 90s leaves
# a wide margin for a legitimate multi-batch document while bounding
# the worst case to about 3x the timeout of a single Presidio call.
MAX_DETECTION_SECONDS = int(os.environ.get("MAX_DETECTION_SECONDS", "90"))


def _reject(reason: str, status_code: int, detail: str) -> None:
    """Increments the rejection counter (metrics.DOCUMENTS_REJECTED) then raises
    the corresponding HTTPException — single choke point so a future added
    rejection is never left uninstrumented. `reason` must remain a
    closed category (see metrics.py): never a value derived from
    user input."""
    metrics.DOCUMENTS_REJECTED.labels(reason=reason).inc()
    raise HTTPException(status_code=status_code, detail=detail)


def _send_alert(alert: Alert) -> None:
    """Single choke point for any alert (see supervision.py) —
    catches ANY exception rather than letting it propagate, including
    an invalid ALERT_SINK config (RuntimeError/ValueError) or an
    unreachable syslog host (DNS, connection refused). Without this
    safety net, a failed alert could, for instance, turn a properly
    handled antivirus rejection (503/400) into an uncaught generic 500,
    or silently and permanently kill the `_cleanup_sweep_loop` thread
    (no restart) — an alert must never cause the flow it is supposed
    to monitor to fail or hang."""
    try:
        get_alert_sink().send(alert)
    except Exception:
        log.warning(
            "Échec d'envoi d'une alerte (source=%s, sévérité=%s) — poursuite sans bloquer le flux principal",
            alert.source, alert.severity.value, exc_info=True,
        )


def _check_detection_deadline(start_time: float) -> None:
    """Raises an HTTPException if the ongoing detection exceeds
    MAX_DETECTION_SECONDS — to be called before each batch/page to never
    let a document close to the size limits block the single worker
    for several minutes (see MAX_DETECTION_SECONDS)."""
    if time.time() - start_time > MAX_DETECTION_SECONDS:
        _reject(
            "trop_volumineux",
            400,
            STRINGS["detection_timeout"].format(max_seconds=MAX_DETECTION_SECONDS),
        )

# Replacement marker for text redaction (DOCX/CSV): a fixed value
# rather than blocks proportional to the original length, so as not to
# leak the approximate length of the masked data.
REDACTION_MARKER = "[MASQUÉ]"

# Every file created by the service (original and redacted document in
# transit in /data/tmp, audit log, OCR temp files) must be
# readable only by the service's user. Without this umask, the
# container's default (022) produced files with 644 permissions: on the host,
# the bind mount /var/lib/anonymiseur/workdir then exposed every document
# (before AND after redaction) to any local user for the entire
# TTL duration, and the audit log permanently. Placed before the first mkdir
# and before the RotatingFileHandler creation below, to cover everything.
os.umask(0o077)

WORKDIR = Path("/data/tmp")
WORKDIR.mkdir(parents=True, exist_ok=True)

# Jobs awaiting human review (between detection and validation).
# In-memory only: acceptable for a single process (lab), but does not
# survive a container restart — a job under review at the
# time of a redeployment must be restarted by the user.
PENDING_JOBS: dict[str, dict] = {}
_PENDING_JOBS_LOCK = threading.Lock()

# Audit log: location separate from temp files, NOT subject to
# the FILE_TTL_SECONDS purge. Never contains the real file name or
# document content — only metadata (who, when, what, such as
# redaction volume), sufficient for a compliance check without
# recreating a data leak risk.
AUDIT_DIR = Path("/data/audit")
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

audit_log = logging.getLogger("anonymiseur.audit")
audit_log.setLevel(logging.INFO)
audit_log.propagate = False  # do not duplicate into normal application logs
_audit_handler = RotatingFileHandler(
    AUDIT_DIR / "audit.log", maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8"
)
_audit_handler.setFormatter(logging.Formatter("%(message)s"))
audit_log.addHandler(_audit_handler)


def _record_audit_event(**fields):
    """Appends a JSON line to the audit log (append-only, with rotation)."""
    event = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
    audit_log.info(json.dumps(event, ensure_ascii=False))


THEMES_DIR = Path(__file__).parent / "themes"
COMMON_RECOGNIZERS_FILENAME = "common.json"


def _load_themes() -> dict:
    """Loads all selectable theme files (app/themes/*.json),
    except for common.json which is not a theme but a base set of
    recognizers applied to all themes (see _load_common_recognizers)."""
    themes = {}
    for path in sorted(THEMES_DIR.glob("*.json")):
        if path.name == COMMON_RECOGNIZERS_FILENAME:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                themes[path.stem] = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Thème illisible, ignoré: %s (%s)", path.name, exc)
    return themes


def _load_common_recognizers() -> list[dict]:
    """Common recognizers (e.g. postal addresses) applied regardless of
    the chosen theme, including when no theme is selected — for
    false negatives that are not specific to a particular business domain."""
    path = THEMES_DIR / COMMON_RECOGNIZERS_FILENAME
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("ad_hoc_recognizers", [])
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Reconnaisseurs communs illisibles, ignorés: %s", exc)
        return []


THEMES = _load_themes()
COMMON_RECOGNIZERS = _load_common_recognizers()
log.info("Thèmes chargés: %s", list(THEMES.keys()))
for _theme_key, _theme_data in THEMES.items():
    log.info(
        "  thème '%s': score_threshold=%s (défaut appliqué si absent: %.2f), %d reconnaisseur(s) personnalisé(s)",
        _theme_key,
        _theme_data.get("score_threshold", "non défini"),
        DEFAULT_SCORE_THRESHOLD,
        len(_theme_data.get("ad_hoc_recognizers", [])),
    )
log.info("Reconnaisseurs communs chargés: %d", len(COMMON_RECOGNIZERS))


# ---------------------------------------------------------------------------
# UI translations (app/i18n/*.json)
# ---------------------------------------------------------------------------
I18N_DIR = Path(__file__).parent / "i18n"
UI_LANG = os.environ.get("UI_LANG", "fr")


def _load_json_file(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Fichier de traduction illisible, ignoré: %s (%s)", path.name, exc)
        return None


def _load_strings() -> dict[str, str]:
    """
    Loads the UI strings for UI_LANG (env var, default "fr"), with the
    French file (app/i18n/fr.json) as the mandatory fallback base: a key
    missing in the target language falls back silently to French (never a
    raw key or empty text shown to the user), with a warning logged once
    per missing key — this function only runs once, at import time (see
    STRINGS below), never per request.

    If UI_LANG points to a language with no JSON file at all, falls back
    entirely to French, with a clear warning at startup — same fail-safe
    logic as the rest of the project: a minor configuration issue must
    never crash the application.
    """
    fr_strings = _load_json_file(I18N_DIR / "fr.json") or {}

    if UI_LANG == "fr":
        return fr_strings

    lang_path = I18N_DIR / f"{UI_LANG}.json"
    if not lang_path.exists():
        log.warning(
            "UI_LANG=%s : fichier de traduction introuvable (%s), repli intégral sur le français.",
            UI_LANG, lang_path.name,
        )
        return fr_strings

    lang_strings = _load_json_file(lang_path)
    if lang_strings is None:
        log.warning("UI_LANG=%s : repli intégral sur le français suite à l'erreur ci-dessus.", UI_LANG)
        return fr_strings

    for key in sorted(fr_strings.keys() - lang_strings.keys()):
        log.warning(
            "UI_LANG=%s : clé de traduction manquante '%s', repli sur le français pour cette clé.", UI_LANG, key
        )

    return {**fr_strings, **lang_strings}


def _load_theme_labels() -> dict[str, str]:
    """Loads app/i18n/themes.json (theme key -> {lang: label}) for the
    theme dropdown on the upload form — kept separate from app/themes/*.json
    itself (see docs/traduire-interface.md) so that adding a new language
    never requires touching the theme/recognizer files."""
    data = _load_json_file(I18N_DIR / "themes.json") or {}
    labels = {}
    for theme_key, per_lang in data.items():
        if UI_LANG not in per_lang:
            log.warning(
                "UI_LANG=%s : libellé de thème manquant pour '%s', repli sur le français.", UI_LANG, theme_key
            )
        labels[theme_key] = per_lang.get(UI_LANG) or per_lang.get("fr") or theme_key
    return labels


# Loaded here at module level (like THEMES/COMMON_RECOGNIZERS above), not
# inside lifespan() like get_scanner()/get_alert_sink(): STRINGS must
# already be ready before `app = FastAPI(title=STRINGS["api_title"], ...)`
# below is even constructed, which runs before lifespan ever starts. Still
# loaded exactly once, never re-read per request — same guarantee as
# get_scanner(), just triggered at a different point.
STRINGS = _load_strings()
THEME_LABELS = _load_theme_labels()
log.info("Langue de l'interface : UI_LANG=%s (%d chaîne(s) chargée(s))", UI_LANG, len(STRINGS))


def _peek_zip_entry_count(raw: bytes) -> int | None:
    """
    Reads the entry count declared in a ZIP's end-of-central-directory
    record (EOCD), without ever calling
    `zipfile.ZipFile()` — it is precisely opening via `zipfile`, which
    parses the whole central directory at once, that is expensive on an
    archive with a very large number of entries (confirmed by real test: ~1.1s
    CPU and ~150 MB of memory for ~260,000 minimal entries fitting in
    ~24 MB, on a single-worker service where this time blocks the
    event loop for everyone). Returns None if the EOCD cannot be found
    (lets `zipfile.ZipFile` raise the usual "corrupted" error).

    Handles the Zip64 case (16-bit field saturated at 0xFFFF, real count in the
    "Zip64 EOCD record" located via the "Zip64 EOCD locator" that precedes
    the standard EOCD) — a zip with an extreme entry count necessarily
    needs this, so ignoring it would let through exactly the case meant to be blocked.
    """
    window = raw[-(22 + 65535):]
    idx = window.rfind(b"PK\x05\x06")
    if idx == -1 or len(window) - idx < 22:
        return None
    eocd = window[idx : idx + 22]
    total_entries = struct.unpack("<H", eocd[10:12])[0]
    if total_entries != 0xFFFF:
        return total_entries

    eocd_abs_offset = len(raw) - len(window) + idx
    locator_offset = eocd_abs_offset - 20
    if locator_offset < 0:
        return None
    locator = raw[locator_offset : locator_offset + 20]
    if locator[:4] != b"PK\x06\x07":
        return None
    zip64_eocd_offset = struct.unpack("<Q", locator[8:16])[0]
    if zip64_eocd_offset + 56 > len(raw):
        return None
    zip64_eocd = raw[zip64_eocd_offset : zip64_eocd_offset + 56]
    if zip64_eocd[:4] != b"PK\x06\x06":
        return None
    return struct.unpack("<Q", zip64_eocd[32:40])[0]


def _validate_docx_zip(raw: bytes) -> str:
    """
    Verifies that a ZIP archive is indeed a .docx that can be safely processed:
      - actually contains word/document.xml (not a renamed .xlsx/.pptx)
      - contains no VBA macro (word/vbaProject.bin -> disguised .docm)
      - is not a "zip bomb" (decompressed size disproportionate
        to the archive size, per entry and overall, OR a disproportionate
        number of entries — an individually reasonable ratio/volume
        does not prevent tens of thousands of tiny files,
        expensive on their own just to walk the file table)
    Any unmet condition raises an explicit 400 HTTPException.
    """
    entry_count = _peek_zip_entry_count(raw)
    if entry_count is not None and entry_count > MAX_DOCX_ZIP_ENTRIES:
        _reject(
            "structure_invalide",
            400,
            STRINGS["docx_zip_bomb_entries"].format(count=entry_count, max_entries=MAX_DOCX_ZIP_ENTRIES),
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        names = zf.namelist()
    except zipfile.BadZipFile as exc:
        metrics.DOCUMENTS_REJECTED.labels(reason="format_invalide").inc()
        raise HTTPException(status_code=400, detail=STRINGS["docx_invalid_corrupt"]) from exc

    if "word/document.xml" not in names:
        _reject("format_invalide", 400, STRINGS["docx_not_a_word_doc"])
    if "word/vbaProject.bin" in names:
        _reject("format_invalide", 400, STRINGS["docx_macro_rejected"])
    if len(names) > MAX_DOCX_ZIP_ENTRIES:
        # Safety net if the EOCD could not be read upstream (e.g.
        # malformed ZIP comment): costs the full read we're
        # trying to avoid above, but still protects against the
        # degradation that follows (python-docx, lxml, etc.) on the rest of
        # the pipeline.
        _reject(
            "structure_invalide",
            400,
            STRINGS["docx_zip_bomb_entries"].format(count=len(names), max_entries=MAX_DOCX_ZIP_ENTRIES),
        )

    total_uncompressed = sum(info.file_size for info in zf.infolist())
    if total_uncompressed > MAX_DOCX_UNCOMPRESSED_MB * 1024 * 1024:
        _reject(
            "trop_volumineux",
            400,
            STRINGS["docx_zip_bomb_uncompressed"].format(max_mb=MAX_DOCX_UNCOMPRESSED_MB),
        )
    for info in zf.infolist():
        if info.compress_size > 0 and (info.file_size / info.compress_size) > MAX_DOCX_ZIP_RATIO:
            _reject("structure_invalide", 400, STRINGS["docx_zip_bomb_generic"])

    return "docx"


def _strip_unicode_control_and_format_chars(value: str) -> str:
    """
    Removes every Unicode character of category "Other" (Cc/Cf/Co/Cs/Cn) from an
    untrusted-origin string (HTTP header) before it joins a
    job or the audit log.

    Precise point of concern (empirically verified, see section 3.5 of
    the audit): `json.dumps(..., ensure_ascii=False)` already neutralizes any
    attempt to forge a fake JSON line (C0/C1 control characters,
    quotes and backslashes are escaped) — but NOT Unicode bidirectional
    formatting characters (category Cf, e.g. U+202E "Right-to-
    Left Override"), valid in UTF-8 and therefore rewritten as-is in the
    file AND in the JSON response of `/api/audit`. Such a character in
    `X-Auth-Request-Email` would allow this field to be displayed in a
    misleading order to anyone reading the log (terminal or audit UI) — not
    a format integrity flaw, but a visual spoofing risk on a
    log whose value relies precisely on its human readability.
    """
    return "".join(ch for ch in value if unicodedata.category(ch)[0] != "C")


def _looks_like_text(raw: bytes, sample_size: int = 8192) -> bool:
    """Weak but sufficient heuristic to rule out an arbitrary binary
    presented as a .csv: a CSV has no proper binary signature, so
    unlike PDF/DOCX we can only validate the absence of null bytes
    and a successful text decode. Documented residual risk: does not guarantee
    that the content is actually tabular — to be covered in the future
    dedicated audit (see also _parse_csv_rows for volume guardrails)."""
    sample = raw[:sample_size]
    if b"\x00" in sample:
        return False
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            sample.decode(encoding)
            return True
        except UnicodeDecodeError:
            continue
    return False


def _run_antivirus_scan(raw: bytes, filename: str, filename_hash: str) -> None:
    """
    Scans the raw file via the configured engine (see antivirus.py) before
    any PDF/DOCX/CSV parsing. AV_ENGINE=none (default) makes this a silent
    no-op (verdict always clean). Behavior on an unfavorable verdict
    depends on AV_ENFORCE: blocking (HTTPException) or a simple
    warning log in observation mode — never a silent failure,
    so that the choice to let a threatening file through stays
    visible in the logs.

    `filename` (raw name) is only passed to the scan engine itself
    (useful for some engines that rely on the extension); only
    `filename_hash` appears in the logs, as everywhere else in the
    project (including the audit log) — a file name may contain
    real patient data.
    """
    try:
        result = get_scanner().scan(raw, filename_hint=filename)
    except AntivirusUnavailableError as exc:
        metrics.AV_SCAN_RESULT.labels(verdict="indisponible").inc()
        _send_alert(
            Alert(
                severity=AlertSeverity.WARNING,
                source="antivirus",
                message="Scanner antivirus indisponible",
                details={"filename_hash": filename_hash},
            )
        )
        if is_av_enforced():
            metrics.DOCUMENTS_REJECTED.labels(reason="antivirus_indisponible").inc()
            raise HTTPException(
                status_code=503,
                detail=STRINGS["av_service_unavailable"],
            ) from exc
        log.warning(
            "AV_ENFORCE=false : scan antivirus indisponible pour fichier %s, fichier traité quand même (%s)",
            filename_hash, exc,
        )
        return

    if not result.is_clean:
        # threat_name comes from the ICAP server (X-Virus-ID/X-Infection-Found
        # header, see antivirus.py) — not directly from the uploaded file's content in
        # a compliant ICAP flow, but sanitized as a precaution before joining the
        # syslog log and the HTTP response: same visual spoofing risk via a
        # Unicode formatting character (RTL override...) as the one found and
        # fixed on the audit log (3.5), for any text source external to the
        # project meant to be read by a human.
        threat = _strip_unicode_control_and_format_chars(result.threat_name or STRINGS["av_unknown_threat"])
        metrics.AV_SCAN_RESULT.labels(verdict="menace").inc()
        _send_alert(
            Alert(
                severity=AlertSeverity.CRITICAL,
                source="antivirus",
                message="Menace détectée par l'antivirus",
                details={"filename_hash": filename_hash, "threat": threat},
            )
        )
        if is_av_enforced():
            metrics.DOCUMENTS_REJECTED.labels(reason="menace_antivirus").inc()
            raise HTTPException(
                status_code=400,
                detail=STRINGS["av_threat_detected"].format(threat=threat),
            )
        log.warning(
            "AV_ENFORCE=false : menace détectée (%s) pour fichier %s, fichier traité quand même",
            threat, filename_hash,
        )
        return

    metrics.AV_SCAN_RESULT.labels(verdict="propre").inc()


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"


def _detect_file_kind(raw: bytes) -> str:
    """
    Determines the actual file type from its binary content, never
    from the Content-Type or file name declared by the client (forgeable).
    JPG and JPEG are the same format (signature \\xFF\\xD8\\xFF) — handled
    identically, without distinction.
    """
    if raw.startswith(b"%PDF-"):
        return "pdf"
    if raw.startswith(b"PK\x03\x04"):
        return _validate_docx_zip(raw)
    if raw.startswith(PNG_SIGNATURE) or raw.startswith(JPEG_SIGNATURE):
        return "image"
    if _looks_like_text(raw):
        return "csv"
    _reject("format_invalide", 400, STRINGS["unknown_file_format"])


def _decode_csv_bytes(raw: bytes) -> tuple[str, str]:
    """Decodes CSV bytes by trying the encodings most common in
    practice (UTF-8 with Excel BOM, standard UTF-8, then Windows-1252
    frequent on French Excel exports with accented characters)."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    _reject("format_invalide", 400, STRINGS["csv_encoding_unrecognized"])


def _detect_csv_delimiter(sample_text: str) -> str:
    """Detects the delimiter (comma or semicolon). The semicolon is
    very common in France (French Excel uses the comma as a decimal
    separator, so it exports CSVs with ';')."""
    first_lines = "\n".join(sample_text.splitlines()[:5])
    try:
        return csv.Sniffer().sniff(first_lines, delimiters=";,\t").delimiter
    except csv.Error:
        return ";" if first_lines.count(";") >= first_lines.count(",") else ","


def _parse_csv_rows(text: str, delimiter: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows: list[list[str]] = []
    for row in reader:
        rows.append(row)
        if len(rows) > MAX_CSV_ROWS:
            _reject(
                "trop_volumineux", 400,
                STRINGS["csv_too_many_rows"].format(count=len(rows), max_rows=MAX_CSV_ROWS),
            )
    return rows


_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _neutralize_csv_formula(value: str) -> str:
    """
    Protection against CSV formula injection: if a cell of the output
    file starts with a character that Excel/LibreOffice interprets as
    the start of a formula, it is prefixed with an apostrophe to neutralize it
    on opening. Applies to ALL cells of the produced file (not
    only redacted ones) — protects the person who opens the
    anonymized file, a risk independent of anonymization itself but
    relevant to cover now rather than leaving it to the future audit.
    """
    if value and value[0] in _CSV_FORMULA_TRIGGERS:
        return "'" + value
    return value


def _sweep_orphaned_files():
    """
    Deletes any output file older than FILE_TTL_SECONDS.

    Safety net for "orphaned" files: if the container
    restarts (e.g. redeployment) between a file's creation and
    the expiry of its individual purge delay (_schedule_cleanup), the
    in-memory thread that was supposed to delete it dies with the old process and
    the file stays on disk indefinitely. This sweep, launched at
    startup and then repeated periodically, catches these cases.
    """
    now = time.time()
    for path in WORKDIR.glob("*-anonymise.*"):
        try:
            age = now - path.stat().st_mtime
            if age > FILE_TTL_SECONDS:
                path.unlink(missing_ok=True)
                log.info("Fichier orphelin purgé (balayage): %s (âge %.0fs)", path.name, age)
        except FileNotFoundError:
            continue
        except OSError as exc:  # pragma: no cover - purge best-effort
            log.warning("Balayage: suppression échouée pour %s: %s", path.name, exc)


def _sweep_stale_jobs():
    """Deletes review jobs never finalized beyond JOB_REVIEW_TTL_SECONDS."""
    now = time.time()
    with _PENDING_JOBS_LOCK:
        stale = [
            job_id
            for job_id, job in PENDING_JOBS.items()
            if now - job["created_at"] > JOB_REVIEW_TTL_SECONDS
        ]
        for job_id in stale:
            del PENDING_JOBS[job_id]
        metrics.PENDING_JOBS.set(len(PENDING_JOBS))
    if stale:
        log.info("Jobs en révision expirés purgés: %d", len(stale))


# Actually mounted volumes (see docker-compose.yml) whose free disk
# space is monitored. WORKDIR and AUDIT_DIR may be two distinct
# mount points in production (separate bind mounts) even though they share
# the same disk in the lab.
_MONITORED_VOLUMES = {"workdir": WORKDIR, "audit": AUDIT_DIR}

# Disk space alert thresholds: triggered on the more restrictive of the
# two criteria (percentage OR absolute value), to stay relevant both
# on a small volume (where 10% can represent several GB of margin) and on
# a very large volume (where 10% can still be huge while the absolute
# remaining space is already critical).
_DISK_WARNING_PCT = 10.0
_DISK_WARNING_MB = 500
_DISK_CRITICAL_PCT = 5.0
_DISK_CRITICAL_MB = 100


def _check_disk_space(volume: str, path: Path) -> None:
    try:
        total, _used, free = shutil.disk_usage(path)
    except OSError as exc:  # pragma: no cover - lecture best-effort
        log.warning("Espace disque illisible pour le volume '%s': %s", volume, exc)
        return

    metrics.DISK_FREE_BYTES.labels(volume=volume).set(free)
    free_mb = free / (1024 * 1024)
    free_pct = (free / total * 100) if total else 100.0
    details = {"volume": volume, "free_mb": round(free_mb, 1), "free_pct": round(free_pct, 1)}

    if free_pct < _DISK_CRITICAL_PCT or free_mb < _DISK_CRITICAL_MB:
        _send_alert(
            Alert(
                severity=AlertSeverity.CRITICAL,
                source="disk-space",
                message=f"Espace disque critique sur le volume '{volume}'",
                details=details,
            )
        )
    elif free_pct < _DISK_WARNING_PCT or free_mb < _DISK_WARNING_MB:
        _send_alert(
            Alert(
                severity=AlertSeverity.WARNING,
                source="disk-space",
                message=f"Espace disque faible sur le volume '{volume}'",
                details=details,
            )
        )


def _check_presidio_health(service: str, base_url: str) -> None:
    """Lightweight health check: a simple HTTP request is enough, no
    need to reproduce a real analysis/anonymization call here."""
    try:
        resp = requests.get(f"{base_url}/health", timeout=5)
        up = resp.ok
    except requests.RequestException:
        up = False
    metrics.PRESIDIO_UP.labels(service=service).set(1 if up else 0)
    if not up:
        _send_alert(
            Alert(
                severity=AlertSeverity.WARNING,
                source="presidio",
                message=f"Service presidio-{service} injoignable",
                details={"service": service},
            )
        )


def _cleanup_sweep_loop(interval_seconds: int = 60):
    while True:
        time.sleep(interval_seconds)
        # This thread is the only thing that purges orphaned files and
        # expired review jobs (1.11/1.14) — an uncaught exception
        # here (e.g. a bug in a check added later) would silently kill the
        # thread FOREVER (no restart), disabling
        # this cleanup until the next container restart without
        # anyone noticing. `_send_alert` already catches alert
        # failures themselves; this net covers everything else as a precaution.
        try:
            _sweep_orphaned_files()
            _sweep_stale_jobs()
            for volume, path in _MONITORED_VOLUMES.items():
                _check_disk_space(volume, path)
            _check_presidio_health("analyzer", ANALYZER_URL)
            _check_presidio_health("anonymizer", ANONYMIZER_URL)
        except Exception:
            log.error("Erreur inattendue dans la boucle de nettoyage périodique, tour ignoré", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    # Validates the antivirus config right now (unknown AV_ENGINE, missing
    # ICAP_HOST, non-numeric ICAP_PORT/ICAP_TIMEOUT_SECONDS...): fast
    # and explicit failure at startup rather than a generic 500 on the
    # first /api/detect request once in production.
    get_scanner()

    # Same for the alerting config (ALERT_SINK) — but unlike the
    # antivirus, a misconfigured supervision setup must never prevent the
    # main service (anonymization, critical function) from starting:
    # simple warning at startup, not a fatal failure. `_send_alert`
    # will retry the construction anyway on the next alert
    # (lru_cache does not memoize failures, see supervision.py).
    try:
        get_alert_sink()
    except Exception:
        log.error("Configuration ALERT_SINK invalide, alertes non fonctionnelles pour l'instant", exc_info=True)

    # Immediately catches up on files left by a previous process
    # (crash, redeployment) even before the first periodic loop pass.
    _sweep_orphaned_files()
    threading.Thread(target=_cleanup_sweep_loop, daemon=True).start()
    log.info("Balayage des fichiers orphelins démarré (contrôle toutes les 60s)")

    yield  # the application runs here

    # --- Shutdown ---
    log.info("Arrêt de l'application")


app = FastAPI(title=STRINGS["api_title"], lifespan=lifespan)

ERROR_TITLES = {
    400: STRINGS["error_400_title"],
    404: STRINGS["error_404_title"],
    413: STRINGS["error_413_title"],
    422: STRINGS["error_422_title"],
    502: STRINGS["error_502_title"],
}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Displays a readable error page for a browser (form submitted
    normally), while keeping a plain JSON response for a
    scripted/API call (curl, tooling) that doesn't explicitly ask for HTML.
    """
    wants_html = "text/html" in request.headers.get("accept", "")
    if not wants_html:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    title = ERROR_TITLES.get(exc.status_code, STRINGS["error_generic_title"])
    return HTMLResponse(
        status_code=exc.status_code,
        content=f"""
        <!doctype html>
        <html lang="{UI_LANG}">
        <head><meta charset="utf-8"><title>{title} - Obfusk8</title></head>
        <body style="font-family: sans-serif; max-width: 560px; margin: 80px auto; text-align:center;">
          <div style="font-size:3em; margin-bottom:8px;">⚠️</div>
          <h1 style="margin-bottom:8px;">{html.escape(title)}</h1>
          <p style="color:#555; font-size:1.1em;">{html.escape(str(exc.detail))}</p>
          <p style="margin-top:32px;">
            <a href="/" style="
                display:inline-block; padding:10px 24px; background:#0d6efd;
                color:white; text-decoration:none; border-radius:4px;">
              &larr; {STRINGS["back_to_home_link"]}
            </a>
          </p>
        </body>
        </html>
        """,
    )


# ---------------------------------------------------------------------------
# HTTP request size ceiling, applied BEFORE any form parsing
# ---------------------------------------------------------------------------
# The MAX_UPLOAD_MB check in detect_document runs after `await
# file.read()`, i.e. after Starlette has already received and stored
# the entire multipart body (in memory up to 1 MB, then in a
# temp file under /tmp — a tmpfs, so memory counted in the
# container's limit), and everything has then been re-read into memory. No ceiling
# existed upstream (neither here nor on the Traefik side): reproduced on a
# disposable container identical to the service (1 GB limit, blocking
# seccomp profile), a SINGLE 700 MB chunked upload kills the container via OOM
# (exit 137) within seconds — taking down with it every review job pending
# for other users. Traefik rate limiting (5 req/min) does not protect
# against this: a single request is enough.
#
# Two guardrails, in order:
#  - Declared Content-Length above the ceiling -> immediate 413, without reading a
#    single byte of the body.
#  - Chunked body (no Content-Length) or lying Content-Length -> each
#    received fragment is counted; as soon as the total exceeds the ceiling, the
#    read is interrupted by a 413 HTTPException (subclass, so that
#    FastAPI lets it propagate as-is up to http_exception_handler instead
#    of converting it to a generic 400). Nothing has been accumulated beyond the
#    ceiling at that point.
# The ceiling leaves a 2 MB margin above MAX_UPLOAD_MB for the multipart
# wrapping and other form fields (manual_zones, otherwise
# limited to 1 MB by Starlette). The exact, user-readable
# check ("File too large (x MB, max 25 MB)") remains the one in
# detect_document; this one is a resource barrier, not a UX check.
MAX_REQUEST_BODY_BYTES = (MAX_UPLOAD_MB + 2) * 1024 * 1024


# ---------------------------------------------------------------------------
# Section 3.7: shared gateway secret (Traefik -> app)
# ---------------------------------------------------------------------------
# `app` trusted `X-Auth-Request-Email` (audit log AND job ownership
# check, section 1.38) without ever verifying that the request had
# actually gone through Traefik -> oauth2-proxy. An external client
# cannot forge this header (Traefik strips it and replaces it before the
# forwardAuth), BUT a container on the same Docker network as `app`
# (`app-internal` OR `backend`: presidio, or a compromised/malicious neighbor)
# can reach `app:8000` DIRECTLY, bypassing Traefik entirely, and
# forge the header outright. Empirically verified: bypass
# confirmed end-to-end (an audit entry `admin@usurpe.fr` was written
# via a neighboring container without ever going through oauth2-proxy).
#
# Countermeasure (same principle as the secrets already in place): a shared secret,
# known only to Traefik and `app`. Traefik injects it, overwriting any
# client-supplied value, on every request routed to `app`; `app` verifies it HERE,
# BEFORE any processing, in constant time (hmac.compare_digest, never ==).
# Missing or incorrect -> immediate 401, before even reading X-Auth-Request-Email.
#
# The secret is mounted as a Docker secret (`/run/secrets/gateway_secret`, same
# convention as oauth2_*), never in cleartext in docker-compose.yml. Traefik
# injects it via a gitignored dynamic configuration file
# (`traefik/dynamic/gateway-secret.yml`), rendered by generate-secrets.sh from
# the SAME value.
#
# Disabled (no-op) if no secret is configured — local dev/test, where
# no /run/secrets is mounted. An explicit warning is logged
# at startup in that case; never a silent bypass in production.
GATEWAY_SECRET_HEADER = b"x-internal-gateway-secret"


def _read_gateway_secret() -> str:
    # Direct override via env var (tests); otherwise reads the Docker
    # secret mounted by Docker at startup. Absent -> empty string -> disabled.
    direct = os.environ.get("GATEWAY_SECRET")
    if direct is not None:
        return direct.strip()
    path = os.environ.get("GATEWAY_SECRET_FILE", "/run/secrets/gateway_secret")
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


GATEWAY_SECRET = _read_gateway_secret()

if not GATEWAY_SECRET:
    log.warning(
        "Secret passerelle (section 3.7) NON configuré : la vérification de "
        "X-Internal-Gateway-Secret est DÉSACTIVÉE. Attendu uniquement en "
        "dev/test. En production, générer et monter le Docker secret "
        "`gateway_secret` (generate-secrets.sh) — sinon un conteneur voisin "
        "du même réseau Docker peut joindre `app` directement, hors Traefik, "
        "et forger X-Auth-Request-Email."
    )


class _GatewaySecretMiddleware:
    """Checks the shared secret injected by Traefik (section 3.7) BEFORE any
    request processing — so before any endpoint at all reads
    `X-Auth-Request-Email`. `/health` is exempted: an optional Docker
    HEALTHCHECK queries the container over loopback, not via Traefik, and must
    not start failing because of this check. Constant-time
    comparison. No-op if no secret is configured (see GATEWAY_SECRET)."""

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return

        secret = GATEWAY_SECRET  # read on every request (overridable in tests)
        if secret and scope.get("path") != "/health":
            provided = b""
            for name, value in scope.get("headers", []):
                if name == GATEWAY_SECRET_HEADER:
                    provided = value
                    break
            if not hmac.compare_digest(provided, secret.encode("utf-8")):
                response = await http_exception_handler(
                    Request(scope),
                    HTTPException(status_code=401, detail=STRINGS["unauthorized_request"]),
                )
                await response(scope, receive, send)
                return

        await self.asgi_app(scope, receive, send)


class RequestBodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=413,
            detail=STRINGS["request_body_too_large"].format(max_mb=MAX_UPLOAD_MB),
        )


class _RequestBodyLimitMiddleware:
    """Pure ASGI middleware (not BaseHTTPMiddleware: that would consume the
    body differently and break streaming)."""

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return

        limit = MAX_REQUEST_BODY_BYTES  # read on every request (overridable in tests)

        declared = None
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None
                break
        if declared is not None and declared > limit:
            response = await http_exception_handler(Request(scope), RequestBodyTooLarge())
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge()
            return message

        await self.asgi_app(scope, limited_receive, send)


# ---------------------------------------------------------------------------
# HTTP security headers, on ALL responses (pages, previews, errors)
# ---------------------------------------------------------------------------
# No header of this kind was set, neither here nor by Traefik. The most
# important one for this project is `Cache-Control: no-store`: without it,
# page previews (/api/preview_image — rendering of the ORIGINAL document, before
# redaction) and downloaded files were written to the disk cache
# of the user's browser, where they survive session closure
# and the server-side TTL. The other headers are the expected baseline
# for a web application handling health data: anti-MIME-sniffing,
# anti-clickjacking (frame-ancestors + X-Frame-Options for old
# browsers), CSP restricting the loading of scripts/images/connections to
# the origin itself (the review pages use inline scripts and styles
# and data: images for DOCX previews, hence 'unsafe-inline' and
# data: — the CSP mainly blocks any exfiltration to another domain and
# any external script), no job_id leak via the Referer outside the origin,
# HSTS (ignored by browsers as long as the connection isn't HTTPS, so
# no effect when testing directly on port 8000), cross-origin isolation of
# resources (a third-party site cannot embed a preview). A header already
# explicitly set by a response is never overwritten.
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (
        b"content-security-policy",
        b"default-src 'self'; script-src 'self' 'unsafe-inline'; "
        b"style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        b"connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
        b"base-uri 'none'; object-src 'none'",
    ),
    (b"referrer-policy", b"same-origin"),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)

# /api/download also serves the redacted PDF/image WITHIN the "Preview
# (visual check)" page generated by /api/finalize (<iframe>/<img> on the same
# origin). frame-ancestors 'none' + X-Frame-Options: DENY, set by default
# above on EVERY response, then prevented the browser from displaying
# this response within ITS OWN page ("Firefox can't open this page"),
# even though the HTTP request itself succeeded (200). Only the extensions
# served inline (see _INLINE_EXTENSIONS below) need this
# relaxation, targeted to 'self' — a third-party site remains blocked as before;
# attachment downloads (.docx/.csv, never framed) keep 'none'.
_INLINE_PREVIEW_HEADERS = {
    "x-frame-options": "SAMEORIGIN",
    "content-security-policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'self'; "
        "base-uri 'none'; object-src 'none'"
    ),
}


class _SecurityHeadersMiddleware:
    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                for name, value in _SECURITY_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.asgi_app(scope, receive, send_with_headers)


# Order: the last one added is the outermost. The headers must also wrap
# the 413 response emitted directly by the size ceiling AND the 401
# response from the gateway (section 3.7). The gateway secret check
# runs BEFORE the body ceiling (no point buffering the body of an
# unauthorized caller) but UNDER the security headers (which therefore
# also wrap its 401).
app.add_middleware(_RequestBodyLimitMiddleware)
app.add_middleware(_GatewaySecretMiddleware)
app.add_middleware(_SecurityHeadersMiddleware)


def _schedule_cleanup(path: Path, delay: int = FILE_TTL_SECONDS):
    """Deletes the file after `delay` seconds (automatic purge)."""

    def _cleanup():
        time.sleep(delay)
        try:
            path.unlink(missing_ok=True)
            log.info("Fichier temporaire purgé: %s", path.name)
        except Exception as exc:  # pragma: no cover - purge best-effort
            log.warning("Purge échouée pour %s: %s", path.name, exc)

    threading.Thread(target=_cleanup, daemon=True).start()


def _normalize_allcaps(text: str) -> str:
    """
    Converts all-uppercase words (e.g. "SMITH") to title case
    ("Smith") to help the NER model recognize them as proper
    nouns — spaCy relies heavily on casing for this detection.

    Important: .capitalize() never changes a word's length, so the
    positions (start/end) returned by the analyzer remain valid for
    slicing the ORIGINAL (non-normalized) text used for redaction.
    """
    return re.sub(r"\b[A-ZÀ-Ý]{2,}\b", lambda m: m.group(0).capitalize(), text)


# Unicode dash variants encountered in real PDFs (depending on the
# generation tool/font used) that do not match the standard ASCII
# hyphen expected by the date-recognition regexes. A
# 1-for-1 replacement preserves the text length, so the positions
# (start/end) returned by the analyzer remain valid on the original text.
_DASH_VARIANTS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"


def _normalize_dashes(text: str) -> str:
    """Replaces typographic dashes (en dash, em dash, mathematical
    minus sign...) with a standard ASCII hyphen, so that
    dates in DD-MM-YYYY format (or similar) are recognized regardless of
    the separator character actually used in the source PDF."""
    return text.translate({ord(c): "-" for c in _DASH_VARIANTS})


def _analyze_text(text: str, theme: dict | None = None) -> list[dict]:
    """Calls presidio-analyzer on a chunk of text, returns the entities found.

    If a theme is provided, its custom recognizers (ad_hoc_recognizers),
    its exclusion list (allow_list) and its sensitivity threshold
    (score_threshold) are sent with the request — without ever touching
    the static config of the presidio-analyzer container.
    """
    if not text.strip():
        return []

    payload = {"text": text, "language": LANGUAGE}

    # Common recognizers always apply, whether a theme is selected or not.
    ad_hoc = list(COMMON_RECOGNIZERS)
    if theme and theme.get("ad_hoc_recognizers"):
        ad_hoc.extend(theme["ad_hoc_recognizers"])
    if ad_hoc:
        payload["ad_hoc_recognizers"] = ad_hoc

    if theme:
        if theme.get("allow_list"):
            payload["allow_list"] = theme["allow_list"]
        if theme.get("allow_list_match"):
            payload["allow_list_match"] = theme["allow_list_match"]

    score_threshold = theme.get("score_threshold") if theme and theme.get("score_threshold") is not None else DEFAULT_SCORE_THRESHOLD
    payload["score_threshold"] = score_threshold

    try:
        resp = requests.post(f"{ANALYZER_URL}/analyze", json=payload, timeout=30)
        resp.raise_for_status()
        entities = resp.json()
    except requests.RequestException as exc:
        log.error("Appel presidio-analyzer échoué: %s", exc)
        raise HTTPException(status_code=502, detail="Moteur d'analyse indisponible") from exc

    # Re-checked client-side (not just sent in the request): depending on the
    # presidio-analyzer version, the score_threshold parameter is not
    # always honored server-side for all recognizers. Cheap safety
    # net; a missing score is treated as maximal (1.0) so as to
    # never reject an entity out of excess caution if the field is missing.
    entities_before = entities
    entities = [e for e in entities_before if e.get("score", 1.0) >= score_threshold]

    # Diagnostics: never logs the detected text (see audit
    # policy), only type + score, to distinguish a genuine NER
    # threshold issue (varied scores, close to the threshold) from a
    # custom recognizer with a fixed score (often 1.0, which no threshold can filter).
    if entities_before:
        log.info(
            "Analyse: seuil=%.2f, %d entité(s) avant filtrage %s, %d après.",
            score_threshold,
            len(entities_before),
            [(e.get("entity_type"), round(e.get("score", 1.0), 2)) for e in entities_before],
            len(entities),
        )

    # After-the-fact filtering rather than a positive list of types sent to
    # Presidio: we only remove what we know to be noise (e.g.
    # ORGANIZATION on multi-line spans merging unrelated
    # fragments in dense medical forms), without risking silently
    # excluding a legitimate type we hadn't thought to list
    # (email, phone...).
    excluded_types = set(theme.get("excluded_entity_types", [])) if theme else set()
    if excluded_types:
        entities = [e for e in entities if e.get("entity_type") not in excluded_types]

    return entities


def _anonymize_text(text: str, entities: list[dict]) -> str:
    """Calls presidio-anonymizer to produce an anonymized text version (audit)."""
    if not entities:
        return text
    try:
        resp = requests.post(
            f"{ANONYMIZER_URL}/anonymize",
            json={
                "text": text,
                "analyzer_results": entities,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("text", text)
    except requests.RequestException as exc:
        log.error("Appel presidio-anonymizer échoué: %s", exc)
        # Non-blocking: PDF redaction does not depend on this call
        return text


PREVIEW_ZOOM = 2.0  # magnification factor for page-to-image rendering

# Defense in depth against CVE-2026-3308 (MuPDF, pdf_load_image_imp):
# an image's stride is computed as a 32-bit integer on the MuPDF side, which
# can overflow with deliberately absurd declared dimensions and
# trigger an out-of-bounds write during decoding. The
# PyMuPDF version used here is already patched, but we still check the
# declared dimensions (read from the image object's metadata, without
# decoding) before any call to get_pixmap(), as defense in depth.
#
# Same threshold reused for PNG/JPEG images uploaded directly (see
# _open_and_validate_image): same decompression-bomb principle,
# same defense (declared dimensions read before any full decoding).
MAX_IMAGE_PIXELS = int(os.environ.get("MAX_IMAGE_PIXELS", "40_000_000"))

# Explicitly aligns Pillow's built-in decompression-bomb protection
# with our own threshold, rather than silently relying on its
# default value (89,478,485, different from MAX_IMAGE_PIXELS above).
# The assertion checks that this protection stays active (never
# disabled by setting Image.MAX_IMAGE_PIXELS to None elsewhere in the code)
# — an explicitly requested point of vigilance: a silent disabling
# of this limit would break the defense in depth below without any
# error signaling it before an actual decompression bomb is
# processed.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
assert Image.MAX_IMAGE_PIXELS is not None, "PIL.Image.MAX_IMAGE_PIXELS ne doit jamais être désactivé (None)"


def _check_page_images_sane(page: "fitz.Page") -> None:
    """
    Rejects the page if an embedded image declares zero/negative
    dimensions or dimensions exceeding MAX_IMAGE_PIXELS. To be called before any
    page.get_pixmap(): get_images(full=True) only reads the
    /Width and /Height entries of the image object in the PDF, without decoding pixels.
    """
    for img in page.get_images(full=True):
        width, height = img[2], img[3]
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise HTTPException(
                status_code=400,
                detail=STRINGS["page_invalid_image_dimensions"],
            )


# Entity types propagated across the whole document once confirmed at
# least once — a patient name that escapes NER in a dense context
# (e.g. a page listing many lab technicians) is nonetheless
# identifying data that recurs verbatim elsewhere in the document.
PROPAGATED_ENTITY_TYPES = {"PERSON", "LOCATION"}


def _name_variants(name: str) -> set[str]:
    """
    Generates plausible variants of an already-confirmed name, to
    find it elsewhere in the document even if its case or first-name/
    last-name order differs from one place to another (observed in
    practice: one page has "Jean DURAND", another "DURAND JEAN").
    Limited to swapping two words — beyond that, the actual order is too
    ambiguous to be guessed safely.
    """
    words = name.split()
    orders = [words]
    if len(words) == 2:
        orders.append([words[1], words[0]])

    variants = set()
    for order in orders:
        variants.add(" ".join(order))
        variants.add(" ".join(w.title() for w in order))
        variants.add(" ".join(w.upper() for w in order))
    return variants


def _detect_pdf(doc: fitz.Document, theme: dict | None = None) -> list[dict]:
    """
    Detects sensitive entities without redacting them. For each
    visual occurrence found, returns both its actual position in the PDF (for
    the final redaction) and its position scaled to the preview image (for
    clickable display on the browser side) — both computed with the same
    zoom matrix to stay perfectly aligned.

    Two passes: (1) standard detection page by page via Presidio, (2)
    propagation of names confirmed in pass 1 to the rest of the document, where
    NER may have missed an identical occurrence (dense context,
    form, different page) — see PROPAGATED_ENTITY_TYPES.
    """
    matrix = fitz.Matrix(PREVIEW_ZOOM, PREVIEW_ZOOM)
    detections: list[dict] = []

    page_texts: list[str] = []
    propagate_candidates: dict[str, str] = {}
    already_covered: set[tuple[int, tuple[float, float, float, float]]] = set()
    detection_start = time.time()

    # --- Pass 1: standard detection, page by page ---
    for page_index, page in enumerate(doc):
        _check_detection_deadline(detection_start)
        page_text = page.get_text()
        page_texts.append(page_text)
        normalized_text = _normalize_dashes(_normalize_allcaps(page_text))
        entities = _analyze_text(normalized_text, theme=theme)

        for entity in entities:
            entity_text = page_text[entity["start"] : entity["end"]]
            stripped = entity_text.strip()
            if len(stripped) < 3:
                continue

            entity_type = entity.get("entity_type", "UNKNOWN")
            # An isolated last name is too ambiguous to propagate without
            # risk, but an isolated city name (e.g. "Ajaccio") is a good
            # candidate even alone — hence the different rule per type.
            # Minimum 4-character threshold for an isolated LOCATION word: a
            # short abbreviation (e.g. "Enr", 3 letters) turned out to be able
            # to propagate as a prefix onto other words via PyMuPDF's
            # case-insensitive search (search_for) — 4+ characters
            # lets real short city names through (Metz, Caen, Nice,
            # Lyon...) while blocking this kind of false positive.
            is_propagatable = entity_type in PROPAGATED_ENTITY_TYPES and (
                " " in stripped or (entity_type == "LOCATION" and len(stripped) >= 4)
            )
            for rect in page.search_for(entity_text):
                display_rect = rect * matrix
                key = (page_index, (rect.x0, rect.y0, rect.x1, rect.y1))
                already_covered.add(key)
                detections.append(
                    {
                        "id": uuid.uuid4().hex[:12],
                        "page": page_index,
                        "entity_type": entity_type,
                        "group_key": stripped if is_propagatable else None,
                        "page_rect": [rect.x0, rect.y0, rect.x1, rect.y1],
                        "display_rect": [
                            display_rect.x0, display_rect.y0,
                            display_rect.x1, display_rect.y1,
                        ],
                    }
                )

            # A proper name or city confirmed once becomes a
            # candidate for propagation across the rest of the document, with the
            # original entity type kept for audit/summary purposes.
            if is_propagatable:
                propagate_candidates[stripped] = entity_type

    # --- Pass 2: propagation of confirmed names/cities to the other pages ---
    for name, propagated_entity_type in propagate_candidates.items():
        for variant in _name_variants(name):
            for page_index, page in enumerate(doc):
                for rect in page.search_for(variant):
                    key = (page_index, (rect.x0, rect.y0, rect.x1, rect.y1))
                    if key in already_covered:
                        continue
                    already_covered.add(key)
                    display_rect = rect * matrix
                    detections.append(
                        {
                            "id": uuid.uuid4().hex[:12],
                            "page": page_index,
                            "entity_type": propagated_entity_type,
                            "group_key": name,
                            "page_rect": [rect.x0, rect.y0, rect.x1, rect.y1],
                            "display_rect": [
                                display_rect.x0, display_rect.y0,
                                display_rect.x1, display_rect.y1,
                            ],
                        }
                    )

    return detections


def _rect_iou(a: list[float], b: list[float]) -> float:
    """Computes the IoU (intersection over union) between two rectangles [x0, y0, x1, y1]."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    area_a = max(0, ax1 - ax0) * max(0, ay1 - ay0)
    area_b = max(0, bx1 - bx0) * max(0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _cluster_detections(detections: list[dict], iou_threshold: float = 0.3) -> list[dict]:
    """
    Groups detections whose rectangles overlap significantly
    (typically: several different recognizers detecting the same
    piece of text, e.g. generic NER + a custom pattern on the
    same name). Without this grouping, two invisibly overlapping zones
    can exist at the same spot: clicking one to exclude it
    leaves the other active, and the zone appears to "not un-redact".

    Each cluster exposes a single clickable id on the browser side, but keeps
    the list of all underlying detection ids for exclusion purposes.
    """
    by_page: dict[int, list[dict]] = {}
    for d in detections:
        by_page.setdefault(d["page"], []).append(d)

    clusters = []
    for page, page_dets in by_page.items():
        used = [False] * len(page_dets)
        for i, d in enumerate(page_dets):
            if used[i]:
                continue
            group = [d]
            used[i] = True
            for j in range(i + 1, len(page_dets)):
                if used[j]:
                    continue
                if _rect_iou(d["page_rect"], page_dets[j]["page_rect"]) >= iou_threshold:
                    group.append(page_dets[j])
                    used[j] = True

            x0 = min(g["display_rect"][0] for g in group)
            y0 = min(g["display_rect"][1] for g in group)
            x1 = max(g["display_rect"][2] for g in group)
            y1 = max(g["display_rect"][3] for g in group)
            group_key = next((g.get("group_key") for g in group if g.get("group_key")), None)
            clusters.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "page": page,
                    "display_rect": [x0, y0, x1, y1],
                    "entity_types": sorted({g["entity_type"] for g in group}),
                    "member_ids": [g["id"] for g in group],
                    "group_key": group_key,
                }
            )

    return clusters


# ---------------------------------------------------------------------------
# Image (PNG/JPEG) - input validation, OCR + PII detection
# ---------------------------------------------------------------------------

OCR_LANGUAGE = "fra"

# Absolute path rather than a plain $PATH lookup (pytesseract's
# default behavior, `tesseract_cmd = "tesseract"`): defense in
# depth against a PATH hijack, even though the read-only root
# filesystem (see docker-compose.yml, `read_only: true`) already makes
# this vector impractical — no PATH directory is writable
# at runtime. The Debian `tesseract-ocr` package (see Dockerfile) always
# installs the binary at this location.
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

# First code path of this application to launch a subprocess
# (see seccomp/README.md): without an explicit time bound, a
# blocking `subprocess.Popen` can run indefinitely on a
# pathological/adversarial image, entirely bypassing the
# `_check_detection_deadline` safety net (which only runs AFTER
# OCR returns) — same design flaw as the one already fixed for
# Presidio calls (explicit `timeout=30` in `_analyze_text`), now
# applied symmetrically here.
MAX_OCR_SECONDS = int(os.environ.get("MAX_OCR_SECONDS", "60"))


def _open_and_validate_image(raw: bytes) -> "Image.Image":
    """
    Phase 1 - input validation for an uploaded PNG/JPEG image: same
    security requirement as for images embedded in a PDF (see
    _check_page_images_sane / CVE-2026-3308) — declared dimensions are
    read BEFORE any full pixel decoding. `Image.open()` only
    reads the format header (IHDR block for PNG, SOF marker for JPEG)
    to determine `img.size`; compressed pixel data is only
    decoded on the first actual access (`.load()`, `.getdata()`...), never
    by `Image.open()` alone.
    """
    try:
        with warnings.catch_warnings():
            # Pillow's built-in DecompressionBombWarning only covers the
            # 1x-2x threshold zone (silent by default, not an error) —
            # we enforce our own strict rejection right after for this
            # intermediate case, instead of letting it through with a mere
            # warning.
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(raw))
            width, height = img.size
            image_format = img.format
    except Image.DecompressionBombError as exc:
        raise HTTPException(
            status_code=400,
            detail=STRINGS["image_dimension_limit_bomb"],
        ) from exc
    except (PILUnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise HTTPException(status_code=400, detail=STRINGS["image_unreadable"]) from exc

    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise HTTPException(
            status_code=400,
            detail=STRINGS["image_dimension_limit_pixels"].format(max_pixels=MAX_IMAGE_PIXELS),
        )
    if image_format not in ("PNG", "JPEG"):
        # Should never happen: _detect_file_kind has already validated the
        # binary signature — safety net in case another format
        # sharing a close signature is ever misrouted to "image".
        raise HTTPException(status_code=400, detail=STRINGS["image_format_unrecognized"])

    return img


def _run_ocr(img: "Image.Image") -> list[dict]:
    """
    Runs pytesseract.image_to_data (not image_to_string): returns the text
    AND the position (bounding box in pixels) of each recognized word, with
    lang="fra" — essential for French text to be correctly recognized
    (accents), see Dockerfile for the language data package.
    Fails closed: any OCR engine error is a clean error (never
    a raw traceback), never an implicit "no detection" verdict.
    `timeout=MAX_OCR_SECONDS` (see above) bounds the `tesseract`
    subprocess: beyond that, pytesseract terminates it cleanly (SIGTERM then
    SIGKILL, see its `kill()` function) and raises a RuntimeError, never a
    zombie process or a request blocked indefinitely.
    """
    try:
        data = pytesseract.image_to_data(
            img, lang=OCR_LANGUAGE, output_type=pytesseract.Output.DICT, timeout=MAX_OCR_SECONDS
        )
    except pytesseract.TesseractNotFoundError as exc:
        log.error("Binaire tesseract introuvable : %s", exc)
        raise HTTPException(status_code=503, detail=STRINGS["ocr_engine_unavailable"]) from exc
    except pytesseract.TesseractError as exc:
        log.warning("Échec de l'OCR : %s", exc)
        raise HTTPException(status_code=400, detail=STRINGS["ocr_analysis_failed"]) from exc
    except RuntimeError as exc:
        # pytesseract raises a bare RuntimeError (not TesseractError, caught
        # separately above even though it inherits from it) with the fixed
        # message "Tesseract process timeout" on timeout — see
        # pytesseract.timeout_manager.
        log.warning("Timeout OCR après %ss : %s", MAX_OCR_SECONDS, exc)
        raise HTTPException(
            status_code=400,
            detail=STRINGS["ocr_timeout"].format(max_seconds=MAX_OCR_SECONDS),
        ) from exc

    words: list[dict] = []
    for i in range(len(data.get("text", []))):
        text = data["text"][i]
        if not text or not text.strip():
            # Tesseract also returns bounding-box entries for
            # structural levels (block/paragraph/line) with no actual text —
            # we only keep words that were actually recognized.
            continue
        words.append({
            "text": text,
            "left": data["left"][i],
            "top": data["top"][i],
            "width": data["width"][i],
            "height": data["height"][i],
            "block_num": data["block_num"][i],
            "par_num": data["par_num"][i],
            "line_num": data["line_num"][i],
        })
    return words


def _build_ocr_text(words: list[dict]) -> tuple[str, list[tuple[int, int, dict]]]:
    """
    Rebuilds the full text from the OCR words (space separator within
    a single line, line break between two different lines/paragraphs/
    blocks), keeping for each word its character span (start,
    end) in this rebuilt text — lets us later find the
    bounding box(es) corresponding to an entity detected by character offset
    (see _map_entities_to_word_boxes), same principle as the
    page/rectangle mapping already done for the PDF.
    """
    parts: list[str] = []
    spans: list[tuple[int, int, dict]] = []
    pos = 0
    prev_key = None
    for word in words:
        key = (word["block_num"], word["par_num"], word["line_num"])
        if prev_key is not None:
            sep = "\n" if key != prev_key else " "
            parts.append(sep)
            pos += len(sep)
        start = pos
        parts.append(word["text"])
        pos += len(word["text"])
        spans.append((start, pos, word))
        prev_key = key
    return "".join(parts), spans


def _map_entities_to_word_boxes(entities: list[dict], spans: list[tuple[int, int, dict]]) -> list[dict]:
    """
    Converts each detected entity (character offset in the rebuilt OCR
    text) into one or more detections in pixel coordinates — an
    entity can span several OCR words (e.g. "Jean Durand"), each
    producing its own bounding box, later grouped by
    _cluster_detections just like for the PDF.
    """
    detections: list[dict] = []
    for entity in entities:
        e_start, e_end = entity["start"], entity["end"]
        entity_type = entity.get("entity_type", "UNKNOWN")
        for w_start, w_end, word in spans:
            if w_end <= e_start or w_start >= e_end:
                continue
            rect = [
                float(word["left"]), float(word["top"]),
                float(word["left"] + word["width"]), float(word["top"] + word["height"]),
            ]
            detections.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "page": 0,
                    "entity_type": entity_type,
                    "group_key": None,
                    "page_rect": rect,
                    "display_rect": rect,
                }
            )
    return detections


def _detect_image(img: "Image.Image", theme: dict | None = None) -> list[dict]:
    """
    Detects sensitive entities in an image without redacting them: OCR
    (see _run_ocr) then text reconstruction (_build_ocr_text) passed
    through the project's EXISTING analysis function (_analyze_text — same
    Presidio call, same theme system, same patched engine) before
    remapping each entity to its bounding box(es) (_map_entities_to_word_boxes).
    Does not reimplement any separate Presidio call. Natively handles the
    "no text recognized" case: simply returns an empty list, no error.
    """
    detection_start = time.time()
    words = _run_ocr(img)
    if not words:
        return []
    _check_detection_deadline(detection_start)

    text, spans = _build_ocr_text(words)
    # Same normalizations as for the PDF (helps NER on all-uppercase
    # words and typographic dashes) — preserve the text's length
    # character for character, so the offsets remain valid for
    # remapping onto the spans computed on the original text.
    normalized_text = _normalize_dashes(_normalize_allcaps(text))
    entities = _analyze_text(normalized_text, theme=theme)
    return _map_entities_to_word_boxes(entities, spans)


NOTE_RELTYPES = {
    "footnote": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes",
    "endnote": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes",
}

COMMENT_RELTYPES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
    "http://schemas.microsoft.com/office/2011/relationships/commentsExtended",
    "http://schemas.microsoft.com/office/2011/relationships/commentsIds",
    "http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible",
}

THUMBNAIL_RELTYPE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"

IMAGE_RELTYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def _flatten_revisions_in(root) -> int:
    """
    "Accepts" all tracked-changes revisions in a given XML root
    element: insertions (w:ins) are unwrapped (the
    inserted text becomes a normal run), deletions (w:del) and
    source moves (w:moveFrom) are removed entirely.

    Necessary because python-docx exposes no high-level API for this —
    and especially because the text of a tracked deletion otherwise
    remains present indefinitely in the XML, invisible to `paragraph.runs` and
    therefore to the rest of the detection pipeline (leak confirmed by testing
    during the security audit: a name and a date deleted in tracked-changes
    mode survived intact all the way through to the final "anonymized" file).
    """
    count = 0
    for del_elem in list(root.iter(qn("w:del"))):
        parent = del_elem.getparent()
        if parent is not None:
            parent.remove(del_elem)
            count += 1
    for ins_elem in list(root.iter(qn("w:ins"))):
        parent = ins_elem.getparent()
        if parent is None:
            continue
        index = list(parent).index(ins_elem)
        for child in list(ins_elem):
            parent.insert(index, child)
            index += 1
        parent.remove(ins_elem)
        count += 1
    for move_from in list(root.iter(qn("w:moveFrom"))):
        parent = move_from.getparent()
        if parent is not None:
            parent.remove(move_from)
            count += 1
    for move_to in list(root.iter(qn("w:moveTo"))):
        parent = move_to.getparent()
        if parent is None:
            continue
        index = list(parent).index(move_to)
        for child in list(move_to):
            parent.insert(index, child)
            index += 1
        parent.remove(move_to)
        count += 1
    return count


def _unwrap_hyperlinks_in(root, part) -> tuple[int, int]:
    """
    Unwraps all <w:hyperlink> elements in a given XML root element: the
    displayed text becomes a normal run again (so it's analyzed by the
    detection pipeline like any other text, since `paragraph.runs` doesn't
    descend into a <w:hyperlink> — leak confirmed by testing), and the relation
    to the target (URL/mailto, potentially carrying identifying
    data) is removed — not just detached from the body of the
    text: the target otherwise remained present and extractable in the
    file's relations even after the visible link was removed.
    """
    unwrapped = 0
    dropped_rels = 0
    for hyperlink_elem in list(root.iter(qn("w:hyperlink"))):
        rid = hyperlink_elem.get(qn("r:id"))
        parent = hyperlink_elem.getparent()
        if parent is None:
            continue
        index = list(parent).index(hyperlink_elem)
        for child in list(hyperlink_elem):
            parent.insert(index, child)
            index += 1
        parent.remove(hyperlink_elem)
        unwrapped += 1
        if rid:
            try:
                part.drop_rel(rid)
                dropped_rels += 1
            except KeyError:
                pass  # relation already absent/shared, nothing to do
    return unwrapped, dropped_rels


def _wipe_comments(document: WordDocument) -> int:
    """
    Removes all comments from a document: the markers in the
    body (w:commentRangeStart/End, the run containing w:commentReference) and
    the comment part(s) themselves. No removal API
    exists in python-docx (only add_comment) — full removal
    rather than redacting the content: even a partially masked comment
    would reveal that an internal discussion was about a specific
    person, a partial leak of the shape of the exchange even
    without the name.
    """
    count = 0
    root = document.element
    for tag in ("commentRangeStart", "commentRangeEnd"):
        for elem in list(root.iter(qn(f"w:{tag}"))):
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)
    for ref in list(root.iter(qn("w:commentReference"))):
        run = ref.getparent()
        parent = run.getparent() if run is not None else None
        if parent is not None and run is not None:
            parent.remove(run)
            count += 1

    part = document.part
    for rid in [rid for rid, rel in list(part.rels.items()) if rel.reltype in COMMENT_RELTYPES]:
        part.drop_rel(rid)
    return count


def _wipe_docx_thumbnail(document: WordDocument) -> int:
    """
    Removes the document thumbnail (docProps/thumbnail.jpeg), never
    analyzed by the pipeline (text only): a .docx actually
    saved by Word (unlike a fixture generated by
    python-docx) can embed an actual rendering of the first page in
    pixels, if the "Save thumbnail" option was ever enabled at some
    point — potentially identifying text visible in image form, never
    otherwise read or redacted. The relation to this part is stored
    at the package root level (_rels/.rels, reltype "metadata/thumbnail"),
    not in word/_rels/document.xml.rels like the usual body
    relations — hence access via document.part.package.rels rather than
    document.part.rels. Full removal (same logic as _wipe_comments):
    this is not analyzable text, the only safe protection is to not
    carry this part over into the output file. Verified that the
    part does indeed disappear from the output zip and that the document
    remains openable.
    """
    package = document.part.package
    to_drop = [rid for rid, rel in list(package.rels.items()) if rel.reltype == THUMBNAIL_RELTYPE]
    for rid in to_drop:
        del package.rels[rid]
    return len(to_drop)


def _wipe_core_properties(document: WordDocument) -> int:
    """
    Clears metadata fields likely to carry an identity
    (author, last modified by, comment/subject/keywords/category of the
    document). Unlike the body text, these are structured fields
    whose nature is known by convention — no need to run them
    through NER, we already know that "author" is always a name.
    """
    props = document.core_properties
    count = 0
    for field in ("author", "last_modified_by", "comments", "subject", "keywords", "category"):
        if getattr(props, field, None):
            setattr(props, field, "")
            count += 1
    return count


def _get_note_part(document: WordDocument, note_kind: str):
    """
    Returns (part, root) for the footnotes/endnotes part, or
    (None, None) if absent. `root` is an lxml tree freshly parsed
    from part.blob — this part is not a standard XmlPart (no
    .element attribute, no live view), so any modification must
    be explicitly written back into part._blob before saving, see
    _save_note_parts.
    """
    reltype = NOTE_RELTYPES[note_kind]
    for rel in document.part.rels.values():
        if rel.reltype == reltype:
            part = rel.target_part
            return part, parse_xml(part.blob)
    return None, None


def _collect_docx_image_parts(document: WordDocument) -> list:
    """
    Returns the list of distinct image parts referenced from the
    body, headers/footers, and footnotes/endnotes —
    the same parts already traversed for text, see _iter_docx_paragraphs.
    Covers both "inline" (wp:inline) and floating
    (wp:anchor) images: the search happens at the OPC relations
    level (image reltype), not via document.inline_shapes which only exposes the
    first case.

    Deduplicated by partname (`/word/media/imageN.ext`): the same image
    referenced twice (the same relation reused in two places, or two
    distinct relations pointing to an identical object after
    deduplication already done by Word) appears only once on the review side.
    Accepted consequence: checking such an image does redact all
    occurrences at once (safe behavior — over-redacting is never
    the problem, unlike the reverse).
    """
    parts_to_scan = [document.part]
    seen_part_ids = {id(document.part)}
    for section in document.sections:
        for header_or_footer in (section.header, section.footer):
            if header_or_footer is not None and id(header_or_footer.part) not in seen_part_ids:
                seen_part_ids.add(id(header_or_footer.part))
                parts_to_scan.append(header_or_footer.part)
    for note_kind in NOTE_RELTYPES:
        note_part, _root = _get_note_part(document, note_kind)
        if note_part is not None:
            parts_to_scan.append(note_part)

    images_by_partname = {}
    for part in parts_to_scan:
        for rel in part.rels.values():
            if rel.reltype == IMAGE_RELTYPE and not rel.is_external:
                images_by_partname[rel.target_part.partname] = rel.target_part
    return list(images_by_partname.values())


_DOCX_IMAGE_PLACEHOLDER_FORMATS = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg"}


def _black_placeholder_image_bytes(content_type: str) -> bytes:
    """
    Solid black 64x64 image, generated on the fly with PyMuPDF (already a
    project dependency — no need for Pillow). Word resizes the
    display according to the dimensions declared in the document's XML
    (<a:ext cx=".." cy="..">), so the replacement file's pixel
    size does not need to match the original.

    Re-encoded in the same format as the original for png/jpeg (the vast
    majority of pasted screenshots/photos). For any other format
    (gif/bmp/tiff/wmf/emf...), a PNG is used anyway, WITHOUT changing
    the part's declared type — accepted, documented mismatch: rare
    in practice for this use case, and the security guarantee (original
    bytes unrecoverable) holds in all cases; only Word's rendering
    fidelity can suffer for these exotic formats.
    """
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 64, 64))
    pix.set_rect(pix.irect, (0, 0, 0))
    fmt = _DOCX_IMAGE_PLACEHOLDER_FORMATS.get((content_type or "").lower(), "png")
    return pix.tobytes(fmt)


def _apply_docx_image_redactions(document: WordDocument, redacted_partnames: set) -> int:
    """
    Replaces the binary content of each selected image with a solid
    black square — same guarantee as manual PDF redaction
    (_apply_manual_redactions): the original byte must not survive anywhere
    in the output file. Unlike PDF (incremental save by
    default, requiring garbage=4/clean=True to purge
    pre-redaction objects), DOCX/OPC writing rewrites each part
    exactly once from its current blob (OpcPackage.save via
    iter_parts, derived from the live relationship graph) — no
    additional purge needed here.

    WHOLE-IMAGE granularity, not a precise pixel area like for PDF:
    DOCX exposes no fixed layout in coordinates without a full
    rendering engine, so drawing a precise rectangle isn't possible here.
    """
    if not redacted_partnames:
        return 0
    count = 0
    for part in _collect_docx_image_parts(document):
        if str(part.partname) in redacted_partnames:
            part._blob = _black_placeholder_image_bytes(part.content_type)
            count += 1
    return count


def _build_docx_images_review_section(document: WordDocument) -> str:
    """
    Builds the "document images" section of the DOCX review screen:
    one checkbox per distinct image, UNCHECKED by default — a
    manual, opt-in mechanism, same logic as manual PDF zones (nothing is
    redacted without explicit user action, since these images are
    not analyzed by NER, see the warning displayed just before).
    """
    parts = _collect_docx_image_parts(document)[:MAX_DOCX_IMAGES]
    if not parts:
        return ""

    cards = []
    for part in parts:
        image_id = html.escape(str(part.partname))
        blob = part.blob
        size_kb = len(blob) / 1024
        content_type = part.content_type or "application/octet-stream"
        if len(blob) <= MAX_DOCX_IMAGE_PREVIEW_BYTES:
            b64 = base64.b64encode(blob).decode("ascii")
            preview = (
                f'<img src="data:{html.escape(content_type)};base64,{b64}" '
                f'style="max-width:180px; max-height:180px; display:block; border:1px solid #ccc;">'
            )
        else:
            preview = (
                '<div style="width:180px; height:100px; display:flex; align-items:center; '
                'justify-content:center; border:1px dashed #999; color:#666; font-size:0.8em; '
                f'text-align:center;">{STRINGS["docx_image_preview_unavailable"].format(size_kb=f"{size_kb:.0f}")}</div>'
            )
        size_suffix = STRINGS["docx_image_size_suffix"].format(content_type=html.escape(content_type), size_kb=f"{size_kb:.0f}")
        cards.append(f"""
        <label style="display:inline-block; margin:8px 12px 8px 0; text-align:center; cursor:pointer; vertical-align:top;">
          {preview}
          <div style="margin-top:4px; font-size:0.85em;">
            <input type="checkbox" class="docx-image-checkbox" data-image-id="{image_id}">
            {STRINGS["docx_image_redact_label"]} <span style="color:#888;">{size_suffix}</span>
          </div>
        </label>
        """)

    return f"""
    <div style="margin: 0 0 16px 0; padding:12px; background:#f8f8f8; border-radius:4px;">
      <p style="margin-top:0;">{STRINGS["docx_images_found_intro"].format(count=len(parts))}</p>
      {"".join(cards)}
    </div>
    """


def _save_note_parts(note_parts: dict) -> None:
    """Rewrites the blob of footnote/endnote parts after
    their paragraphs are modified — necessary before document.save(),
    see _get_note_part."""
    for part, root in note_parts.values():
        part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _iter_docx_paragraphs(document: WordDocument):
    """
    Walks all paragraphs of a document in a deterministic order
    that is STABLE across two successive openings of the same file
    (essential for matching the same block indices between
    detection and finalization).

    Covers: body, table cells (recursive), headers and footers
    of each section, footnotes and endnotes. On EVERY
    part traversed (body, headers/footers, notes), tracked
    changes are flattened and hyperlinks are unwrapped BEFORE
    paragraph extraction — see _flatten_revisions_in /
    _unwrap_hyperlinks_in for why (leaks confirmed by
    the security audit, otherwise invisible to `paragraph.runs`).

    Returns (blocks, note_parts):
      - blocks: list of [(label, paragraph, table_ref), ...].
      - note_parts: dict {"footnote"|"endnote": (part, root)} for the
        parts whose content was included — MUST BE PASSED BACK TO
        _save_note_parts after editing the runs, before document.save().

    KNOWN LIMITATION: does not include the text of text boxes, shapes,
    SmartArt, or embedded OLE objects, nor images embedded in the
    body (`<w:drawing>`) — python-docx doesn't expose it / it isn't
    text analyzable by NER. Comments, metadata, and the
    document thumbnail are not redacted but entirely removed
    upstream during finalization (_wipe_comments / _wipe_core_properties /
    _wipe_docx_thumbnail), so they're never presented for review either.
    """
    blocks = []

    _flatten_revisions_in(document.element)
    _unwrap_hyperlinks_in(document.element, document.part)

    # `label` is a language-neutral internal identifier, not display text:
    # it is translated only at render time via STRINGS["docx_block_label_*"]
    # (see _handle_detect_docx) — "body" is the one exception, never
    # displayed (see the `label != "body"` check there).
    for p in document.paragraphs:
        blocks.append(("body", p, None))

    for table in document.tables:
        table_id = id(table)
        for row_idx, row in enumerate(table.rows):
            for col_idx, cell in enumerate(row.cells):
                for p in cell.paragraphs:
                    blocks.append(("table", p, (table_id, row_idx, col_idx)))
                for nested_table in cell.tables:
                    for nested_row in nested_table.rows:
                        for nested_cell in nested_row.cells:
                            for p in nested_cell.paragraphs:
                                blocks.append(("table", p, None))

    for section in document.sections:
        if section.header is not None:
            _flatten_revisions_in(section.header.part.element)
            _unwrap_hyperlinks_in(section.header.part.element, section.header.part)
            for p in section.header.paragraphs:
                blocks.append(("header", p, None))
        if section.footer is not None:
            _flatten_revisions_in(section.footer.part.element)
            _unwrap_hyperlinks_in(section.footer.part.element, section.footer.part)
            for p in section.footer.paragraphs:
                blocks.append(("footer", p, None))

    note_parts = {}
    note_labels = {"footnote": "footnote", "endnote": "endnote"}
    for note_kind, label in note_labels.items():
        part, root = _get_note_part(document, note_kind)
        if root is None:
            continue
        _flatten_revisions_in(root)
        _unwrap_hyperlinks_in(root, part)
        note_parts[note_kind] = (part, root)
        for note_elem in root.findall(qn(f"w:{note_kind}")):
            if note_elem.get(qn("w:id")) in ("-1", "0"):
                continue
            for p_elem in note_elem.findall(qn("w:p")):
                blocks.append((label, Paragraph(p_elem, document.part), None))

    return blocks, note_parts


def _cluster_text_detections(detections: list[dict]) -> list[dict]:
    """
    Groups detections whose character ranges overlap, within
    the same block — 1D equivalent of the IoU-based grouping used for
    the PDF (_cluster_detections), for the same reason: prevent one
    clickable zone from hiding another one invisibly at the same spot.
    """
    by_block: dict = {}
    for d in detections:
        by_block.setdefault(d["block_id"], []).append(d)

    clusters = []
    for block_id, block_dets in by_block.items():
        block_dets = sorted(block_dets, key=lambda d: d["start"])
        used = [False] * len(block_dets)
        for i, d in enumerate(block_dets):
            if used[i]:
                continue
            group = [d]
            used[i] = True
            group_end = d["end"]
            for j in range(i + 1, len(block_dets)):
                if used[j]:
                    continue
                if block_dets[j]["start"] < group_end:
                    group.append(block_dets[j])
                    used[j] = True
                    group_end = max(group_end, block_dets[j]["end"])
            group_key = next((g.get("group_key") for g in group if g.get("group_key")), None)
            clusters.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "block_id": block_id,
                    "start": min(g["start"] for g in group),
                    "end": max(g["end"] for g in group),
                    "entity_types": sorted({g["entity_type"] for g in group}),
                    "member_ids": [g["id"] for g in group],
                    "group_key": group_key,
                }
            )
    return clusters


# An isolated fragment (a table cell, a column header, a
# "Name: Smith" line without the rest of the sentence) deprives the NER model of the
# context that helps it decide between "proper noun" and "ordinary
# capitalized word" — this is what causes both false negatives (a name
# actually present but not detected, for lack of context) and false
# positives (an ordinary word at the start of a cell, capitalized by
# _normalize_allcaps, mistaken for a name). We therefore group several
# contiguous blocks into a single Presidio call instead of analyzing them one
# by one, to recreate a "full page" effect comparable to what
# works well for PDF.
TEXT_CHUNK_MAX_CHARS = 8000
TEXT_CHUNK_MAX_BLOCKS = 200


# Column header keywords (first row of a DOCX or CSV table)
# directly associated with an entity type: a match here
# triggers redaction of the ENTIRE column via a structural rule, without
# relying on NER. Necessary because an isolated cell ("Jean Durand"
# alone, without a surrounding sentence) offers no grammatical context to the
# NER model to recognize it as a name — the table structure (the column
# header) is a much more reliable signal here than generic NER.
# Customizable/extendable per document without touching the code: add a
# "column_entity_keywords" key (dict keyword -> entity type) in common.json
# or in a specific theme (the theme takes priority over the common values).
DEFAULT_COLUMN_ENTITY_KEYWORDS = {
    "nom": "PERSON",
    "prénom": "PERSON",
    "prenom": "PERSON",
    "nom et prénom": "PERSON",
    "nom du patient": "PERSON",
    "nom patient": "PERSON",
    "patient": "PERSON",
    "nom de famille": "PERSON",
    "adresse": "LOCATION",
    "lieu de formation": "LOCATION",
    "lieu de naissance": "LOCATION",
    "lieu de résidence": "LOCATION",
    "lieu": "LOCATION",
    "ville": "LOCATION",
    "commune": "LOCATION",
    "email": "EMAIL_ADDRESS",
    "e-mail": "EMAIL_ADDRESS",
    "mail": "EMAIL_ADDRESS",
    "courriel": "EMAIL_ADDRESS",
    "téléphone": "PHONE_NUMBER",
    "telephone": "PHONE_NUMBER",
    "tél": "PHONE_NUMBER",
    "date de naissance": "DATE_TIME",
    "date": "DATE_TIME",
    "né le": "DATE_TIME",
    "née le": "DATE_TIME",
}


def _load_column_keywords() -> dict[str, str]:
    """Loads the default column keywords, augmented/overridden by an
    optional "column_entity_keywords" key in common.json (same
    conventions as _load_common_recognizers)."""
    keywords = dict(DEFAULT_COLUMN_ENTITY_KEYWORDS)
    path = THEMES_DIR / COMMON_RECOGNIZERS_FILENAME
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            keywords.update({k.lower(): v for k, v in data.get("column_entity_keywords", {}).items()})
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Mots-clés de colonne illisibles dans common.json, valeurs par défaut utilisées: %s", exc)
    return keywords


COLUMN_ENTITY_KEYWORDS = _load_column_keywords()


def _resolve_column_keywords(theme: dict | None) -> dict[str, str]:
    """Effective column keywords for a given theme: the common
    values, optionally augmented/overridden by the theme's own."""
    keywords = dict(COLUMN_ENTITY_KEYWORDS)
    if theme and theme.get("column_entity_keywords"):
        keywords.update({k.lower(): v for k, v in theme["column_entity_keywords"].items()})
    return keywords


MAX_LABEL_CHARS = 40  # a real field label ("Name", "Date of birth") is short;
                       # a long description paragraph is never one, even if it
                       # happens to contain a keyword substring (e.g. "nom" in
                       # "dénomination" or "installation").


def _match_column_keyword(text: str, column_keywords: dict[str, str]) -> str | None:
    """
    Checks whether `text` matches a known column keyword, requiring:
      1. A WHOLE-WORD match (\\b boundaries), never a plain
         substring — "nom" must NOT match inside
         "dénomination", "installation" or "anonyme".
      2. A candidate text short enough (MAX_LABEL_CHARS) to be
         plausibly a field label, not a description paragraph
         that happens to contain the word.
    Fixes a real bug observed in production: without these two guardrails, a
    table whose first cell in a row is a long description
    (instead of a label) could wrongly redact the whole row
    (dates, checkboxes...) as soon as it contained the letter sequence
    of a keyword anywhere in the text.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > MAX_LABEL_CHARS:
        return None
    lowered = stripped.lower()
    for keyword, entity_type in column_keywords.items():
        if re.search(r"\b" + re.escape(keyword) + r"\b", lowered):
            return entity_type
    return None


def _docx_table_structural_entities(blocks: list, block_texts: list[str], column_keywords: dict[str, str]) -> list[dict]:
    """
    Per-table structural detection, independent of NER, with two
    recognized patterns (a table falls under only one of the two):

      1. COLUMN HEADER: row 0 has at least 2 columns whose
         heading matches a known keyword (e.g. "Name | First name | City"
         on row 0, followed by several data rows) -> the whole
         column is redacted on the following rows.
      2. LABEL: VALUE PER ROW: each row carries its own
         label in the first non-empty cell (e.g. one row "Name" -> a
         value, another row "First name" -> another value) — a very
         common pattern in forms/certificates, one piece of information per
         row rather than a table of several records. The
         following cells of THAT row are then redacted.

    Pattern 1 is tried first; pattern 2 only applies to
    tables that were not recognized as "column header", so that
    a real header row is never interpreted as a label
    (which would then redact the other headers on the same row).
    See _match_column_keyword for the anti-false-positive guardrails.
    """
    if not column_keywords:
        return []

    cells: dict[tuple, list[int]] = {}
    for block_id, (label, _, table_ref) in enumerate(blocks):
        if label == "table" and table_ref is not None:
            cells.setdefault(table_ref, []).append(block_id)

    def cell_text(block_ids: list[int]) -> str:
        return " ".join(block_texts[b] for b in block_ids).strip()

    def match_keyword(text: str) -> str | None:
        return _match_column_keyword(text, column_keywords)

    def add_detection(detections: list[dict], block_id: int, entity_type: str) -> None:
        text = block_texts[block_id]
        stripped = text.strip()
        if len(stripped) < 2:
            return
        start = text.find(stripped)
        is_propagatable = entity_type in PROPAGATED_ENTITY_TYPES and (
            " " in stripped or (entity_type == "LOCATION" and len(stripped) >= 4)
        )
        detections.append(
            {
                "id": uuid.uuid4().hex[:12],
                "block_id": block_id,
                "start": start,
                "end": start + len(stripped),
                "entity_type": entity_type,
                "group_key": stripped if is_propagatable else None,
                "source": "structural",
            }
        )

    rows_by_table: dict[int, dict[int, dict[int, list[int]]]] = {}
    for (table_id, row_idx, col_idx), block_ids in cells.items():
        rows_by_table.setdefault(table_id, {}).setdefault(row_idx, {})[col_idx] = block_ids

    detections: list[dict] = []

    for table_id, rows in rows_by_table.items():
        # --- Pattern 1: column header on row 0 ---
        header_matches: dict[int, str] = {}
        for col_idx, block_ids in rows.get(0, {}).items():
            entity_type = match_keyword(cell_text(block_ids))
            if entity_type:
                header_matches[col_idx] = entity_type

        # A header with no data row below it isn't a
        # real "header + records" table — it's very probably
        # a single label:value row (pattern 2) where 2 columns match
        # a keyword by coincidence (e.g. "Name" and "Date" on the same row).
        # Without this guardrail, pattern 1 would wrongly claim the table and
        # would produce no useful detection (nothing to do on the following
        # rows since there aren't any).
        if len(header_matches) >= 2 and len(rows) >= 2:
            for row_idx, cols in rows.items():
                if row_idx == 0:
                    continue
                for col_idx, block_ids in cols.items():
                    entity_type = header_matches.get(col_idx)
                    if entity_type:
                        for block_id in block_ids:
                            add_detection(detections, block_id, entity_type)
            continue  # table already handled by pattern 1

        # --- Pattern 2: label: value, ONE OR SEVERAL pairs per row ---
        # A row can contain several fields side by side (e.g. "Name: X
        # Date: Y" in a single table row) — we scan the row in
        # column order and switch labels as soon as a new
        # cell matches a keyword, rather than assuming a single
        # label for the whole row (real bug observed: "Date" and its
        # value were wrongly absorbed into the "Name" detection).
        for row_idx, cols in rows.items():
            sorted_cols = sorted(cols.items())
            current_entity_type: str | None = None
            for col_idx, block_ids in sorted_cols:
                text = cell_text(block_ids)
                if len(text) >= 2:
                    matched = match_keyword(text)
                    if matched:
                        current_entity_type = matched
                        continue  # this cell IS the label, not a value
                if current_entity_type:
                    for block_id in block_ids:
                        add_detection(detections, block_id, current_entity_type)

    return detections


def _csv_structural_entities(rows: list[list[str]], column_keywords: dict[str, str]) -> list[dict]:
    """CSV equivalent of _docx_table_structural_entities, same two patterns
    (multiple column headers on row 0, or label:value per row
    with a single recognized column in the first position)."""
    if not rows or not column_keywords:
        return []

    def match_keyword(text: str) -> str | None:
        return _match_column_keyword(text, column_keywords)

    def add_detection(detections: list[dict], row_idx: int, col_idx: int, cell: str, entity_type: str) -> None:
        stripped = cell.strip()
        if len(stripped) < 2:
            return
        start = cell.find(stripped)
        is_propagatable = entity_type in PROPAGATED_ENTITY_TYPES and (
            " " in stripped or (entity_type == "LOCATION" and len(stripped) >= 4)
        )
        detections.append(
            {
                "id": uuid.uuid4().hex[:12],
                "block_id": f"{row_idx}:{col_idx}",
                "start": start,
                "end": start + len(stripped),
                "entity_type": entity_type,
                "group_key": stripped if is_propagatable else None,
                "source": "structural",
            }
        )

    detections: list[dict] = []

    header_matches: dict[int, str] = {}
    for col_idx, cell in enumerate(rows[0]):
        entity_type = match_keyword(cell)
        if entity_type:
            header_matches[col_idx] = entity_type

    if len(header_matches) >= 2 and len(rows) >= 2:
        for row_idx in range(1, len(rows)):
            for col_idx, cell in enumerate(rows[row_idx]):
                entity_type = header_matches.get(col_idx)
                if entity_type:
                    add_detection(detections, row_idx, col_idx, cell, entity_type)
        return detections

    for row_idx, row in enumerate(rows):
        current_entity_type: str | None = None
        for col_idx, cell in enumerate(row):
            if len(cell.strip()) >= 2:
                matched = match_keyword(cell)
                if matched:
                    current_entity_type = matched
                    continue
            if current_entity_type:
                add_detection(detections, row_idx, col_idx, cell, current_entity_type)

    return detections


def _detect_text_blocks(block_texts: list, theme: dict | None = None, extra_detections: list[dict] | None = None) -> list[dict]:
    """
    Generic detection over a list of indexed text blocks (DOCX
    paragraphs or CSV cells) — same two-pass logic as _detect_pdf
    (standard batch detection, then propagation of confirmed names/cities
    to the rest of the document), but in 1D (character offsets) rather than
    PDF rectangles. See TEXT_CHUNK_MAX_CHARS above for why we
    group into batches.
    """
    detections: list[dict] = list(extra_detections or [])
    already_covered: set[tuple] = {(d["block_id"], d["start"], d["end"]) for d in detections}
    propagate_candidates: dict[str, str] = {
        d["group_key"]: d["entity_type"] for d in detections if d.get("group_key")
    }

    def _process_chunk(items: list[tuple]) -> None:
        if not items:
            return
        text_by_block = dict(items)
        combined_parts = []
        offsets = []  # (block_id, start_in_combined, block_len)
        pos = 0
        for block_id, text in items:
            offsets.append((block_id, pos, len(text)))
            combined_parts.append(text)
            pos += len(text) + 1  # +1 for the "\n" separator inserted below

        combined_text = "\n".join(combined_parts)
        normalized = _normalize_dashes(_normalize_allcaps(combined_text))
        entities = _analyze_text(normalized, theme=theme)

        for entity in entities:
            for block_id, start_in_combined, length in offsets:
                if not (start_in_combined <= entity["start"] < start_in_combined + length):
                    continue
                local_start = entity["start"] - start_in_combined
                local_end = min(entity["end"] - start_in_combined, length)
                block_text = text_by_block[block_id]
                stripped = block_text[local_start:local_end].strip()
                if len(stripped) < 3:
                    break
                entity_type = entity.get("entity_type", "UNKNOWN")
                is_propagatable = entity_type in PROPAGATED_ENTITY_TYPES and (
                    " " in stripped or (entity_type == "LOCATION" and len(stripped) >= 4)
                )
                key = (block_id, local_start, local_end)
                if key in already_covered:
                    break
                already_covered.add(key)
                detections.append(
                    {
                        "id": uuid.uuid4().hex[:12],
                        "block_id": block_id,
                        "start": local_start,
                        "end": local_end,
                        "entity_type": entity_type,
                        "group_key": stripped if is_propagatable else None,
                        "source": "ner",
                        "score": entity.get("score"),
                    }
                )
                if is_propagatable:
                    propagate_candidates[stripped] = entity_type
                break  # an interval belongs to a single block, no need to continue

    detection_start = time.time()
    chunk: list[tuple] = []
    chunk_chars = 0
    for block_id, text in block_texts:
        if not text.strip():
            continue
        if chunk and (chunk_chars + len(text) > TEXT_CHUNK_MAX_CHARS or len(chunk) >= TEXT_CHUNK_MAX_BLOCKS):
            _check_detection_deadline(detection_start)
            _process_chunk(chunk)
            chunk, chunk_chars = [], 0
        chunk.append((block_id, text))
        chunk_chars += len(text)
    _process_chunk(chunk)

    for name, propagated_entity_type in propagate_candidates.items():
        for variant in _name_variants(name):
            for block_id, text in block_texts:
                idx = text.find(variant)
                while idx != -1:
                    key = (block_id, idx, idx + len(variant))
                    if key not in already_covered:
                        already_covered.add(key)
                        detections.append(
                            {
                                "id": uuid.uuid4().hex[:12],
                                "block_id": block_id,
                                "start": idx,
                                "end": idx + len(variant),
                                "entity_type": propagated_entity_type,
                                "group_key": name,
                                "source": "propagation",
                            }
                        )
                    idx = text.find(variant, idx + 1)

    return detections


def _apply_text_redactions(non_excluded_detections: list[dict]) -> tuple[dict, dict[str, int]]:
    """
    Groups confirmed (non-excluded) detections by block into merged
    intervals — needed to edit the text without overlap — while
    keeping the count per entity type for the summary (the same zone
    detected by two different recognizers counts twice, as for
    the PDF).
    """
    summary: dict[str, int] = {}
    by_block: dict = {}
    for d in non_excluded_detections:
        by_block.setdefault(d["block_id"], []).append((d["start"], d["end"]))
        summary[d["entity_type"]] = summary.get(d["entity_type"], 0) + 1

    merged_by_block = {}
    for block_id, spans in by_block.items():
        spans = sorted(spans)
        merged = []
        for s, e in spans:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        merged_by_block[block_id] = merged

    return merged_by_block, summary


def _apply_docx_paragraph_redactions(paragraph, intervals: list[tuple[int, int]]) -> None:
    """
    Actually replaces the text of the runs covered by `intervals` (offsets
    into the paragraph's concatenated text) with REDACTION_MARKER — modifies the
    underlying XML (run.text), not just a visual wrapper.

    The same logical entity can be split across several runs (mixed
    formatting in the middle of a word, common with spell
    checkers or after a copy-paste): in that case, only ONE
    REDACTION_MARKER is inserted, in the first affected run — the
    following runs affected by the SAME entity simply have their
    portion removed (empty string), never a second marker. Without this rule, an
    entity spanning 2 runs would produce two concatenated markers
    ("[MASQUÉ][MASQUÉ]") instead of one — bug fixed after observing it
    in real usage (artifacts like "[MASQUÉ]]]" caused by badly split runs).
    """
    if not intervals:
        return

    runs = paragraph.runs
    run_spans = []
    pos = 0
    for i, run in enumerate(runs):
        length = len(run.text)
        run_spans.append((i, pos, pos + length))
        pos += length

    # ops[i]: list of (local_start, local_end, replacement) for run i.
    # `replacement` is REDACTION_MARKER for the first run touched by a
    # given interval, "" for the following runs of the same interval.
    ops: dict[int, list[tuple[int, int, str]]] = {i: [] for i in range(len(runs))}
    for g_start, g_end in intervals:
        marker_placed = False
        for i, r_start, r_end in run_spans:
            ov_start, ov_end = max(g_start, r_start), min(g_end, r_end)
            if ov_start < ov_end:
                replacement = REDACTION_MARKER if not marker_placed else ""
                ops[i].append((ov_start - r_start, ov_end - r_start, replacement))
                marker_placed = True

    for i, edits in ops.items():
        if not edits:
            continue
        text = runs[i].text
        for local_start, local_end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
            text = text[:local_start] + replacement + text[local_end:]
        runs[i].text = text


def _render_highlighted_text(text: str, block_clusters: list[dict]) -> str:
    """Splits `text` into segments and inserts a clickable <span> for each
    cluster (detected zone), in order, without overlap (guaranteed by
    _cluster_text_detections). The text is HTML-escaped before insertion."""
    block_clusters = sorted(block_clusters, key=lambda c: c["start"])
    pieces = []
    pos = 0
    for c in block_clusters:
        if c["start"] > pos:
            pieces.append(html.escape(text[pos : c["start"]]))
        segment = html.escape(text[c["start"] : c["end"]])
        group_attr = hashlib.md5(c["group_key"].encode()).hexdigest()[:12] if c.get("group_key") else ""
        pieces.append(
            f'<span class="text-detection" data-id="{c["id"]}" data-group="{group_attr}" '
            f'title="{", ".join(c["entity_types"])}" onclick="toggleTextDetection(this)">{segment}</span>'
        )
        pos = c["end"]
    if pos < len(text):
        pieces.append(html.escape(text[pos:]))
    return "".join(pieces)


def _build_text_review_page(
    job_id: str, total_detections: int, blocks_html: str, extra_note: str = "", images_html: str = ""
) -> HTMLResponse:
    """Common DOCX/CSV review template: inline highlighting rather than
    image rendering, no manual pixel-precise zone drawing like for PDF
    (see documented limitation) — `images_html` (DOCX only) does,
    however, allow redacting a whole embedded image, see
    _build_docx_images_review_section."""
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="{UI_LANG}">
    <head>
      <meta charset="utf-8">
      <title>{STRINGS["review_page_title"]}</title>
      <style>
        .text-detection {{
          background: rgba(220, 40, 40, 0.25);
          border-bottom: 2px solid rgba(220, 40, 40, 0.9);
          cursor: pointer;
        }}
        .text-detection.excluded {{
          background: rgba(40, 170, 40, 0.12);
          border-bottom: 2px dashed rgba(40, 140, 40, 0.7);
        }}
        .doc-block {{ margin-bottom: 10px; white-space: pre-wrap; line-height: 1.6; }}
        .doc-block-label {{
          display:inline-block; font-size:0.75em; color:#888; text-transform:uppercase;
          margin-right:6px;
        }}
        .toolbar {{
          position: sticky; top: 0; background: white; padding: 12px 0;
          border-bottom: 1px solid #ddd; margin-bottom: 16px; z-index: 10;
        }}
      </style>
    </head>
    <body style="font-family: sans-serif; max-width: 900px; margin: 0 auto; padding: 0 20px;">
      <div class="toolbar">
        <p><a href="/">&larr; {STRINGS["restart_link"]}</a></p>
        <p>
          {STRINGS["review_zone_count_text"].format(count=total_detections)}
        </p>
        {extra_note}
        <form id="finalize-form" action="/api/finalize" method="post">
          <input type="hidden" name="job_id" value="{job_id}">
          <input type="hidden" name="format" value="html">
          <input type="hidden" id="excluded_ids" name="excluded_ids" value="">
          <input type="hidden" id="redacted_image_ids" name="redacted_image_ids" value="">
          <button type="button" onclick="submitTextFinalize()" style="
              padding:10px 20px; background:#0d6efd; color:white; border:none;
              border-radius:4px; cursor:pointer; font-size:1em;">
            {STRINGS["confirm_and_redact_button"]}
          </button>
        </form>
      </div>

      {images_html}

      {blocks_html}

      <script>
        function toggleTextDetection(el) {{
          el.classList.toggle('excluded');
          const group = el.dataset.group;
          if (!group) return;
          const state = el.classList.contains('excluded');
          document.querySelectorAll(`.text-detection[data-group="${{group}}"]`).forEach(sibling => {{
            sibling.classList.toggle('excluded', state);
          }});
        }}
        function submitTextFinalize() {{
          const excluded = Array.from(document.querySelectorAll('.text-detection.excluded'))
                                 .map(el => el.dataset.id);
          document.getElementById('excluded_ids').value = excluded.join(',');
          const redactedImages = Array.from(document.querySelectorAll('.docx-image-checkbox:checked'))
                                       .map(el => el.dataset.imageId);
          document.getElementById('redacted_image_ids').value = redactedImages.join(',');
          document.getElementById('finalize-form').submit();
        }}
      </script>
    </body>
    </html>
    """)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics_endpoint():
    """
    Prometheus exposition endpoint (see metrics.py). Does NOT go through
    oauth2-proxy authentication — a Prometheus scraper doesn't do an
    OAuth dance — but remains protected by ipallowlist (see the
    dedicated `app-metrics` Traefik router in docker-compose.yml, restricted by
    default to 127.0.0.1) rather than left open to the whole Internet.
    """
    body, content_type = metrics.metrics_response()
    return Response(content=body, media_type=content_type)


@app.get("/", response_class=HTMLResponse)
def upload_form():
    options = f'<option value="">{STRINGS["theme_none_option"]}</option>'
    for key, theme in THEMES.items():
        options += f'<option value="{key}">{THEME_LABELS.get(key, key)}</option>'

    return f"""
    <!doctype html>
    <html lang="{UI_LANG}"><head><meta charset="utf-8"><title>{STRINGS["home_page_title"]}</title></head>
    <body style="font-family: sans-serif; max-width: 600px; margin: 40px auto;">
      <h1>{STRINGS["home_page_heading"]}</h1>
      <p>{STRINGS["home_page_intro"]}</p>
      <form action="/api/detect" method="post" enctype="multipart/form-data">
        <p>
          <label for="theme">{STRINGS["theme_select_label"]}</label><br>
          <select name="theme" id="theme">{options}</select>
        </p>
        <input type="file" name="file" accept=".pdf,.docx,.csv,.png,.jpg,.jpeg" required>
        <button type="submit">{STRINGS["analyze_button"]}</button>
      </form>
    </body>
    </html>
    """


@app.post("/api/detect")
async def detect_document(
    request: Request,
    file: UploadFile = File(...),
    theme: str = Form(default=""),
):
    """
    Phase 1 of the review flow, common to the four accepted formats
    (PDF/DOCX/CSV/image): detects entities without redacting them, stores the
    job in memory, returns a review page (image rendering + pixel
    clickable zones for PDF and image; inline highlighting for
    DOCX/CSV — see _build_text_review_page). The file's actual type is
    determined from its binary content (_detect_file_kind), never from the
    Content-Type declared by the client, which is forgeable.
    """
    raw = await file.read()

    size_mb = len(raw) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        raise HTTPException(
            status_code=413,
            detail=STRINGS["upload_too_large"].format(size_mb=f"{size_mb:.1f}", max_mb=MAX_UPLOAD_MB),
        )

    filename_hash = hashlib.sha256((file.filename or "").encode()).hexdigest()[:12]

    _run_antivirus_scan(raw, file.filename or "", filename_hash)

    kind = _detect_file_kind(raw)

    # Global quota of jobs pending review (independent of the rate
    # limited by Traefik): prevents raw documents from accumulating in
    # memory beyond a safe threshold, even while staying under the
    # per-IP rate limit.
    with _PENDING_JOBS_LOCK:
        if len(PENDING_JOBS) >= MAX_PENDING_JOBS:
            raise HTTPException(
                status_code=503,
                detail=STRINGS["too_many_pending_jobs"],
            )

    # The `theme` field is a form field freely controlled by the
    # client (forgeable, unbounded, not constrained to a known theme). It
    # later joins the audit log (`_record_audit_event(theme=...)`)
    # AND the output file name (`theme_slug`). Without handling here:
    #  - a Unicode formatting character (RTL override U+202E, category
    #    Cf) passes through intact and enables the same visual log spoofing
    #    already neutralized on `X-Auth-Request-Email` (audit section 3.5);
    #  - a very long value produces a `theme_slug` exceeding the
    #    system's filename length limit (Errno 36), causing
    #    finalization to fail with a misleading message ("corrupted file").
    # Sanitized once, at the source, exactly like `user_email` below,
    # then length-bounded (a real theme is short: medical/it/accounting).
    theme = _strip_unicode_control_and_format_chars(theme)[:MAX_THEME_CHARS]

    selected_theme = THEMES.get(theme) if theme else None
    if theme and selected_theme is None:
        log.warning("Thème inconnu demandé (%r), poursuite sans thème", theme)

    user_email = _strip_unicode_control_and_format_chars(
        request.headers.get("x-auth-request-email", "inconnu")
    )
    job_id = uuid.uuid4().hex

    if kind == "docx":
        return _handle_detect_docx(raw, theme, selected_theme, job_id, filename_hash, user_email, size_mb)
    if kind == "csv":
        return _handle_detect_csv(raw, theme, selected_theme, job_id, filename_hash, user_email, size_mb)
    if kind == "image":
        return _handle_detect_image(raw, theme, selected_theme, job_id, filename_hash, user_email, size_mb)
    return _handle_detect_pdf(raw, theme, selected_theme, job_id, filename_hash, user_email, size_mb)


def _handle_detect_pdf(raw, theme, selected_theme, job_id, filename_hash, user_email, size_mb):
    """PDF detection logic — unchanged from the PDF-only version,
    only extracted into its own function to allow dispatching."""
    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=STRINGS["pdf_unreadable"]) from exc

    if doc.needs_pass:
        doc.close()
        raise HTTPException(
            status_code=400,
            detail=STRINGS["pdf_password_protected"],
        )

    if len(doc) > MAX_PDF_PAGES:
        page_count = len(doc)
        doc.close()
        raise HTTPException(
            status_code=400,
            detail=STRINGS["pdf_too_many_pages"].format(page_count=page_count, max_pages=MAX_PDF_PAGES),
        )

    try:
        with metrics.DETECTION_DURATION_SECONDS.labels(format="pdf").time():
            detections = _detect_pdf(doc, theme=selected_theme)
        clusters = _cluster_detections(detections)

        page_sizes = []
        for page in doc:
            r = page.rect
            page_sizes.append((round(r.width * PREVIEW_ZOOM), round(r.height * PREVIEW_ZOOM)))
    except HTTPException:
        raise
    except Exception as exc:
        # PyMuPDF can succeed at opening a PDF (fitz.open raises nothing) but
        # fail later, during processing, on an invalid structure
        # discovered late (e.g. a cycle in an object's resources).
        # Without this safety net, the raw exception would propagate up to
        # Starlette and display its generic, unstyled error page — not
        # dangerous in itself, but an unnecessary information leak (internal
        # paths, library name) for a rejection that is, fundamentally,
        # just an "invalid PDF".
        log.warning("Échec du traitement PDF après ouverture réussie : %s", exc)
        raise HTTPException(
            status_code=400,
            detail=STRINGS["pdf_corrupt_structure"],
        ) from exc
    finally:
        doc.close()

    with _PENDING_JOBS_LOCK:
        PENDING_JOBS[job_id] = {
            "kind": "pdf",
            "raw_pdf": raw,
            "detections": detections,
            "clusters": {c["id"]: c["member_ids"] for c in clusters},
            "theme": theme,
            "filename_hash": filename_hash,
            "user_email": user_email,
            "size_mb": size_mb,
            "created_at": time.time(),
        }
        metrics.PENDING_JOBS.set(len(PENDING_JOBS))

    log.info(
        "Job %s en révision: fichier#%s, %d détection(s) (%d zone(s) après regroupement), thème=%s",
        job_id, filename_hash, len(detections), len(clusters), theme or "aucun",
    )

    pages_html = _build_page_containers_html(job_id, page_sizes, clusters)
    return _build_pixel_review_page(job_id, len(clusters), pages_html)


def _build_page_containers_html(job_id: str, page_sizes: list[tuple[int, int]], clusters: list[dict]) -> str:
    """Builds the pages' HTML with clickable overlays (one per cluster,
    not per raw detection — see _cluster_detections for why).
    Common to PDF (several pages) and image (a single "page" = the
    entire image) — see _build_pixel_review_page for the enclosing template."""
    pages_html = []
    for page_index, (width, height) in enumerate(page_sizes):
        overlays = "".join(
            f'<div class="detection" data-id="{c["id"]}" '
            f'data-group="{hashlib.md5(c["group_key"].encode()).hexdigest()[:12] if c.get("group_key") else ""}" '
            f'title="{", ".join(c["entity_types"])}" '
            f'style="left:{c["display_rect"][0]:.0f}px; top:{c["display_rect"][1]:.0f}px; '
            f'width:{c["display_rect"][2] - c["display_rect"][0]:.0f}px; '
            f'height:{c["display_rect"][3] - c["display_rect"][1]:.0f}px;" '
            f'onclick="toggleDetection(this)"></div>'
            for c in clusters if c["page"] == page_index
        )
        pages_html.append(f"""
        <div class="page-container" data-page="{page_index}" style="position:relative; width:{width}px; height:{height}px; margin-bottom:16px;">
          <img src="/api/preview_image/{job_id}/{page_index}" width="{width}" height="{height}" style="display:block;">
          {overlays}
        </div>
        """)
    return "".join(pages_html)


def _build_pixel_review_page(job_id: str, total_detections: int, pages_html: str) -> HTMLResponse:
    """Common PDF/image review template: image rendering + pixel
    clickable zones to exclude a detection, AND manual drawing of a new zone
    (manual_zones) — unlike the DOCX/CSV text template
    (_build_text_review_page), which does not offer this possibility."""
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="{UI_LANG}">
    <head>
      <meta charset="utf-8">
      <title>{STRINGS["review_page_title"]}</title>
      <style>
        .detection {{
          position: absolute;
          background: rgba(220, 40, 40, 0.35);
          border: 1px solid rgba(220, 40, 40, 0.9);
          cursor: pointer;
        }}
        .detection.excluded {{
          background: rgba(40, 170, 40, 0.15);
          border: 1px dashed rgba(40, 140, 40, 0.7);
        }}
        .manual-zone {{
          position: absolute;
          background: rgba(40, 80, 220, 0.35);
          border: 1px solid rgba(40, 80, 220, 0.9);
          cursor: pointer;
        }}
        .page-container.manual-mode {{ cursor: crosshair; }}
        .drag-preview {{
          position: absolute;
          border: 2px dashed rgba(40, 80, 220, 0.9);
          background: rgba(40, 80, 220, 0.15);
          pointer-events: none;
        }}
        .toolbar {{
          position: sticky; top: 0; background: white; padding: 12px 0;
          border-bottom: 1px solid #ddd; margin-bottom: 16px; z-index: 10;
        }}
      </style>
    </head>
    <body style="font-family: sans-serif; max-width: 1000px; margin: 0 auto; padding: 0 20px;">
      <div class="toolbar">
        <p><a href="/">&larr; {STRINGS["restart_link"]}</a></p>
        <p>
          {STRINGS["review_zone_count_text"].format(count=total_detections)}
        </p>
        <button type="button" id="manual-mode-btn" onclick="toggleManualMode()" style="
            padding:8px 16px; background:#e9ecef; border:1px solid #ccc;
            border-radius:4px; cursor:pointer; margin-bottom:8px;">
          {STRINGS["add_manual_zone_button"]}
        </button>
        <form id="finalize-form" action="/api/finalize" method="post">
          <input type="hidden" name="job_id" value="{job_id}">
          <input type="hidden" name="format" value="html">
          <input type="hidden" id="excluded_ids" name="excluded_ids" value="">
          <input type="hidden" id="manual_zones" name="manual_zones" value="[]">
          <button type="button" onclick="submitFinalize()" style="
              padding:10px 20px; background:#0d6efd; color:white; border:none;
              border-radius:4px; cursor:pointer; font-size:1em;">
            {STRINGS["confirm_and_redact_button"]}
          </button>
        </form>
      </div>

      {pages_html}

      <script>
        let manualModeActive = false;
        let manualZones = [];
        let dragState = null;

        function toggleManualMode() {{
          manualModeActive = !manualModeActive;
          document.querySelectorAll('.page-container').forEach(el =>
            el.classList.toggle('manual-mode', manualModeActive));
          document.getElementById('manual-mode-btn').textContent = manualModeActive
            ? {json.dumps(STRINGS["manual_mode_active_button"])}
            : {json.dumps(STRINGS["add_manual_zone_button"])};
        }}

        document.querySelectorAll('.page-container').forEach(container => {{
          const pageIndex = parseInt(container.dataset.page, 10);

          container.addEventListener('mousedown', (e) => {{
            if (!manualModeActive || e.target.closest('.manual-zone')) return;
            const rect = container.getBoundingClientRect();
            dragState = {{
              startX: e.clientX - rect.left,
              startY: e.clientY - rect.top,
              preview: document.createElement('div'),
            }};
            dragState.preview.className = 'drag-preview';
            container.appendChild(dragState.preview);
            e.preventDefault();
          }});

          container.addEventListener('mousemove', (e) => {{
            if (!dragState) return;
            const rect = container.getBoundingClientRect();
            const curX = e.clientX - rect.left, curY = e.clientY - rect.top;
            const x0 = Math.min(dragState.startX, curX), y0 = Math.min(dragState.startY, curY);
            Object.assign(dragState.preview.style, {{
              left: x0 + 'px', top: y0 + 'px',
              width: Math.abs(curX - dragState.startX) + 'px',
              height: Math.abs(curY - dragState.startY) + 'px',
            }});
          }});

          container.addEventListener('mouseup', (e) => {{
            if (!dragState) return;
            const rect = container.getBoundingClientRect();
            const curX = e.clientX - rect.left, curY = e.clientY - rect.top;
            const x0 = Math.min(dragState.startX, curX), y0 = Math.min(dragState.startY, curY);
            const x1 = Math.max(dragState.startX, curX), y1 = Math.max(dragState.startY, curY);
            dragState.preview.remove();

            if (x1 - x0 > 6 && y1 - y0 > 6) {{
              const id = 'manual-' + Date.now() + '-' + Math.floor(Math.random() * 1000);
              manualZones.push({{ id, page: pageIndex, x0, y0, x1, y1 }});
              const zoneEl = document.createElement('div');
              zoneEl.className = 'manual-zone';
              zoneEl.dataset.id = id;
              zoneEl.title = {json.dumps(STRINGS["manual_zone_tooltip"])};
              Object.assign(zoneEl.style, {{
                left: x0 + 'px', top: y0 + 'px',
                width: (x1 - x0) + 'px', height: (y1 - y0) + 'px',
              }});
              zoneEl.onclick = (ev) => {{
                ev.stopPropagation();
                manualZones = manualZones.filter(z => z.id !== id);
                zoneEl.remove();
              }};
              container.appendChild(zoneEl);
            }}
            dragState = null;
          }});
        }});

        function toggleDetection(el) {{
          el.classList.toggle('excluded');
          const group = el.dataset.group;
          if (!group) return;
          // The same person may be detected several times in the
          // document (see server-side propagation) — excluding one
          // occurrence excludes all others in the same group, to avoid
          // having to click every occurrence individually.
          const state = el.classList.contains('excluded');
          document.querySelectorAll(`.detection[data-group="${{group}}"]`).forEach(sibling => {{
            sibling.classList.toggle('excluded', state);
          }});
        }}

        function submitFinalize() {{
          const excluded = Array.from(document.querySelectorAll('.detection.excluded'))
                                 .map(el => el.dataset.id);
          document.getElementById('excluded_ids').value = excluded.join(',');
          document.getElementById('manual_zones').value = JSON.stringify(
            manualZones.map(({{page, x0, y0, x1, y1}}) => ({{page, rect: [x0, y0, x1, y1]}}))
          );
          document.getElementById('finalize-form').submit();
        }}
      </script>
    </body>
    </html>
    """)


def _handle_detect_image(raw, theme, selected_theme, job_id, filename_hash, user_email, size_mb):
    """
    Image detection: input validation (_open_and_validate_image), OCR +
    PII detection (_detect_image), then the same pixel review screen as
    the PDF (_build_pixel_review_page/_build_page_containers_html), adapted to a
    single image rather than multiple pages (a single "page", index 0).
    """
    img = _open_and_validate_image(raw)
    try:
        img.load()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=STRINGS["image_corrupt_truncated"],
        ) from exc

    width, height = img.size
    image_format = img.format

    try:
        with metrics.DETECTION_DURATION_SECONDS.labels(format="image").time():
            detections = _detect_image(img, theme=selected_theme)
        clusters = _cluster_detections(detections)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Échec du traitement image après ouverture réussie : %s", exc)
        raise HTTPException(
            status_code=400,
            detail=STRINGS["image_invalid_structure"],
        ) from exc

    with _PENDING_JOBS_LOCK:
        PENDING_JOBS[job_id] = {
            "kind": "image",
            "raw_image": raw,
            "image_format": image_format,
            "detections": detections,
            "clusters": {c["id"]: c["member_ids"] for c in clusters},
            "theme": theme,
            "filename_hash": filename_hash,
            "user_email": user_email,
            "size_mb": size_mb,
            "created_at": time.time(),
        }
        metrics.PENDING_JOBS.set(len(PENDING_JOBS))

    log.info(
        "Job %s en révision (image): fichier#%s, %d détection(s) (%d zone(s) après regroupement), thème=%s",
        job_id, filename_hash, len(detections), len(clusters), theme or "aucun",
    )

    pages_html = _build_page_containers_html(job_id, [(width, height)], clusters)
    return _build_pixel_review_page(job_id, len(clusters), pages_html)


def _handle_detect_docx(raw, theme, selected_theme, job_id, filename_hash, user_email, size_mb):
    try:
        document = WordDocument(io.BytesIO(raw))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=STRINGS["docx_unreadable"]) from exc

    try:
        blocks, _note_parts = _iter_docx_paragraphs(document)
        if len(blocks) > MAX_DOCX_PARAGRAPHS:
            raise HTTPException(
                status_code=400,
                detail=STRINGS["docx_too_many_paragraphs"].format(count=len(blocks), max_paragraphs=MAX_DOCX_PARAGRAPHS),
            )
        block_texts = ["".join(run.text for run in p.runs) for _, p, _ in blocks]
        indexed_texts = list(enumerate(block_texts))
        structural = _docx_table_structural_entities(blocks, block_texts, _resolve_column_keywords(selected_theme))
        with metrics.DETECTION_DURATION_SECONDS.labels(format="docx").time():
            detections = _detect_text_blocks(indexed_texts, theme=selected_theme, extra_detections=structural)
        clusters = _cluster_text_detections(detections)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Échec du traitement DOCX après ouverture réussie : %s", exc)
        raise HTTPException(
            status_code=400,
            detail=STRINGS["docx_corrupt_structure"],
        ) from exc

    with _PENDING_JOBS_LOCK:
        PENDING_JOBS[job_id] = {
            "kind": "docx",
            "raw_docx": raw,
            "detections": detections,
            "clusters": {c["id"]: c["member_ids"] for c in clusters},
            "theme": theme,
            "filename_hash": filename_hash,
            "user_email": user_email,
            "size_mb": size_mb,
            "created_at": time.time(),
        }
        metrics.PENDING_JOBS.set(len(PENDING_JOBS))

    log.info(
        "Job %s en révision (DOCX): fichier#%s, %d détection(s) (%d zone(s) après regroupement), thème=%s",
        job_id, filename_hash, len(detections), len(clusters), theme or "aucun",
    )

    clusters_by_block: dict[int, list[dict]] = {}
    for c in clusters:
        clusters_by_block.setdefault(c["block_id"], []).append(c)

    blocks_html_parts = []
    rendered_count = 0
    truncated = False
    for block_id, (label, _, _table_ref) in enumerate(blocks):
        text = block_texts[block_id]
        if not text.strip():
            continue
        if rendered_count >= MAX_REVIEW_ROWS:
            truncated = True
            break
        rendered = _render_highlighted_text(text, clusters_by_block.get(block_id, []))
        label_text = STRINGS.get(f"docx_block_label_{label}", label)
        label_html = f'<span class="doc-block-label">{html.escape(label_text)}</span>' if label != "body" else ""
        blocks_html_parts.append(f'<div class="doc-block">{label_html}{rendered}</div>')
        rendered_count += 1

    limitation_note = (
        '<p style="color:#a15c00; font-size:0.85em; background:#fff8e6; padding:8px 12px; '
        f'border-radius:4px;">{STRINGS["docx_limitation_warning"]}</p>'
    )
    if truncated:
        limitation_note += (
            '<p style="color:#a15c00; font-size:0.85em; background:#fff8e6; padding:8px 12px; '
            f'border-radius:4px;">{STRINGS["docx_truncated_note"].format(max_rows=MAX_REVIEW_ROWS)}</p>'
        )

    images_html = _build_docx_images_review_section(document)

    return _build_text_review_page(
        job_id=job_id,
        total_detections=len(clusters),
        blocks_html="".join(blocks_html_parts),
        extra_note=limitation_note,
        images_html=images_html,
    )


def _handle_detect_csv(raw, theme, selected_theme, job_id, filename_hash, user_email, size_mb):
    text, _encoding = _decode_csv_bytes(raw)
    delimiter = _detect_csv_delimiter(text)
    rows = _parse_csv_rows(text, delimiter)

    total_cells = sum(len(r) for r in rows)
    if total_cells > MAX_CSV_CELLS:
        raise HTTPException(
            status_code=400,
            detail=STRINGS["csv_too_many_cells"].format(count=total_cells, max_cells=MAX_CSV_CELLS),
        )

    try:
        indexed_texts = [(f"{r}:{c}", cell) for r, row in enumerate(rows) for c, cell in enumerate(row)]
        structural = _csv_structural_entities(rows, _resolve_column_keywords(selected_theme))
        with metrics.DETECTION_DURATION_SECONDS.labels(format="csv").time():
            detections = _detect_text_blocks(indexed_texts, theme=selected_theme, extra_detections=structural)
        clusters = _cluster_text_detections(detections)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Échec du traitement CSV après lecture réussie : %s", exc)
        raise HTTPException(
            status_code=400,
            detail=STRINGS["csv_corrupt_structure"],
        ) from exc

    with _PENDING_JOBS_LOCK:
        PENDING_JOBS[job_id] = {
            "kind": "csv",
            "csv_text": text,
            "csv_delimiter": delimiter,
            "detections": detections,
            "clusters": {c["id"]: c["member_ids"] for c in clusters},
            "theme": theme,
            "filename_hash": filename_hash,
            "user_email": user_email,
            "size_mb": size_mb,
            "created_at": time.time(),
        }
        metrics.PENDING_JOBS.set(len(PENDING_JOBS))

    log.info(
        "Job %s en révision (CSV): fichier#%s, %d détection(s) (%d zone(s) après regroupement), thème=%s, délimiteur=%r",
        job_id, filename_hash, len(detections), len(clusters), theme or "aucun", delimiter,
    )

    clusters_by_block: dict[str, list[dict]] = {}
    for c in clusters:
        clusters_by_block.setdefault(c["block_id"], []).append(c)

    truncated = len(rows) > MAX_REVIEW_ROWS
    table_rows_html = []
    for row_idx, row in enumerate(rows[:MAX_REVIEW_ROWS]):
        cells_html = []
        for col_idx, cell in enumerate(row):
            rendered = _render_highlighted_text(cell, clusters_by_block.get(f"{row_idx}:{col_idx}", []))
            cells_html.append(f'<td style="border:1px solid #ddd; padding:4px 8px;">{rendered}</td>')
        table_rows_html.append(f"<tr>{''.join(cells_html)}</tr>")

    table_html = f'<table style="border-collapse:collapse; width:100%; font-size:0.9em;">{"".join(table_rows_html)}</table>'

    truncation_note = (
        (
            '<p style="color:#a15c00; font-size:0.85em; background:#fff8e6; padding:8px 12px; '
            f'border-radius:4px;">{STRINGS["csv_truncated_note"].format(max_rows=MAX_REVIEW_ROWS, total_rows=len(rows))}</p>'
        )
        if truncated else ""
    )

    return _build_text_review_page(
        job_id=job_id,
        total_detections=len(clusters),
        blocks_html=table_html,
        extra_note=truncation_note,
    )


def _request_user(request: Request) -> str:
    """Caller identity as guaranteed by oauth2-proxy via Traefik
    (`X-Auth-Request-Email`, replaced — never passed through as-is — by the
    forwardAuth). Same sanitization as at job creation, so that the
    comparison is exact."""
    return _strip_unicode_control_and_format_chars(
        request.headers.get("x-auth-request-email", "inconnu")
    )


def _get_pending_job_for(job_id: str, request: Request, pop: bool = False) -> dict:
    """Finds a job pending review **belonging to the caller**.

    Until now, knowing a `job_id` (uuid4, unpredictable, but present in
    Traefik's access logs and the browser's history) was enough to
    see the ORIGINAL document preview (before redaction) of any
    other user and finalize their job in their place. Audit decision 1.18
    (no ownership check on download) relied on
    "file already redacted" — an argument that doesn't apply here. A job belonging to another
    user is treated exactly like a non-existent job (404), without
    revealing its existence, and above all without removing it from the queue (`pop`
    only once ownership is confirmed) — otherwise a third party could
    destroy another user's job under review just by attempting this."""
    user = _request_user(request)
    with _PENDING_JOBS_LOCK:
        job = PENDING_JOBS.get(job_id)
        if job is None or job.get("user_email") != user:
            raise HTTPException(status_code=404, detail=STRINGS["job_not_found_expired"])
        if pop:
            PENDING_JOBS.pop(job_id, None)
            metrics.PENDING_JOBS.set(len(PENDING_JOBS))
    return job


@app.get("/api/preview_image/{job_id}/{page_index}")
def preview_image(job_id: str, page_index: int, request: Request):
    """Renders a page of the PDF (or the whole image, for an image job) that is
    pending review — only for the job's owner (preview of the
    ORIGINAL document, see _get_pending_job_for)."""
    job = _get_pending_job_for(job_id, request)

    if job.get("kind") == "image":
        if page_index != 0:
            raise HTTPException(status_code=404, detail=STRINGS["page_not_found"])
        # Original raw bytes (already validated in Phase 1 at detection
        # time): this is the pre-redaction PREVIEW, exactly like for PDF —
        # the actual redaction only happens at finalization.
        media_type = "image/png" if job["image_format"] == "PNG" else "image/jpeg"
        return Response(content=job["raw_image"], media_type=media_type)

    if job.get("kind") != "pdf":
        raise HTTPException(status_code=400, detail=STRINGS["image_preview_pdf_image_only"])

    doc = fitz.open(stream=job["raw_pdf"], filetype="pdf")
    try:
        if page_index < 0 or page_index >= len(doc):
            raise HTTPException(status_code=404, detail=STRINGS["page_not_found"])

        page = doc[page_index]
        _check_page_images_sane(page)

        matrix = fitz.Matrix(PREVIEW_ZOOM, PREVIEW_ZOOM)
        pix = page.get_pixmap(matrix=matrix)
        png_bytes = pix.tobytes("png")
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Échec du rendu de la page %s (job %s) : %s", page_index, job_id, exc)
        raise HTTPException(
            status_code=400,
            detail=STRINGS["page_preview_invalid_structure"],
        ) from exc
    finally:
        doc.close()

    return Response(content=png_bytes, media_type="image/png")


def _apply_selected_redactions(doc: fitz.Document, detections: list[dict], excluded_ids: set) -> dict:
    """
    Applies redaction only for detections whose id is
    NOT in excluded_ids (those the user unchecked during review).
    """
    summary: dict[str, int] = {}
    by_page: dict[int, list[dict]] = {}
    for d in detections:
        if d["id"] not in excluded_ids:
            by_page.setdefault(d["page"], []).append(d)

    for page_index, page_detections in by_page.items():
        page = doc[page_index]
        for d in page_detections:
            page.add_redact_annot(fitz.Rect(d["page_rect"]), fill=(0, 0, 0))
            summary[d["entity_type"]] = summary.get(d["entity_type"], 0) + 1
        page.apply_redactions()

    return summary


def _apply_manual_redactions(doc: fitz.Document, manual_zones: list[dict]) -> int:
    """
    Applies redaction on zones manually drawn by the user
    (false negatives fixed by hand). The received coordinates are in preview
    pixels (display_rect), converted back to PDF coordinates via PREVIEW_ZOOM,
    exactly like for automatic detections.
    """
    count = 0
    by_page: dict[int, list] = {}
    for zone in manual_zones[:200]:  # anti-abuse guardrail
        page_index = zone.get("page")
        rect = zone.get("rect")
        if not isinstance(page_index, int) or not rect or len(rect) != 4:
            continue
        by_page.setdefault(page_index, []).append(rect)

    for page_index, rects in by_page.items():
        if page_index < 0 or page_index >= len(doc):
            continue
        page = doc[page_index]
        page_bounds = page.rect
        for x0, y0, x1, y1 in rects:
            pdf_rect = fitz.Rect(
                x0 / PREVIEW_ZOOM, y0 / PREVIEW_ZOOM,
                x1 / PREVIEW_ZOOM, y1 / PREVIEW_ZOOM,
            )
            pdf_rect.intersect(page_bounds)
            if pdf_rect.is_empty:
                continue
            page.add_redact_annot(pdf_rect, fill=(0, 0, 0))
            count += 1
        page.apply_redactions()

    return count


def _wipe_pdf_metadata(doc: fitz.Document) -> None:
    """
    Clears metadata likely to carry an identity (author,
    creator/producer, creation/modification dates, title, subject,
    keywords) and the associated XMP packet. `apply_redactions()` only
    touches the visible content of pages; without this cleanup, the
    "anonymized" document remains dated and attributed like the original — same
    underlying point as for DOCX (_wipe_core_properties): a structured field whose
    nature is known by convention, no need to run it through NER.
    """
    doc.set_metadata({})
    if doc.xref_xml_metadata():
        doc.del_xml_metadata()


def _finalize_pdf_job(job: dict, job_id: str, excluded_set: set, manual_zones_data: list) -> tuple[dict, Path, int]:
    """PDF finalization logic."""
    doc = fitz.open(stream=job["raw_pdf"], filetype="pdf")
    try:
        summary = _apply_selected_redactions(doc, job["detections"], excluded_set)
        manual_count = _apply_manual_redactions(doc, manual_zones_data)
        if manual_count:
            summary["MANUEL"] = summary.get("MANUEL", 0) + manual_count

        _wipe_pdf_metadata(doc)

        theme = job["theme"]
        theme_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", theme) if theme else "document"
        output_path = WORKDIR / f"{job_id}-{theme_slug}-anonymise.pdf"
        # garbage=4 + clean=True: purges objects that became orphaned after
        # apply_redactions() (the old pre-redaction page content is
        # never removed from the file by default, only dereferenced —
        # see the session finding verified on caviar_test.pdf: original
        # text recoverable in cleartext in the "anonymized" file with any
        # tool that walks all PDF objects instead of following
        # only the current page tree).
        doc.save(output_path, garbage=4, clean=True, deflate=True)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Échec de la finalisation PDF (job %s) : %s", job_id, exc)
        raise HTTPException(
            status_code=400,
            detail=STRINGS["pdf_finalize_corrupt"],
        ) from exc
    finally:
        doc.close()
    return summary, output_path, manual_count


_IMAGE_MODES_KEPT_AS_IS = ("RGB", "RGBA", "L", "LA")


def _normalize_image_mode_for_editing(img: "Image.Image") -> "Image.Image":
    """
    Converts to a mode that is directly drawable/encodable before redaction
    (e.g. indexed palette "P", CMYK...) — the redaction rectangle's
    fill color depends on the mode (see _black_fill_for_mode), so
    we first normalize to a small set of known modes rather than
    handling every possible Pillow mode.
    """
    if img.mode == "P":
        return img.convert("RGBA" if "transparency" in img.info else "RGB")
    if img.mode not in _IMAGE_MODES_KEPT_AS_IS:
        return img.convert("RGB")
    return img


def _black_fill_for_mode(mode: str):
    if mode == "RGBA":
        return (0, 0, 0, 255)
    if mode == "LA":
        return (0, 0)
    if mode == "L":
        return 0
    return (0, 0, 0)  # RGB and any mode already normalized to RGB


def _apply_selected_image_redactions(
    draw: "ImageDraw.ImageDraw", mode: str, detections: list[dict], excluded_ids: set
) -> dict:
    """Draws an opaque solid rectangle directly into the pixels for
    each non-excluded detection — same guarantees as PDF redaction
    (redact_annot): the original pixels under the rectangle are overwritten,
    not merely covered by a layer."""
    summary: dict[str, int] = {}
    fill = _black_fill_for_mode(mode)
    for d in detections:
        if d["id"] in excluded_ids:
            continue
        x0, y0, x1, y1 = d["page_rect"]
        draw.rectangle([x0, y0, x1, y1], fill=fill)
        summary[d["entity_type"]] = summary.get(d["entity_type"], 0) + 1
    return summary


def _apply_manual_image_redactions(
    draw: "ImageDraw.ImageDraw", mode: str, size: tuple[int, int], manual_zones: list[dict]
) -> int:
    """Applies redaction on zones manually drawn by
    the user (false negatives fixed by hand) — same pixel
    coordinates as the preview (no zoom applied for image, unlike
    PDF), same MAX_MANUAL_ZONES anti-abuse guardrail as for PDF."""
    count = 0
    fill = _black_fill_for_mode(mode)
    width, height = size
    for zone in manual_zones[:MAX_MANUAL_ZONES]:
        page_index = zone.get("page")
        rect = zone.get("rect")
        if page_index != 0 or not rect or len(rect) != 4:
            continue
        x0, x1 = sorted((rect[0], rect[2]))
        y0, y1 = sorted((rect[1], rect[3]))
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(float(width), x1), min(float(height), y1)
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            continue
        draw.rectangle([x0, y0, x1, y1], fill=fill)
        count += 1
    return count


def _strip_image_metadata_and_encode(img: "Image.Image", output_format: str) -> bytes:
    """
    ENTIRELY strips metadata before saving: full EXIF (including
    GPS coordinates and embedded EXIF thumbnail), PNG text chunks
    (tEXt/zTXt/iTXt), and ICC profile.

    Deliberately radical approach rather than field-by-field removal
    (EXIF, then GPS, then thumbnail, then tEXt, then ICC...): rebuilding
    a brand-new image from the raw pixel bytes alone
    (Image.frombytes) produces an object whose `.info` dictionary is empty
    by construction — no risk of forgetting an existing metadata
    field, or one introduced by a future Pillow version, including the
    embedded EXIF thumbnail (carried in the `exif` bytes of `.info`,
    never copied here). `.save()` adds neither exif, nor
    icc_profile, nor pnginfo/comment by default unless explicitly passed.
    """
    clean = Image.frombytes(img.mode, img.size, img.tobytes())
    buffer = io.BytesIO()
    save_kwargs: dict = {}
    if output_format == "JPEG":
        if clean.mode not in ("RGB", "L"):
            clean = clean.convert("RGB")
        save_kwargs["quality"] = 95
    clean.save(buffer, format=output_format, **save_kwargs)
    return buffer.getvalue()


def _finalize_image_job(job: dict, job_id: str, excluded_set: set, manual_zones_data: list) -> tuple[dict, Path, int]:
    """Image finalization logic: reopens from the raw bytes
    stored in the job (never the Pillow object from the detection phase),
    actual redaction via solid rectangles in the pixels, then a full
    metadata strip before writing the output file."""
    try:
        img = Image.open(io.BytesIO(job["raw_image"]))
        img.load()
    except Exception as exc:
        log.warning("Échec de la finalisation image (job %s) : %s", job_id, exc)
        raise HTTPException(
            status_code=400,
            detail=STRINGS["image_finalize_corrupt"],
        ) from exc

    img = _normalize_image_mode_for_editing(img)

    try:
        draw = ImageDraw.Draw(img)
        summary = _apply_selected_image_redactions(draw, img.mode, job["detections"], excluded_set)
        manual_count = _apply_manual_image_redactions(draw, img.mode, img.size, manual_zones_data)
        if manual_count:
            summary["MANUEL"] = summary.get("MANUEL", 0) + manual_count

        output_format = job["image_format"]
        out_bytes = _strip_image_metadata_and_encode(img, output_format)

        theme = job["theme"]
        theme_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", theme) if theme else "document"
        extension = ".png" if output_format == "PNG" else ".jpg"
        output_path = WORKDIR / f"{job_id}-{theme_slug}-anonymise{extension}"
        output_path.write_bytes(out_bytes)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Échec de la finalisation image (job %s) : %s", job_id, exc)
        raise HTTPException(
            status_code=400,
            detail=STRINGS["image_finalize_corrupt"],
        ) from exc

    return summary, output_path, manual_count


def _finalize_docx_job(job: dict, job_id: str, excluded_set: set, redacted_image_ids: set) -> tuple[dict, Path, int]:
    non_excluded = [d for d in job["detections"] if d["id"] not in excluded_set]
    merged_by_block, summary = _apply_text_redactions(non_excluded)

    try:
        document = WordDocument(io.BytesIO(job["raw_docx"]))
        blocks, note_parts = _iter_docx_paragraphs(document)
        for block_id, intervals in merged_by_block.items():
            if 0 <= block_id < len(blocks):
                _, paragraph, _ = blocks[block_id]
                _apply_docx_paragraph_redactions(paragraph, intervals)

        # Structural zones removed entirely rather than redacted
        # (see _wipe_comments/_wipe_core_properties): only at
        # finalization, since they are neither analyzed nor presented on
        # the review screen.
        _wipe_comments(document)
        _wipe_core_properties(document)
        _wipe_docx_thumbnail(document)
        _save_note_parts(note_parts)

        image_count = _apply_docx_image_redactions(document, redacted_image_ids)
        if image_count:
            summary["IMAGE"] = summary.get("IMAGE", 0) + image_count

        theme = job["theme"]
        theme_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", theme) if theme else "document"
        output_path = WORKDIR / f"{job_id}-{theme_slug}-anonymise.docx"
        document.save(output_path)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Échec de la finalisation DOCX (job %s) : %s", job_id, exc)
        raise HTTPException(
            status_code=400,
            detail=STRINGS["docx_finalize_corrupt"],
        ) from exc
    return summary, output_path, image_count


def _finalize_csv_job(job: dict, job_id: str, excluded_set: set) -> tuple[dict, Path, int]:
    non_excluded = [d for d in job["detections"] if d["id"] not in excluded_set]
    merged_by_block, summary = _apply_text_redactions(non_excluded)

    try:
        rows = _parse_csv_rows(job["csv_text"], job["csv_delimiter"])
        for block_id, intervals in merged_by_block.items():
            row_idx, col_idx = (int(part) for part in block_id.split(":"))
            if row_idx < len(rows) and col_idx < len(rows[row_idx]):
                cell = rows[row_idx][col_idx]
                for start, end in sorted(intervals, reverse=True):
                    cell = cell[:start] + REDACTION_MARKER + cell[end:]
                rows[row_idx][col_idx] = cell

        theme = job["theme"]
        theme_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", theme) if theme else "document"
        output_path = WORKDIR / f"{job_id}-{theme_slug}-anonymise.csv"
        # utf-8-sig (BOM): French Excel correctly displays accented
        # characters without the user having to manually pick the encoding on import.
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=job["csv_delimiter"])
            for row in rows:
                writer.writerow([_neutralize_csv_formula(cell) for cell in row])
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Échec de la finalisation CSV (job %s) : %s", job_id, exc)
        raise HTTPException(
            status_code=400,
            detail=STRINGS["csv_finalize_corrupt"],
        ) from exc
    return summary, output_path, 0


@app.post("/api/finalize")
async def finalize_document(
    request: Request,
    job_id: str = Form(...),
    excluded_ids: str = Form(default=""),
    manual_zones: str = Form(default="[]"),
    redacted_image_ids: str = Form(default=""),
    response_format: str = Form(default="json", alias="format"),
):
    """
    Phase 2 of the review flow, common to all three formats: applies
    redaction only to the detections the user has not
    excluded (plus manual zones for PDF and whole images
    selected for DOCX — see _apply_manual_redactions /
    _apply_docx_image_redactions, not offered for CSV, a pure text format
    with no image container), produces the final file in its
    original format.
    """
    # Ownership checked BEFORE removing the job from the queue (see
    # _get_pending_job_for): a third party can neither finalize nor destroy
    # another user's job under review.
    job = _get_pending_job_for(job_id, request, pop=True)

    excluded_cluster_ids = {i for i in excluded_ids.split(",") if i}

    # excluded_ids received from the browser are CLUSTER ids (a
    # visible zone can group several overlapping detections) — they must be
    # expanded to all underlying detection ids before applying
    # redaction, otherwise an invisible detection left "hidden behind"
    # another one at the same spot would keep being redacted despite the click.
    excluded_set = set()
    for cluster_id in excluded_cluster_ids:
        excluded_set.update(job["clusters"].get(cluster_id, []))

    redacted_image_id_set = {i for i in redacted_image_ids.split(",") if i}

    try:
        manual_zones_data = json.loads(manual_zones)
        if not isinstance(manual_zones_data, list):
            manual_zones_data = []
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        # RecursionError: a deeply nested JSON array ("[[[[...")
        # of barely ~200 KB (so UNDER Starlette's multipart part size
        # limit, 1 MB) exceeds the `json` decoder's recursion
        # depth. Not covered by JSONDecodeError, it used to propagate
        # up to an uncontrolled generic 500 (leaking a Starlette
        # traceback). Treated as an ordinary malformed input:
        # no manual zone, the rest of finalization proceeds.
        manual_zones_data = []

    # These form fields do not go through the MAX_UPLOAD_MB check
    # (which only applies to the original file) — we explicitly cap
    # their size to prevent a buggy or malicious client from causing
    # disproportionate CPU/memory consumption during parsing/application.
    if len(manual_zones_data) > MAX_MANUAL_ZONES:
        raise HTTPException(
            status_code=400,
            detail=STRINGS["too_many_manual_zones"].format(count=len(manual_zones_data), max_zones=MAX_MANUAL_ZONES),
        )
    if len(excluded_cluster_ids) > MAX_EXCLUDED_IDS:
        raise HTTPException(
            status_code=400,
            detail=STRINGS["too_many_excluded_zones"].format(count=len(excluded_cluster_ids), max_ids=MAX_EXCLUDED_IDS),
        )
    if len(redacted_image_id_set) > MAX_DOCX_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=STRINGS["too_many_selected_images"].format(count=len(redacted_image_id_set), max_images=MAX_DOCX_IMAGES),
        )

    kind = job.get("kind", "pdf")
    theme = job["theme"]
    if kind == "pdf":
        summary, output_path, manual_count = _finalize_pdf_job(job, job_id, excluded_set, manual_zones_data)
    elif kind == "docx":
        summary, output_path, manual_count = _finalize_docx_job(job, job_id, excluded_set, redacted_image_id_set)
    elif kind == "csv":
        summary, output_path, manual_count = _finalize_csv_job(job, job_id, excluded_set)
    elif kind == "image":
        summary, output_path, manual_count = _finalize_image_job(job, job_id, excluded_set, manual_zones_data)
    else:  # pragma: no cover - defensive, should never happen
        raise HTTPException(status_code=400, detail=STRINGS["unknown_document_type"])

    total = sum(summary.values())
    excluded_count = len(excluded_cluster_ids)

    metrics.DOCUMENTS_PROCESSED.labels(format=kind).inc()
    for entity_type, count in summary.items():
        metrics.ENTITIES_REDACTED.labels(entity_type=entity_type).inc(count)

    log.info(
        "Job %s finalisé (%s): %d entité(s) caviardée(s), %d zone(s) exclue(s), %d zone(s) manuelle(s)",
        job_id, kind, total, excluded_count, manual_count,
    )

    _record_audit_event(
        job_id=job_id,
        format=kind,
        user=job["user_email"],
        theme=theme or "aucun",
        file_size_mb=round(job["size_mb"], 2),
        filename_hash=job["filename_hash"],
        entities_found=summary,
        total_redactions=total,
        manually_excluded=excluded_count,
        manually_added=manual_count,
    )

    _schedule_cleanup(output_path)

    download_url = f"/api/download/{job_id}"

    if response_format == "html":
        rows = "".join(
            f"<tr><td>{entity_type}</td><td style='text-align:right'>{count}</td></tr>"
            for entity_type, count in sorted(summary.items())
        ) or f"<tr><td colspan='2'>{STRINGS['no_entity_redacted']}</td></tr>"

        ttl_minutes = FILE_TTL_SECONDS // 60
        excluded_note = (
            f"<p style='color:#555;'>{STRINGS['excluded_zones_note'].format(count=excluded_count)}</p>"
            if excluded_count else ""
        )

        # Inline preview only works for PDF (native browser
        # rendering via iframe) and image (plain <img> tag) — a
        # .docx/.csv does not display correctly inline, so only
        # download is offered for these two formats.
        if kind == "pdf":
            preview_html = (
                f'<h2>{STRINGS["preview_visual_check_heading"]}</h2>'
                f'<iframe src="{download_url}" style="width:100%; height:900px; border:1px solid #ccc;"></iframe>'
            )
        elif kind == "image":
            preview_html = (
                f'<h2>{STRINGS["preview_visual_check_heading"]}</h2>'
                f'<img src="{download_url}" style="max-width:100%; border:1px solid #ccc;">'
            )
        else:
            preview_html = ""

        return HTMLResponse(f"""
        <!doctype html>
        <html lang="{UI_LANG}">
        <head><meta charset="utf-8"><title>{STRINGS["result_page_title"]}</title></head>
        <body style="font-family: sans-serif; max-width: 900px; margin: 40px auto;">
          <p><a href="/">&larr; {STRINGS["anonymize_another_doc_link"]}</a></p>
          <h1>{STRINGS["anonymized_document_heading"]}</h1>

          <p>
            {STRINGS["total_redacted_elements_text"].format(count=total)}
            <a href="{download_url}" download style="
                display:inline-block; margin-left:1em; padding:8px 16px;
                background:#0d6efd; color:white; text-decoration:none;
                border-radius:4px;">
              {STRINGS["download_anonymized_button"]}
            </a>
          </p>
          {excluded_note}

          <table style="border-collapse: collapse; margin-bottom: 24px;">
            <thead>
              <tr>
                <th style="text-align:left; border-bottom:1px solid #ccc; padding:4px 12px 4px 0;">{STRINGS["table_header_data_type"]}</th>
                <th style="text-align:right; border-bottom:1px solid #ccc; padding:4px 0 4px 12px;">{STRINGS["table_header_occurrences"]}</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>

          <p style="color:#555; font-size:0.9em;">
            {STRINGS["file_auto_deleted_note"].format(minutes=ttl_minutes)}
          </p>
          {preview_html}
        </body>
        </html>
        """)

    return JSONResponse(
        {
            "job_id": job_id,
            "format": kind,
            "entities_found": summary,
            "total_redactions": total,
            "manually_excluded": excluded_count,
            "manually_added": manual_count,
            "download_url": download_url,
        }
    )


_DOWNLOAD_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
}

_INLINE_EXTENSIONS = {".pdf", ".png", ".jpg"}


@app.get("/api/download/{job_id}")
def download(job_id: str):
    # job_id is a uuid4().hex (hexadecimal characters only), so
    # safe to use in a glob pattern without risk of path injection.
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=400, detail=STRINGS["invalid_job_id"])

    matches = list(WORKDIR.glob(f"{job_id}-*-anonymise.*"))
    if not matches:
        raise HTTPException(status_code=404, detail=STRINGS["file_not_found_purged"])
    path = matches[0]

    extension = path.suffix.lower()
    media_type = _DOWNLOAD_MEDIA_TYPES.get(extension)
    if media_type is None:  # pragma: no cover - defensive, extension is always known in practice
        raise HTTPException(status_code=500, detail=STRINGS["output_format_unrecognized"])

    # Rebuilds an explicit download name (document type +
    # short reference) without ever exposing the original file name.
    theme_slug = path.name[len(job_id) + 1 : -len(f"-anonymise{extension}")]
    public_filename = f"caviarde_{theme_slug}_{job_id[:8]}{extension}"

    is_inline = extension in _INLINE_EXTENSIONS
    return FileResponse(
        path,
        media_type=media_type,
        filename=public_filename,
        content_disposition_type="inline" if is_inline else "attachment",
        headers=_INLINE_PREVIEW_HEADERS if is_inline else None,
    )


@app.get("/api/audit")
def read_audit_log(n: int = 50):
    """
    Audit log consultation (the last n entries, 50 by default).
    Never contains document content or a cleartext file name —
    only who, when, which theme, how many elements redacted.
    """
    n = max(1, min(n, 500))  # avoids negative values (inconsistent slice) and excessive requests

    log_path = AUDIT_DIR / "audit.log"
    if not log_path.exists():
        return JSONResponse({"entries": []})

    try:
        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except OSError as exc:
        log.error("Lecture du journal d'audit impossible : %s", exc)
        raise HTTPException(status_code=500, detail=STRINGS["audit_log_unavailable"]) from exc


    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return JSONResponse({"entries": entries})
