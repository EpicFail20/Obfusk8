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
Regression tests for audit point 3.5 ("injection into the audit log"):
cover both the write mechanism (`_record_audit_event`) and the
untrusted entry point that feeds its `user` field (the
`X-Auth-Request-Email` HTTP header, see 3.7).

Two layers tested separately so a regression on one is not masked if
the other compensates for it:
- `_record_audit_event`: the JSON format (`json.dumps`) alone already
  prevents forging a fake line (newline, quotes, backslashes) —
  verified here without relying on upstream sanitization.
- `_strip_unicode_control_and_format_chars`: neutralizes what JSON
  alone does not cover (valid Unicode formatting characters such as the
  RTL override U+202E, which allow visual spoofing without ever
  breaking the file format).
"""
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402


@pytest.fixture
def redirected_audit_log(tmp_path, monkeypatch):
    """Redirects the real audit log to a disposable file for the duration
    of the test — must never write into /data/audit (production audit.log)."""
    log_path = tmp_path / "audit.log"
    handler = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))

    original_handlers = list(main.audit_log.handlers)
    for h in original_handlers:
        main.audit_log.removeHandler(h)
    main.audit_log.addHandler(handler)

    yield log_path

    main.audit_log.removeHandler(handler)
    handler.close()
    for h in original_handlers:
        main.audit_log.addHandler(h)


def _lines(log_path: Path) -> list[str]:
    return [l for l in log_path.read_bytes().split(b"\n") if l]


# ---------------------------------------------------------------------------
# Layer 1: the JSON format of _record_audit_event resists injection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("malicious_user", [
    'attacker@x.com\n{"job_id": "FORGED", "user": "admin@x.com", "total_redactions": 999999}',
    'attacker@x.com\r\n{"job_id": "FORGED2", "user": "root"}',
    'x", "total_redactions": 999, "user": "pwned',
    'x\\", "user": "pwned',
    "\x1b[31;1mFAKE CRITICAL ALERT\x1b[0m attacker@x.com",
    "attacker@x.com\x00hidden-after-null",
])
def test_record_audit_event_ne_permet_pas_de_forger_une_fausse_ligne(redirected_audit_log, malicious_user):
    main._record_audit_event(
        job_id="realjob123", format="pdf", user=malicious_user, theme="aucun",
        file_size_mb=1.0, filename_hash="abc123", entities_found={"PERSON": 1},
        total_redactions=1, manually_excluded=0, manually_added=0,
    )

    lines = _lines(redirected_audit_log)
    assert len(lines) == 1, "a single call must produce a single line, never an extra forged line"

    entry = json.loads(lines[0])  # raises if the line is not valid JSON
    assert entry["user"] == malicious_user, "exact round-trip: the payload must be neither truncated nor altered"
    assert entry["job_id"] == "realjob123", "a neighboring field must never be overwritten by the payload"


def test_record_audit_event_valeur_tres_longue_reste_une_seule_ligne(redirected_audit_log):
    main._record_audit_event(job_id="j", format="pdf", user="a" * 200_000, theme="aucun",
                              file_size_mb=1.0, filename_hash="abc", entities_found={},
                              total_redactions=0, manually_excluded=0, manually_added=0)
    assert len(_lines(redirected_audit_log)) == 1


# ---------------------------------------------------------------------------
# Layer 2: neutralization of Unicode formatting characters (RTL override
# and the like) — what JSON alone does not cover
# ---------------------------------------------------------------------------

def test_strip_unicode_neutralise_rtl_override():
    payload = "attacker@x.com‮مصمم.gpj"
    cleaned = main._strip_unicode_control_and_format_chars(payload)
    assert "‮" not in cleaned


@pytest.mark.parametrize("char", [
    "‮",  # RTL override
    "‭",  # LRO
    "⁦", "⁧", "⁨", "⁩",  # directional isolates
    "\x00",  # NUL
    "\x1b",  # ESC
    "\n", "\r",
])
def test_strip_unicode_retire_chaque_caractere_dangereux(char):
    cleaned = main._strip_unicode_control_and_format_chars(f"avant{char}apres")
    assert char not in cleaned
    assert cleaned == "avantapres"


@pytest.mark.parametrize("legit", [
    "jean.dupont@hopital.fr",
    "François Müller <f.muller@ex.com>",
    "utilisateur+tag@domaine.io",
    "inconnu",
])
def test_strip_unicode_preserve_les_valeurs_legitimes(legit):
    assert main._strip_unicode_control_and_format_chars(legit) == legit


def test_bout_en_bout_email_malveillant_neutralise_avant_le_journal(redirected_audit_log):
    """Reproduces the real path: header -> _strip_unicode_control_and_format_chars
    (detect_document) -> job -> _record_audit_event. The RTL override must not
    survive any step."""
    raw_header_value = "attacker@x.com‮مصمم.gpj"
    sanitized = main._strip_unicode_control_and_format_chars(raw_header_value)

    main._record_audit_event(job_id="j2", format="pdf", user=sanitized, theme="aucun",
                              file_size_mb=1.0, filename_hash="abc", entities_found={},
                              total_redactions=0, manually_excluded=0, manually_added=0)

    raw_bytes = redirected_audit_log.read_bytes()
    assert "‮".encode() not in raw_bytes
