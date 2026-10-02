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
Request/response models of the text API (browser extension, phase 1) —
see docs/api-extension.en.md for the contract and docs/DECISIONS.md for
the reasoning behind each choice.

Deliberately absent from every request model (doctrine §0, CLAUDE.md §5):
no score threshold, no list of entity types to disable, no client-supplied
recognizer. `extra="forbid"` turns any such attempt into a 422 instead of
silently ignoring it, so a client can never believe it weakened detection.

The maximum text length is NOT a model constraint: exceeding it is a 413
(size), checked by the endpoint against MAX_TEXT_CHARS, not a 422.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Same bound and same character set as a theme file stem (app/themes/*.json):
# a legitimate theme key is a short identifier (medical/it/compta). Whether
# the key actually exists is checked by the endpoint against the loaded
# themes (unknown theme -> 422, never a silent fallback to "no theme").
ThemeKey = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")]

# Major version in the URL (/api/v1/...); minor bumped for additive,
# backward-compatible changes only (new optional response field).
API_VERSION: Literal["1.0"] = "1.0"

_STRICT_INPUT = ConfigDict(extra="forbid", strict=True, frozen=True)
_OUTPUT = ConfigDict(extra="forbid", frozen=True)


class TextRequest(BaseModel):
    """Body of POST /api/v1/text/analyze and /api/v1/text/pseudonymize."""

    model_config = _STRICT_INPUT

    # A lone UTF-16 surrogate (JSON "\ud800") is rejected by pydantic-core
    # itself: by the JSON parser (json_invalid -> 400), and by the string
    # constraints for any other input (string_unicode). Rejected, never
    # repaired: positions would no longer match what the analyzer receives.
    text: Annotated[str, StringConstraints(min_length=1)]
    theme: ThemeKey | None = None


class Entity(BaseModel):
    """A detected span. Offsets are given twice, on the string AS RECEIVED:
    in Unicode code points (`start`/`end`, Python indexing) and in UTF-16
    code units (`start_utf16`/`end_utf16`, JavaScript indexing). No score:
    Presidio scores are not calibrated and must not be used to filter
    (doctrine §0.2)."""

    model_config = _OUTPUT

    entity_type: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    start_utf16: int = Field(ge=0)
    end_utf16: int = Field(ge=0)


class AnalyzeResponse(BaseModel):
    model_config = _OUTPUT

    request_id: str
    theme: str | None
    text_length: int = Field(ge=0)
    text_length_utf16: int = Field(ge=0)
    entities: list[Entity]


class MappingEntry(BaseModel):
    """One placeholder and the original value it replaces. The client
    restores the text from this list; the server keeps nothing."""

    model_config = _OUTPUT

    placeholder: str
    entity_type: str
    original: str


class PseudonymizeResponse(BaseModel):
    model_config = _OUTPUT

    request_id: str
    theme: str | None
    text: str
    mapping: list[MappingEntry]


class ThemeInfo(BaseModel):
    model_config = _OUTPUT

    key: str
    label: str


class VersionResponse(BaseModel):
    model_config = _OUTPUT

    api_version: Literal["1.0"]
    detection_config: str
    analyzer_language: str
    # D-013: Presidio's REST API exposes no version; fingerprint of the
    # recognizers actually loaded by the analyzer instead (null if unreachable).
    analyzer_recognizers: str | None
    presidio_version: str | None
    themes: list[ThemeInfo]
    max_text_chars: int = Field(gt=0)


class ErrorResponse(BaseModel):
    """Same `detail` field as every existing error of the application, plus
    the correlation identifier also sent in the X-Request-ID header. Never
    any echo of the submitted text, never a trace or a path."""

    model_config = _OUTPUT

    detail: str
    request_id: str
