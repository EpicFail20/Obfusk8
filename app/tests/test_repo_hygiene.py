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
EXT-39 (phase 2 bis): no invisible format character (Unicode category Cf:
bidirectional controls such as U+202E, zero-width characters, soft hyphen,
word joiner...) written literally in a text file of the repository. Such a
character makes code or a test read differently from what it does
("Trojan Source", CVE-2021-42574) and survives copy-paste unseen. Tests
that need one write its escape sequence ("\\u202e").

Single exception: U+FEFF as the very first character of a file, the UTF-8
signature (BOM) that the CSV flow must accept (tests/fixtures/csv_fixture.csv).

The repository is mounted read-only by app/run-tests.sh (OBFUSK8_REPO).
"""

import os
import unicodedata
from pathlib import Path

# Not part of the code base, or never read as text by anyone.
_SKIPPED_DIRS = {".git", "__pycache__", ".ruff_cache", ".mypy_cache", ".pytest_cache", "secrets"}
_BOM = "\ufeff"


def _text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIPPED_DIRS]
        files.extend(Path(dirpath) / name for name in filenames if name != ".env")
    return files


def _invisible_characters(path: Path) -> list[tuple[int, int, str]]:
    """(line, column, code point) of every literal Cf character; [] for a
    binary file (NUL byte or not UTF-8)."""
    raw = path.read_bytes()
    if b"\0" in raw:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return []
    if text.startswith(_BOM):
        text = " " + text[1:]
    found: list[tuple[int, int, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for column, char in enumerate(line, 1):
            if unicodedata.category(char) == "Cf":
                found.append((line_number, column, f"U+{ord(char):04X}"))
    return found


def test_aucun_caractere_invisible_litteral_dans_le_depot() -> None:
    root = Path(os.environ.get("OBFUSK8_REPO", ""))
    assert (root / "app" / "main.py").is_file(), "OBFUSK8_REPO must point to the repository (see app/run-tests.sh)"
    offenders = {
        str(path.relative_to(root)): hits for path in _text_files(root) if (hits := _invisible_characters(path))
    }
    assert offenders == {}


def test_detecteur_trouve_un_caractere_invisible(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text('ok = "a"\nbad = "x\u202ey"\n', encoding="utf-8")
    assert _invisible_characters(sample) == [(2, 9, "U+202E")]


def test_bom_tolere_seulement_en_tete(tmp_path: Path) -> None:
    head = tmp_path / "head.csv"
    head.write_text("\ufeffa,b\n", encoding="utf-8")
    inside = tmp_path / "inside.csv"
    inside.write_text("a,\ufeffb\n", encoding="utf-8")
    assert _invisible_characters(head) == []
    assert _invisible_characters(inside) == [(1, 3, "U+FEFF")]
