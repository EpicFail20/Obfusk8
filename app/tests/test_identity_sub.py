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
D-057 point 9: one identity, the OIDC `sub`, everywhere.

oauth2-proxy sets X-Auth-Request-User to the `sub` claim for a cookie
session (observed on the lab stack on 2026-10-12) and for a bearer token
of an extra issuer (source code of v7.15.5). The e-mail is optional in a
token and may even be replaced by the `sub`: it is no longer an identity.
The `sub` keys the audit, the pending-document quota, the ownership of a
document (preview, finalize, cancel) and, at the edge, the rate limit.
"""

import json
from typing import Any

import pytest

import main
from tests.doc_headers import TEST_ORIGIN
from tests.test_document_availability import (
    CANCEL_HEADERS,
    _cancel,
    _detect,
    _empty_queue,  # noqa: F401 (autouse fixture, applied here too)
    _job_id,
    _req,
    _run,
    _status,
)

SUB_A = "0a0a0a0a-1111-4111-8111-aaaaaaaaaaaa"
SUB_B = "0b0b0b0b-2222-4222-8222-bbbbbbbbbbbb"
EMAIL = "agent.partage@exemple.invalid"
OTHER_EMAIL = "autre.adresse@exemple.invalid"


def _headers(sub: str | None, email: str | None = None) -> dict[str, str]:
    headers = {"origin": TEST_ORIGIN}
    if sub is not None:
        headers["x-auth-request-user"] = sub
    if email is not None:
        headers["x-auth-request-email"] = email
    return headers


def test_flux_documents_courriel_seul_n_est_pas_une_identite() -> None:
    """A request carrying only an e-mail has no identity: 403, like EXT-47."""
    assert _status(lambda: _detect(_headers(None, EMAIL))) == (403, main.STRINGS["identity_missing"], {})


def test_flux_documents_courriel_absent_accepte() -> None:
    """The e-mail is not required (D-057 point 9): the sub alone suffices."""
    assert _detect(_headers(SUB_A)).status_code == 200


def test_quota_compte_par_sub_et_non_par_courriel() -> None:
    """Same sub with a changing e-mail: one quota. Another sub sharing the
    e-mail: its own quota (MAX_PENDING_JOBS_PER_USER is 3 in the fixture)."""
    for email in (EMAIL, OTHER_EMAIL, None):
        assert _detect(_headers(SUB_A, email)).status_code == 200
    status, _, headers = _status(lambda: _detect(_headers(SUB_A, "encore.une@exemple.invalid")))
    assert status == 429 and "Retry-After" in headers
    assert _detect(_headers(SUB_B, EMAIL)).status_code == 200


def test_propriete_par_sub_annulation_apercu_finalisation() -> None:
    """Ownership follows the sub: another sub with the owner's e-mail gets
    the generic 404 and the document survives; the owner's sub with another
    e-mail is the owner."""
    job_id = _job_id(_detect(_headers(SUB_A, EMAIL)))
    intruder = {**_headers(SUB_B, EMAIL), **CANCEL_HEADERS}
    expected = (404, main.STRINGS["job_not_found_expired"], {})
    assert _status(lambda: _cancel(job_id, intruder)) == expected
    assert _status(lambda: _run(main.finalize_document(_req(intruder), job_id=job_id))) == expected
    assert _status(lambda: _run(main.preview_image(job_id, 0, _req(intruder))))[0] == 404
    assert job_id in main.PENDING_JOBS

    response = _cancel(job_id, {**_headers(SUB_A, OTHER_EMAIL), **CANCEL_HEADERS})
    assert response.status_code == 200
    assert job_id not in main.PENDING_JOBS


def test_audit_des_documents_porte_le_sub_jamais_le_courriel(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(main, "_record_audit_event", lambda **fields: events.append(fields))
    job_id = _job_id(_detect(_headers(SUB_A, EMAIL)))
    response = _run(
        main.finalize_document(
            _req(_headers(SUB_A, EMAIL)),
            job_id=job_id,
            excluded_ids="",
            manual_zones="[]",
            redacted_image_ids="",
            response_format="json",
        )
    )
    assert response.status_code == 200
    assert events and all(event["user"] == SUB_A for event in events)
    assert EMAIL not in json.dumps(events, ensure_ascii=False)
    for out in main.WORKDIR.glob(f"{job_id}-*"):
        out.unlink(missing_ok=True)


def test_identite_du_journal_de_l_api_texte_est_le_sub() -> None:
    """The text API audits main._request_user (wired in main.py)."""
    assert main._request_user(_req(_headers(SUB_A, EMAIL))) == SUB_A
    assert main._request_user(_req(_headers(SUB_A))) == SUB_A


def test_identite_assainie_comme_avant() -> None:
    """Same sanitizing as the e-mail had (audit 3.5): a bidirectional
    control character never reaches a job or the audit log."""
    assert main._document_user(_req(_headers("\u202e" + SUB_A))) == SUB_A
