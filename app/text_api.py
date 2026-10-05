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
Text API for the browser extension (phase 1): /api/v1/version,
/api/v1/text/analyze, /api/v1/text/pseudonymize.

Contract: docs/api-extension.en.md. Reasoning: docs/DECISIONS.md (D-001 to
D-016). Disabled by default (ENABLE_EXTENSION_API=false): main.py then never
builds this router, the routes do not exist (404) and nothing here runs —
no audit file is even created.

Detection is NOT reimplemented: the router receives main.py's own
`_detect_text_blocks` (same themes, thresholds, normalization and name
propagation as the document flow) and only adds, for these routes, the
recognizers of app/themes/extension/ (secrets, identifiers missing from the
no-theme flow — D-015) and exact-value propagation to every type (D-012).

Stateless: nothing is written to disk except metadata in a dedicated audit
log (D-007); the text, detected values, placeholders and mapping never reach
a log, the audit log, an alert or a metric.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import traceback
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import metrics
from supervision import Alert, AlertSeverity
from text_api_models import (
    API_VERSION,
    AnalyzeResponse,
    Entity,
    ErrorResponse,
    MappingEntry,
    PseudonymizeResponse,
    TextRequest,
    ThemeInfo,
    VersionResponse,
)

log = logging.getLogger("anonymiseur.text_api")

PREFIX = "/api/v1"
AUDIT_LOGGER_NAME = "anonymiseur.audit.extension"
REQUEST_ID_HEADER = "X-Request-ID"

# Worst-case JSON size of one code point: a character outside the BMP escaped
# as two \uXXXX sequences (12 bytes), as written by a client escaping all
# non-ASCII (Python's json.dumps default). Raw UTF-8 is at most 4 bytes.
_JSON_BYTES_PER_CHAR = 12
# JSON envelope: braces, keys, quotes, whitespace, a theme of <= 64 chars.
_JSON_ENVELOPE_BYTES = 4096

# Exact-value propagation (D-012) ignores values shorter than this — same
# minimum as the document flow's NER detections (main._detect_text_blocks).
_MIN_PROPAGATED_CHARS = 3

# A Presidio outage on these routes alerts at most once per this interval:
# every failing prompt would otherwise send its own alert to the SIEM.
_ALERT_MIN_INTERVAL_SECONDS = 300

# Highest code point of the Basic Multilingual Plane: beyond it, a character
# takes two UTF-16 code units (a surrogate pair) in JavaScript strings.
_BMP_MAX = 0xFFFF
# Status of the HTTPException main._analyze_text raises on any Presidio failure.
_ANALYZER_FAILURE_STATUS = 502

# Timeout of the one-off GET /recognizers call behind /version (D-013).
_RECOGNIZERS_TIMEOUT_SECONDS = 5

# Placeholder delimiters (D-004): U+27E6 / U+27E7, practically absent from
# ordinary text and code.
_PLACEHOLDER_OPEN = "⟦"
_PLACEHOLDER_CLOSE = "⟧"
# A type that wins a tie between overlapping spans of equal length: a secret
# must never be labelled as something less sensitive.
_PRIORITY_TYPE = "SECRET"

# D-035 (phase 2 bis): both identity headers that Traefik's forwardAuth copies
# from oauth2-proxy (authResponseHeaders). X-Auth-Request-User keys the
# per-user rate limit at the edge, X-Auth-Request-Email is the identity the
# application audits (main._request_user, which falls back to "inconnu" when
# it is missing): a request lacking either one is refused, never served under
# a shared or empty identity.
_IDENTITY_HEADERS = ("x-auth-request-user", "x-auth-request-email")


class TextApiConfigError(RuntimeError):
    """Invalid configuration — raised at startup, never per request."""


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("true", "false"):
        return value == "true"
    raise TextApiConfigError(f"{name} must be 'true' or 'false'")


