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
Unicode normalization applied before detection, with a map back to the
text received (decision D-014, phase 2).

Why: measured in phase 1 (benchmarks/results/quality-20261002T091416.md,
masking recall per variant), zero-width characters (0.646), non-breaking
spaces (0.789) and NFD forms (0.810) made the analyzer miss values it finds
in plain text (0.919). Measured in phase 2 step B: U+0000, which PyMuPDF
extracts for a glyph without a Unicode mapping, hid a name from the NER
(EXT-38); ligature glyphs (U+FB00-FB06) come out of word-processor PDFs.

What, character by character, then recomposition:
  - format characters (category Cf: zero-width, bidirectional controls,
    soft hyphen, word joiner, BOM): removed;
  - space separators other than U+0020 (category Zs: no-break, narrow
    no-break, thin, figure, ideographic...): one space;
  - control characters other than tab, line feed and carriage return
    (U+0000 included): one space;
  - line and paragraph separators (U+2028, U+2029): one line feed;
  - Latin ligatures U+FB00-FB06: expanded (NFKC of those only);
  - typographic apostrophes (U+2018, U+2019, U+02BC): ASCII apostrophe;
  - canonical recomposition (NFC), cluster by cluster (a base character and
    the combining marks that follow it).
The existing length-preserving normalizations of main.py (upper-case words,
typographic dashes) are applied afterwards, on the result.

Every character of the result knows the interval of the text received it
comes from, so an interval found on the result maps back exactly
(to_original). Characters removed INSIDE an interval are covered by it: a
value is never cut in two by an invisible character it contains.

Cost: one pass over the text, plus unicodedata.normalize per cluster only
when the text is not already NFC; a text with nothing to normalize takes the
identity path (one scan).
"""

import re
import unicodedata
from dataclasses import dataclass

_LINE_SEPARATORS = {"\u2028", "\u2029"}
_KEPT_CONTROLS = {"\t", "\n", "\r"}
_APOSTROPHES = {"\u2018", "\u2019", "\u02bc"}
_LIGATURES = {chr(code): unicodedata.normalize("NFKC", chr(code)) for code in range(0xFB00, 0xFB07)}
# Fast path: only plain printable ASCII and the three kept controls.
_NOTHING_TO_DO = re.compile(r"[\t\n\r\x20-\x7e]*")


@dataclass(frozen=True)
class NormalizedText:
    """`text`, the normalized text; `starts[i]`/`ends[i]`, the interval of
    the original text that normalized character `i` comes from. None for both
    when nothing changed (identity)."""

    text: str
    original_length: int
    starts: tuple[int, ...] | None = None
    ends: tuple[int, ...] | None = None

    def to_original(self, start: int, end: int) -> tuple[int, int]:
        """Interval of the original text covering the normalized interval
        [start, end): from the start of character `start` to the end of
        character `end - 1`, which includes any character removed between
        them. An empty interval maps to an empty one at the same place."""
        if not 0 <= start <= end <= len(self.text):
            raise ValueError("interval outside the normalized text")
        if self.starts is None or self.ends is None:
            return start, end
        if start == end:
            position = self.starts[start] if start < len(self.text) else self.original_length
            return position, position
        return self.starts[start], self.ends[end - 1]


def _replacement(char: str) -> str:
    """Normalized form of one character (empty string: removed)."""
    category = unicodedata.category(char)
    if category == "Cf":
        return ""
    if char in _LINE_SEPARATORS:
        return "\n"
    if (category == "Zs" and char != " ") or (category == "Cc" and char not in _KEPT_CONTROLS):
        return " "
    if char in _APOSTROPHES:
        return "'"
    return _LIGATURES.get(char, char)


def normalize_for_analysis(text: str) -> NormalizedText:
    if _NOTHING_TO_DO.fullmatch(text):
        return NormalizedText(text, len(text))

    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for index, char in enumerate(text):
        for out in _replacement(char):
            chars.append(out)
            starts.append(index)
            ends.append(index + 1)

    replaced = "".join(chars)
    if not unicodedata.is_normalized("NFC", replaced):
        replaced, starts, ends = _recompose(chars, starts, ends)
    return NormalizedText(replaced, len(text), tuple(starts), tuple(ends))


def _recompose(chars: list[str], starts: list[int], ends: list[int]) -> tuple[str, list[int], list[int]]:
    """NFC cluster by cluster: a cluster is a character followed by the
    combining marks after it; every character of its recomposed form maps to
    the whole cluster."""
    out: list[str] = []
    out_starts: list[int] = []
    out_ends: list[int] = []
    i, n = 0, len(chars)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(chars[j]):
            j += 1
        cluster = "".join(chars[i:j])
        composed = unicodedata.normalize("NFC", cluster)
        for out_char in composed:
            out.append(out_char)
            out_starts.append(starts[i])
            out_ends.append(ends[j - 1])
        i = j
    return "".join(out), out_starts, out_ends
