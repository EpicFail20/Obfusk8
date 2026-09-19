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
Tests for the metrics.py module — run and successfully verified via an
external venv where prometheus_client was already installed (always
missing, no network access to install it, in the main development
environment).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from metrics import (  # noqa: E402
    AV_SCAN_RESULT,
    DETECTION_DURATION_SECONDS,
    DISK_FREE_BYTES,
    DOCUMENTS_PROCESSED,
    DOCUMENTS_REJECTED,
    ENTITIES_REDACTED,
    PENDING_JOBS,
    PRESIDIO_UP,
    metrics_response,
)


def test_metrics_response_contient_les_metriques_declarees():
    DOCUMENTS_PROCESSED.labels(format="pdf").inc()
    body, content_type = metrics_response()
    text = body.decode("utf-8")

    assert "anonymiseur_documents_processed_total" in text
    assert content_type.startswith("text/plain")


def test_compteur_documents_traites_par_format():
    before = DOCUMENTS_PROCESSED.labels(format="docx")._value.get()
    DOCUMENTS_PROCESSED.labels(format="docx").inc()
    after = DOCUMENTS_PROCESSED.labels(format="docx")._value.get()
    assert after == before + 1


def test_compteur_rejets_par_raison():
    before = DOCUMENTS_REJECTED.labels(reason="menace_antivirus")._value.get()
    DOCUMENTS_REJECTED.labels(reason="menace_antivirus").inc()
    after = DOCUMENTS_REJECTED.labels(reason="menace_antivirus")._value.get()
    assert after == before + 1


def test_jauge_pending_jobs_peut_etre_mise_a_jour():
    PENDING_JOBS.set(3)
    assert PENDING_JOBS._value.get() == 3
    PENDING_JOBS.set(0)
    assert PENDING_JOBS._value.get() == 0


def test_histogramme_duree_detection():
    with DETECTION_DURATION_SECONDS.labels(format="pdf").time():
        pass  # just verify that the context manager does not raise
    body, _ = metrics_response()
    assert b"anonymiseur_detection_duration_seconds" in body


def test_jauge_presidio_up_binaire():
    PRESIDIO_UP.labels(service="analyzer").set(1)
    assert PRESIDIO_UP.labels(service="analyzer")._value.get() == 1
    PRESIDIO_UP.labels(service="analyzer").set(0)
    assert PRESIDIO_UP.labels(service="analyzer")._value.get() == 0


def test_compteur_scan_antivirus_par_verdict():
    before = AV_SCAN_RESULT.labels(verdict="menace")._value.get()
    AV_SCAN_RESULT.labels(verdict="menace").inc()
    after = AV_SCAN_RESULT.labels(verdict="menace")._value.get()
    assert after == before + 1


def test_jauge_espace_disque_par_volume():
    DISK_FREE_BYTES.labels(volume="workdir").set(1_000_000_000)
    assert DISK_FREE_BYTES.labels(volume="workdir")._value.get() == 1_000_000_000
