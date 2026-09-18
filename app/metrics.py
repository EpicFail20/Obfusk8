"""
metrics.py — métriques Prometheus de l'application.

Exposées via /metrics (voir main.py), format d'exposition Prometheus —
standard universel pour l'observabilité, scrapable directement par
Prometheus ou via un agent/intégration pour la quasi-totalité des outils
de supervision d'entreprise (Datadog, Splunk, Dynatrace...). Contrairement
à l'antivirus (ICAP) ou aux alertes (syslog), pas besoin d'un système
d'adaptateurs ici : exposer ce format suffit à couvrir la quasi-totalité
des cas.

RÈGLE IMPORTANTE, à respecter pour tout ajout futur : aucune étiquette
(label) de métrique ne doit jamais contenir de donnée personnelle, de nom
de fichier original, d'email utilisateur, ou toute valeur à cardinalité
non bornée (un identifiant de job, un hash de fichier...) — uniquement des
catégories fermées et prévisibles (format de fichier, type d'entité
détectée, catégorie d'erreur...). Une métrique n'est pas un journal :
elle est agrégée, pas faite pour porter du détail par requête. Une
étiquette à cardinalité non bornée dégraderait aussi les performances du
serveur de métriques lui-même (chaque valeur distincte crée une nouvelle
série temporelle), indépendamment du risque de confidentialité.

Testé via test_metrics.py (venv externe avec prometheus_client déjà
installé, réseau toujours indisponible dans l'environnement principal de
développement) — les 14 tests des deux modules passent.
"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

DOCUMENTS_PROCESSED = Counter(
    "anonymiseur_documents_processed_total",
    "Nombre de documents traités avec succès, par format",
    ["format"],  # "pdf", "docx", "csv", "image"
)

DOCUMENTS_REJECTED = Counter(
    "anonymiseur_documents_rejected_total",
    "Nombre de documents rejetés avant traitement, par raison",
    ["reason"],  # "format_invalide", "trop_volumineux", "menace_antivirus", "antivirus_indisponible", ...
)

ENTITIES_REDACTED = Counter(
    "anonymiseur_entities_redacted_total",
    "Nombre d'entités caviardées, par type",
    ["entity_type"],  # "PERSON", "LOCATION", "DATE_TIME", ...
)

PENDING_JOBS = Gauge(
    "anonymiseur_pending_jobs",
    "Nombre de jobs actuellement en attente de révision",
)

DETECTION_DURATION_SECONDS = Histogram(
    "anonymiseur_detection_duration_seconds",
    "Durée du traitement de détection, par format",
    ["format"],  # "pdf", "docx", "csv", "image"
)

PRESIDIO_UP = Gauge(
    "anonymiseur_presidio_up",
    "Disponibilité du service Presidio (1 = joignable, 0 = injoignable)",
    ["service"],  # "analyzer" ou "anonymizer"
)

AV_SCAN_RESULT = Counter(
    "anonymiseur_av_scan_total",
    "Résultats du scan antivirus, par verdict",
    ["verdict"],  # "propre", "menace", "indisponible"
)

DISK_FREE_BYTES = Gauge(
    "anonymiseur_disk_free_bytes",
    "Espace disque libre, par volume surveillé",
    ["volume"],  # "workdir", "audit", "debug"
)


def metrics_response() -> tuple[bytes, str]:
    """Construit le corps et le content-type de la réponse HTTP /metrics."""
    return generate_latest(), CONTENT_TYPE_LATEST
