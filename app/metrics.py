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
metrics.py — Prometheus metrics for the application.

Exposed via /metrics (see main.py), Prometheus exposition format —
universal standard for observability, directly scrapable by Prometheus or
via an agent/integration for virtually all enterprise monitoring tools
(Datadog, Splunk, Dynatrace...). Unlike the antivirus (ICAP) or alerts
(syslog), no adapter system is needed here: exposing this format is
enough to cover almost all cases.

IMPORTANT RULE, to be respected for any future addition: no metric
label must ever contain personal data, an original file name, a user
email, or any value with unbounded cardinality (a job identifier, a
file hash...) — only closed, predictable categories (file format,
detected entity type, error category...). A metric is not a log: it is
aggregated, not meant to carry per-request detail. A label with
unbounded cardinality would also degrade the performance of the metrics
server itself (each distinct value creates a new time series),
independently of the confidentiality risk.

Tested via test_metrics.py (external venv with prometheus_client already
installed, network always unavailable in the main development
environment) — all 14 tests across the two modules pass.
"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

DOCUMENTS_PROCESSED = Counter(
    "anonymiseur_documents_processed_total",
    "Number of documents successfully processed, by format",
    ["format"],  # "pdf", "docx", "csv", "image"
)

DOCUMENTS_REJECTED = Counter(
    "anonymiseur_documents_rejected_total",
    "Number of documents rejected before processing, by reason",
    ["reason"],  # "format_invalide", "trop_volumineux", "menace_antivirus", "antivirus_indisponible", ...
)

ENTITIES_REDACTED = Counter(
    "anonymiseur_entities_redacted_total",
    "Number of entities redacted, by type",
    ["entity_type"],  # "PERSON", "LOCATION", "DATE_TIME", ...
)

PENDING_JOBS = Gauge(
    "anonymiseur_pending_jobs",
    "Number of jobs currently pending review",
)

DETECTION_DURATION_SECONDS = Histogram(
    "anonymiseur_detection_duration_seconds",
    "Duration of detection processing, by format",
    ["format"],  # "pdf", "docx", "csv", "image"
)

PRESIDIO_UP = Gauge(
    "anonymiseur_presidio_up",
    "Availability of the Presidio service (1 = reachable, 0 = unreachable)",
    ["service"],  # "analyzer" or "anonymizer"
)

AV_SCAN_RESULT = Counter(
    "anonymiseur_av_scan_total",
    "Antivirus scan results, by verdict",
    ["verdict"],  # "propre", "menace", "indisponible"
)

DISK_FREE_BYTES = Gauge(
    "anonymiseur_disk_free_bytes",
    "Free disk space, by monitored volume",
    ["volume"],  # "workdir", "audit", "debug"
)


def metrics_response() -> tuple[bytes, str]:
    """Builds the body and content-type of the HTTP /metrics response."""
    return generate_latest(), CONTENT_TYPE_LATEST