def _env_positive(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise TextApiConfigError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise TextApiConfigError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class TextApiSettings:
    """Environment configuration. Defaults and their measurements:
    docs/api-extension.en.md §4."""

    enabled: bool = False
    max_text_chars: int = 20000
    max_analysis_seconds: int = 10
    max_concurrency: int = 1
    max_queue: int = 8

    @property
    def max_body_bytes(self) -> int:
        """Derived, not separately configurable (D-006). Must equal the
        Traefik `text-bodylimit` label in docker-compose.yml."""
        return _JSON_BYTES_PER_CHAR * self.max_text_chars + _JSON_ENVELOPE_BYTES

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TextApiSettings:
        source = os.environ if env is None else env
        return cls(
            enabled=_env_bool(source, "ENABLE_EXTENSION_API", False),
            max_text_chars=_env_positive(source, "MAX_TEXT_CHARS", cls.max_text_chars),
            max_analysis_seconds=_env_positive(source, "MAX_TEXT_ANALYSIS_SECONDS", cls.max_analysis_seconds),
            max_concurrency=_env_positive(source, "MAX_TEXT_CONCURRENCY", cls.max_concurrency),
            max_queue=_env_positive(source, "MAX_TEXT_QUEUE", cls.max_queue),
        )


DetectBlocks = Callable[[list[tuple[int, str]], dict[str, Any] | None, float], list[dict[str, Any]]]


@dataclass(frozen=True)
class TextApiDeps:
    """What the router borrows from main.py — injected rather than imported,
    so this module has no import cycle and is testable on its own."""

    detect_blocks: DetectBlocks
    themes: Mapping[str, Mapping[str, Any]]
    theme_labels: Mapping[str, str]
    common_recognizers: Sequence[Mapping[str, Any]]
    strings: Mapping[str, str]
    send_alert: Callable[[Alert], None]
    request_user: Callable[[Request], str]
    audit_dir: Path
    extension_dir: Path
    analyzer_url: str
    analyzer_language: str
    default_score_threshold: float
    # Versions of everything outside the configuration that shapes the result
    # (phase 2 bis step G, D-030): normalization, the other detection steps of
    # the code, analyzer (Presidio, spaCy, model). See main.detection_versions.
    detection_versions: Mapping[str, Any]


def load_extension_recognizers(extension_dir: Path, themes: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Loads app/themes/extension/*.json: their `ad_hoc_recognizers`, plus
    the theme recognizers they reference by name in
    `include_theme_recognizers` ({theme: [recognizer name, ...]}) — reused,
    never copied, so a fix in the theme applies here too. Unlike the document
    themes (an unreadable theme is skipped), any problem here FAILS startup:
    a silently missing recognizer is a silent false negative."""
    recognizers: list[dict[str, Any]] = []
    for path in sorted(extension_dir.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise TextApiConfigError(f"unreadable extension recognizer file: {path.name}") from exc
        entries = data.get("ad_hoc_recognizers") if isinstance(data, dict) else None
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise TextApiConfigError(f"invalid extension recognizer file: {path.name}")
        recognizers.extend(included_theme_recognizers(data.get("include_theme_recognizers", {}), themes, path.name))
        recognizers.extend(entries)
    return recognizers


def included_theme_recognizers(
    includes: object, themes: Mapping[str, Mapping[str, Any]], filename: str
) -> list[dict[str, Any]]:
    """Copies of the theme recognizers named in `includes` ({theme: [name,
    ...]}). Also used by main.py for app/themes/common.json (phase 2, EXT-08):
    any unknown theme or name raises TextApiConfigError (startup fails)."""
    if not isinstance(includes, dict):
        raise TextApiConfigError(f"invalid include_theme_recognizers in {filename}")
    found: list[dict[str, Any]] = []
    for theme_key, names in includes.items():
        available = {r.get("name"): r for r in themes.get(theme_key, {}).get("ad_hoc_recognizers", [])}
        if not isinstance(names, list):
            raise TextApiConfigError(f"invalid include_theme_recognizers in {filename}")
        for name in names:
            if name not in available:
                raise TextApiConfigError(f"{filename}: recognizer {name!r} not found in theme {theme_key!r}")
            found.append(dict(available[name]))
    return found


def detection_config_fingerprint(deps: TextApiDeps, extension_recognizers: Sequence[Mapping[str, Any]]) -> str:
    """Fingerprint of everything that shapes detection on these routes,
    computed once at startup (see /version)."""
    canonical = json.dumps(
        {
            "api_version": API_VERSION,
            "themes": deps.themes,
            "common": deps.common_recognizers,
            "extension": extension_recognizers,
            "default_score_threshold": deps.default_score_threshold,
            "language": deps.analyzer_language,
            "versions": deps.detection_versions,
        },
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Pure text processing (run in a worker thread, never on the event loop)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Span:
    start: int
    end: int
    entity_type: str


def propagate_exact_values(text: str, spans: Sequence[Span]) -> list[Span]:
    """D-012: every other exact, whole-word occurrence of an already
    detected value is reported too, whatever its type — in a pseudonymized
    prompt, a missed repetition would leak in clear next to its placeholder.
    Word boundaries: "Paul" is not propagated into "Pauline"."""
    result = set(spans)
    first_type: dict[str, str] = {}
    for span in sorted(spans):
        value = text[span.start : span.end].strip()
        if len(value) >= _MIN_PROPAGATED_CHARS:
            first_type.setdefault(value, span.entity_type)
    covered = {(s.start, s.end) for s in result}
    for value, entity_type in first_type.items():
        for match in re.finditer(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text):
            if (match.start(), match.end()) not in covered:
                covered.add((match.start(), match.end()))
                result.add(Span(match.start(), match.end(), entity_type))
    return sorted(result)


def utf16_prefix(text: str) -> list[int]:
    """offsets[i] = UTF-16 code units before code point i (D-002)."""
    offsets = [0] * (len(text) + 1)
    units = 0
    for i, char in enumerate(text):
        offsets[i] = units
        units += 2 if ord(char) > _BMP_MAX else 1
    offsets[len(text)] = units
    return offsets


def _merge_overlaps(spans: Sequence[Span]) -> list[Span]:
    """Union of overlapping spans (D-004). Type: longest member, then
    SECRET, then alphabetical — deterministic."""
    merged: list[Span] = []
    group: list[Span] = []
    group_end = -1

    def close() -> None:
        if group:
            best = min(group, key=lambda s: (-(s.end - s.start), s.entity_type != _PRIORITY_TYPE, s.entity_type))
            merged.append(Span(min(s.start for s in group), group_end, best.entity_type))

    for span in sorted(spans):
        if span.start >= group_end:
            close()
            group = []
        group.append(span)
        group_end = max(group_end, span.end)
    close()
    return merged


def _placeholder_label(entity_type: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", entity_type.upper()) or "ENTITY"


def pseudonymize(text: str, spans: Sequence[Span]) -> tuple[str, list[MappingEntry]]:
    """Same exact value -> same placeholder; a placeholder already present in
    the received text is never used (D-004), so restoration is unambiguous."""
    by_value: dict[str, MappingEntry] = {}
    counters: Counter[str] = Counter()
    pieces: list[str] = []
    position = 0
    for span in _merge_overlaps(spans):
        original = text[span.start : span.end]
        entry = by_value.get(original)
        if entry is None:
            label = _placeholder_label(span.entity_type)
            while True:
                counters[label] += 1
                placeholder = f"{_PLACEHOLDER_OPEN}{label}_{counters[label]}{_PLACEHOLDER_CLOSE}"
                if placeholder not in text:
                    break
            entry = MappingEntry(placeholder=placeholder, entity_type=span.entity_type, original=original)
            by_value[original] = entry
        pieces.append(text[position : span.start])
        pieces.append(entry.placeholder)
        position = span.end
    pieces.append(text[position:])
    return "".join(pieces), list(by_value.values())


# ---------------------------------------------------------------------------
# Errors, admission control, audit
# ---------------------------------------------------------------------------


def has_identity(request: Request) -> bool:
    """True when every identity header carries something visible: control
    and format characters (category C) and white space do not count, so a
    value made only of them is as empty as a missing header."""
    for name in _IDENTITY_HEADERS:
        value = request.headers.get(name, "")
        if not "".join(c for c in value if not unicodedata.category(c).startswith("C")).strip():
            return False
    return True


class ApiError(Exception):
    """A client-facing error: closed `outcome` category, i18n key."""

    def __init__(
        self,
        status: int,
        outcome: str,
        message_key: str,
        headers: Mapping[str, str] | None = None,
        **message_args: object,
    ) -> None:
        super().__init__(outcome)
        self.status = status
        self.outcome = outcome
        self.message_key = message_key
        self.message_args = message_args
        self.headers = dict(headers or {})


class AnalysisGate:
    """At most `concurrency` analyses in progress and `queue` waiting
    (docs/api-extension.en.md §4). The analyzer has a single worker shared
    with the document flow: more text analyses in flight would only
    lengthen the wait of document batches. Only touched from the event
    loop (single-threaded), hence no lock on the counter."""

    def __init__(self, concurrency: int, queue: int) -> None:
        self.limiter = anyio.CapacityLimiter(concurrency)
        self.capacity = concurrency + queue
        self.pending = 0

    def admit(self) -> None:
        if self.pending >= self.capacity:
            raise ApiError(429, "busy", "text_api_busy", headers={"Retry-After": "1"})
        self.pending += 1

    def leave(self) -> None:
        self.pending -= 1


def _make_audit_logger(audit_dir: Path) -> logging.Logger:
    """Dedicated rotating audit file (D-007): prompt events must not evict
    the document history from audit.log. Same format and rotation."""
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    path = os.path.abspath(audit_dir / "audit-extension.log")
    already = any(isinstance(h, RotatingFileHandler) and h.baseFilename == path for h in logger.handlers)
    if not already:
        handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


@dataclass
class _Outcome:
    """Metadata of one request — the ONLY data that leaves the request."""

    route: str
    request_id: str
    user: str
    theme: str | None = None
    text_chars: int | None = None
    entities: Counter[str] = field(default_factory=Counter)
    outcome: str = "ok"
    status: int = 200


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class _TextApi:
    def __init__(self, settings: TextApiSettings, deps: TextApiDeps) -> None:
        self.settings = settings
        self.deps = deps
        self.extension_recognizers = load_extension_recognizers(deps.extension_dir, deps.themes)
        self.fingerprint = detection_config_fingerprint(deps, self.extension_recognizers)
        self.gate = AnalysisGate(settings.max_concurrency, settings.max_queue)
        self.thread_limiter = anyio.CapacityLimiter(settings.max_concurrency)
        self.audit = _make_audit_logger(deps.audit_dir)
        self.last_alert = float("-inf")
        self.recognizers_fingerprint: str | None = None
        log.info(
            "API texte activée : MAX_TEXT_CHARS=%d, plafond de corps=%d octets, délai=%ds, "
            "concurrence=%d, file=%d, %d reconnaisseur(s) propre(s), configuration=%s",
            settings.max_text_chars,
            settings.max_body_bytes,
            settings.max_analysis_seconds,
            settings.max_concurrency,
            settings.max_queue,
            len(self.extension_recognizers),
            self.fingerprint,
        )

    # --- helpers -----------------------------------------------------------

    def _message(self, key: str, **args: object) -> str:
        return self.deps.strings[key].format(**args)

    def _error_response(self, error: ApiError, request_id: str) -> JSONResponse:
        body = ErrorResponse(detail=self._message(error.message_key, **error.message_args), request_id=request_id)
        return JSONResponse(
            status_code=error.status,
            content=body.model_dump(),
            headers={REQUEST_ID_HEADER: request_id, **error.headers},
        )

    def _timeout_error(self) -> ApiError:
        return ApiError(
            503,
            "timeout",
            "text_api_timeout",
            headers={"Retry-After": "1"},
            max_seconds=self.settings.max_analysis_seconds,
        )

    def _effective_theme(self, theme: Mapping[str, Any] | None) -> dict[str, Any]:
        """The selected theme (or none) plus the extension recognizers. With
        no theme, the result carries no score_threshold/allow_list/excluded
        types, so main._analyze_text applies DEFAULT_SCORE_THRESHOLD exactly
        as for the document flow without a theme."""
        effective = dict(theme) if theme else {}
        effective["ad_hoc_recognizers"] = [*effective.get("ad_hoc_recognizers", []), *self.extension_recognizers]
        return effective

    async def _read_body(self, request: Request) -> bytes:
        """Reads at most max_body_bytes (D-006): a declared Content-Length
        above the cap is refused without reading a byte; otherwise reading
        stops as soon as the cap is exceeded. Raised from the route itself,
        not from `receive` (see EXT-22)."""
        cap = self.settings.max_body_bytes
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > cap:
            raise ApiError(413, "too_large", "text_api_body_too_large", max_bytes=cap)
        received = bytearray()
        async for chunk in request.stream():
            received += chunk
            if len(received) > cap:
                raise ApiError(413, "too_large", "text_api_body_too_large", max_bytes=cap)
        return bytes(received)

    @staticmethod
    def _check_media_type(request: Request) -> None:
        """application/json only (charset, if given, must be UTF-8): also the
        CSRF barrier — a cross-site form cannot send this type, and a
        cross-site fetch with it triggers a CORS preflight that is refused."""
        media_type, _, params = request.headers.get("content-type", "").partition(";")
        charset = ""
        for param in params.split(";"):
            name, _, value = param.partition("=")
            if name.strip().lower() == "charset":
                charset = value.strip().strip('"').lower()
        if media_type.strip().lower() != "application/json" or charset not in ("", "utf-8", "utf8"):
            raise ApiError(415, "invalid", "text_api_unsupported_media_type")

    async def _parse(self, request: Request, outcome: _Outcome) -> tuple[TextRequest, Mapping[str, Any] | None]:
        self._check_media_type(request)
        body = await self._read_body(request)
        try:
            body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ApiError(400, "invalid", "text_api_invalid_json") from exc
        try:
            payload = TextRequest.model_validate_json(body)
        except ValidationError as exc:
            # Never str(exc): pydantic's message embeds the offending input.
            if any(error["type"] == "json_invalid" for error in exc.errors(include_input=False)):
                raise ApiError(400, "invalid", "text_api_invalid_json") from None
            raise ApiError(422, "invalid", "text_api_invalid_request") from None
        outcome.text_chars = len(payload.text)
        if len(payload.text) > self.settings.max_text_chars:
            raise ApiError(413, "too_large", "text_api_text_too_long", max_chars=self.settings.max_text_chars)
        theme = None
        if payload.theme is not None:
            theme = self.deps.themes.get(payload.theme)
            if theme is None:
                raise ApiError(422, "invalid", "text_api_unknown_theme")
            outcome.theme = payload.theme
        return payload, theme

    def _detect_sync(self, text: str, theme: Mapping[str, Any] | None, timeout: float) -> list[Span]:
        """Runs in a worker thread: blocking HTTP call to Presidio and CPU
        work, never on the event loop (single worker)."""
        detections = self.deps.detect_blocks([(0, text)], self._effective_theme(theme), timeout)
        spans = [Span(int(d["start"]), int(d["end"]), str(d["entity_type"])) for d in detections]
        return propagate_exact_values(text, spans)

    def _alert_analyzer_down(self) -> None:
        now = time.monotonic()
        if now - self.last_alert >= _ALERT_MIN_INTERVAL_SECONDS:
            self.last_alert = now
            self.deps.send_alert(
                Alert(
                    severity=AlertSeverity.WARNING,
                    source="presidio",
                    message="Service presidio-analyzer injoignable depuis l'API texte",
                    details={"service": "analyzer", "flow": "text-api"},
                )
            )

    async def _detect(self, text: str, theme: Mapping[str, Any] | None) -> list[Span]:
        deadline = time.monotonic() + self.settings.max_analysis_seconds
        self.gate.admit()
        try:
            try:
                with anyio.fail_after(self.settings.max_analysis_seconds):
                    await self.gate.limiter.acquire()
            except TimeoutError:
                raise self._timeout_error() from None
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    raise self._timeout_error()
                return await anyio.to_thread.run_sync(
                    partial(self._detect_sync, text, theme, remaining), limiter=self.thread_limiter
                )
            except HTTPException as exc:
                # main._analyze_text maps any Presidio failure (unreachable,
                # HTTP error, timeout) to a 502 HTTPException; anything else
                # is unexpected here and ends as a generic 500.
                if exc.status_code != _ANALYZER_FAILURE_STATUS:
                    raise
                if time.monotonic() >= deadline:
                    # The HTTP call was given the remaining budget as its
                    # timeout and spent it: our own deadline, not an outage —
                    # no alert (observed under load, see the regression test).
                    raise self._timeout_error() from exc
                self._alert_analyzer_down()
                raise ApiError(
                    503, "analyzer_unavailable", "text_api_analyzer_unavailable", headers={"Retry-After": "5"}
                ) from exc
            finally:
                self.gate.limiter.release()
        finally:
            self.gate.leave()

    def _record(self, outcome: _Outcome, started: float) -> None:
        duration_ms = round((time.monotonic() - started) * 1000)
        total = sum(outcome.entities.values())
        metrics.TEXT_API_REQUESTS.labels(route=outcome.route, outcome=outcome.outcome).inc()
        metrics.TEXT_API_DURATION_SECONDS.labels(route=outcome.route).observe(duration_ms / 1000)
        log.info(
            "API texte %s %s : statut=%d, issue=%s, %s caractère(s), %d entité(s), %d ms",
            outcome.request_id,
            outcome.route,
            outcome.status,
            outcome.outcome,
            outcome.text_chars if outcome.text_chars is not None else "-",
            total,
            duration_ms,
        )
        event = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": f"text_{outcome.route}",
            "request_id": outcome.request_id,
            "user": outcome.user,
            "theme": outcome.theme or "aucun",
            "text_chars": outcome.text_chars,
            "entities_found": dict(sorted(outcome.entities.items())),
            "total_entities": total,
            "duration_ms": duration_ms,
            "outcome": outcome.outcome,
        }
        self.audit.info(json.dumps(event, ensure_ascii=False))

    async def _handle(
        self, request: Request, route: str, build: Callable[[str, TextRequest, list[Span]], dict[str, Any]]
    ) -> JSONResponse:
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        outcome = _Outcome(route=route, request_id=request_id, user=self.deps.request_user(request))
        try:
            if not has_identity(request):
                raise ApiError(403, "forbidden", "text_api_identity_missing")
            payload, theme = await self._parse(request, outcome)
            spans = await self._detect(payload.text, theme)
            outcome.entities = Counter(span.entity_type for span in spans)
            content = await anyio.to_thread.run_sync(
                partial(build, request_id, payload, spans), limiter=self.thread_limiter
            )
            return JSONResponse(content=content, headers={REQUEST_ID_HEADER: request_id})
        except ApiError as error:
            outcome.outcome, outcome.status = error.outcome, error.status
            return self._error_response(error, request_id)
        except Exception as exc:  # noqa: BLE001 - last resort: logged, answered as a generic 500, never swallowed
            # Frames only, never the exception message: it may quote input.
            outcome.outcome, outcome.status = "error", 500
            log.error(
                "API texte %s %s : erreur inattendue %s\n%s",
                request_id,
                route,
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            return self._error_response(ApiError(500, "error", "text_api_internal_error"), request_id)
        finally:
            self._record(outcome, started)

    # --- response builders (worker thread) ------------------------------------

    @staticmethod
    def _build_analyze(request_id: str, payload: TextRequest, spans: list[Span]) -> dict[str, Any]:
        utf16 = utf16_prefix(payload.text)
        entities = [
            Entity(
                entity_type=s.entity_type,
                start=s.start,
                end=s.end,
                start_utf16=utf16[s.start],
                end_utf16=utf16[s.end],
            )
            for s in spans
        ]
        return AnalyzeResponse(
            request_id=request_id,
            theme=payload.theme,
            text_length=len(payload.text),
            text_length_utf16=utf16[-1],
            entities=entities,
        ).model_dump()

    @staticmethod
    def _build_pseudonymize(request_id: str, payload: TextRequest, spans: list[Span]) -> dict[str, Any]:
        text, mapping = pseudonymize(payload.text, spans)
        return PseudonymizeResponse(request_id=request_id, theme=payload.theme, text=text, mapping=mapping).model_dump()

    # --- version ---------------------------------------------------------------

    def _fetch_recognizers_fingerprint(self) -> str | None:
        """D-013: fingerprint of the recognizers actually loaded by the
        analyzer, cached after the first success."""
        try:
            response = requests.get(
                f"{self.deps.analyzer_url}/recognizers",
                params={"language": self.deps.analyzer_language},
                timeout=_RECOGNIZERS_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            names = sorted(str(name) for name in response.json())
        except (requests.RequestException, ValueError, TypeError):
            log.warning("API texte : liste des reconnaisseurs de presidio-analyzer indisponible")
            return None
        return hashlib.sha256(json.dumps(names).encode("ascii")).hexdigest()[:16]

    async def version(self, request: Request) -> JSONResponse:
        request_id = uuid.uuid4().hex
        if not has_identity(request):
            return self._error_response(ApiError(403, "forbidden", "text_api_identity_missing"), request_id)
        if self.recognizers_fingerprint is None:
            self.recognizers_fingerprint = await anyio.to_thread.run_sync(self._fetch_recognizers_fingerprint)
        body = VersionResponse(
            api_version=API_VERSION,
            detection_config=self.fingerprint,
            analyzer_language=self.deps.analyzer_language,
            analyzer_recognizers=self.recognizers_fingerprint,
            presidio_version=None,
            themes=[ThemeInfo(key=key, label=self.deps.theme_labels.get(key, key)) for key in self.deps.themes],
            max_text_chars=self.settings.max_text_chars,
        )
        return JSONResponse(content=body.model_dump(), headers={REQUEST_ID_HEADER: request_id})

    async def analyze(self, request: Request) -> JSONResponse:
        return await self._handle(request, "analyze", self._build_analyze)

    async def pseudonymize(self, request: Request) -> JSONResponse:
        return await self._handle(request, "pseudonymize", self._build_pseudonymize)


def create_router(settings: TextApiSettings, deps: TextApiDeps) -> APIRouter:
    """Builds the v1 router. Only called by main.py when
    ENABLE_EXTENSION_API=true. include_in_schema=False: the request body is
    parsed by the route itself (D-009), the generated schema would be wrong;
    the reference is docs/api-extension.en.md."""
    api = _TextApi(settings, deps)
    router = APIRouter(prefix=PREFIX, include_in_schema=False)
    router.add_api_route("/version", api.version, methods=["GET"])
    router.add_api_route("/text/analyze", api.analyze, methods=["POST"])
    router.add_api_route("/text/pseudonymize", api.pseudonymize, methods=["POST"])
    return router
