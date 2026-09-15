"""
Anonymiseur de documents (PDF, DOCX, CSV, image) - application de test (lab Proxmox)
------------------------------------------------------------------------------
Flux commun à tous les formats : détection des données sensibles via
presidio-analyzer, révision humaine (exclusion de zones), puis caviardage
réel (le contenu original est supprimé de la structure du fichier, pas
juste masqué visuellement) :

  - PDF   : rendu image de chaque page + zones cliquables en pixels
            (redact_annot PyMuPDF : le texte sous-jacent est retiré).
  - DOCX  : surlignage inline du texte par paragraphe/cellule de tableau/
            en-tête-pied de page (python-docx) : le texte du run est remplacé
            dans le XML, pas juste habillé visuellement.
  - CSV   : tableau HTML avec cellules surlignées ; le texte de la cellule
            est remplacé au même titre que pour le DOCX.
  - Image : texte extrait par OCR (pytesseract) avec position par mot, même
            écran de révision pixel que le PDF (zones cliquables + tracé
            manuel), caviardage par rectangles opaques dessinés directement
            dans les pixels (Pillow), métadonnées entièrement dépouillées.

LIMITATION CONNUE (DOCX) : python-docx ne donne pas accès au texte contenu
dans des zones de texte, formes, SmartArt ou objets OLE incrustés — une
donnée sensible placée dans un de ces éléments échappe à la détection.
Documenté à l'utilisateur dans l'écran de révision, à couvrir explicitement
dans le futur audit de sécurité dédié à ces deux nouveaux formats.

Ce n'est pas un outil de production : pas de file d'attente, pas de retry,
gestion d'erreurs minimale. Suffisant pour valider le fonctionnel avant un
éventuel durcissement.
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

import pymupdf as fitz  # PyMuPDF — alias 'fitz' conservé, 'import fitz' est déprécié
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
# Longueur maximale retenue du champ de formulaire `theme` (voir
# detect_document) : un thème légitime est un identifiant court
# (medical/it/compta) ; borne défensive contre une valeur arbitrairement
# longue qui déborderait le nom de fichier de sortie ou le journal d'audit.
MAX_THEME_CHARS = int(os.environ.get("MAX_THEME_CHARS", "64"))
MAX_EXCLUDED_IDS = int(os.environ.get("MAX_EXCLUDED_IDS", "2000"))
# Nombre max d'images DOCX distinctes proposées au caviardage manuel — même
# esprit de garde-fou anti-abus que MAX_MANUAL_ZONES pour le PDF.
MAX_DOCX_IMAGES = int(os.environ.get("MAX_DOCX_IMAGES", "100"))
# Au-delà, pas d'aperçu intégré en base64 dans la page de révision (juste
# taille/format affichés) — reste sélectionnable pour caviardage, seul
# l'aperçu visuel est sauté, pour ne pas alourdir démesurément la page.
MAX_DOCX_IMAGE_PREVIEW_BYTES = int(os.environ.get("MAX_DOCX_IMAGE_PREVIEW_BYTES", str(500 * 1024)))
LANGUAGE = os.environ.get("ANALYZER_LANGUAGE", "fr")
# Seuil de confiance minimal appliqué par défaut quand le thème n'en définit
# pas un explicitement. Jusqu'ici, en l'absence de thème (ou avec un thème
# muet sur ce point), AUCUN filtrage n'avait lieu : Presidio renvoyait toutes
# ses hypothèses, y compris les plus incertaines — un mot capitalisé isolé
# en début de puce/titre, sans phrase autour, obtient typiquement un score
# de confiance NER plus faible qu'un vrai nom dans une phrase complète ; ce
# seuil filtre ces hypothèses faibles. Valeur de départ raisonnable, à
# ajuster (env var ou score_threshold par thème) selon le taux réel de
# faux positifs/négatifs observé.
DEFAULT_SCORE_THRESHOLD = float(os.environ.get("DEFAULT_SCORE_THRESHOLD", "0.5"))

# --- Seuils DOCX ---
# Même raisonnement produit que MAX_CSV_CELLS : la finalisation reste une
# revue humaine. À 20000 (valeur d'origine), la limite était 10x
# MAX_REVIEW_ROWS (2000) — jusqu'à 90% d'un document légitime aurait été
# caviardé "à l'aveugle", jamais montré à l'utilisateur pour relecture,
# même si le caviardage réel restait correct (job["detections"] couvre
# toujours tout, voir 14.3). Ramené à un ordre de grandeur cohérent avec
# ce qui est réellement révisable, aligné sur MAX_CSV_CELLS.
MAX_DOCX_PARAGRAPHS = int(os.environ.get("MAX_DOCX_PARAGRAPHS", "5000"))
# Protection anti "zip-bomb" : un .docx est une archive ZIP, une archive de
# quelques Ko peut en théorie se décompresser en plusieurs Go. On borne la
# taille décompressée totale et le ratio de compression par entrée avant de
# laisser python-docx/lxml ouvrir quoi que ce soit.
MAX_DOCX_UNCOMPRESSED_MB = int(os.environ.get("MAX_DOCX_UNCOMPRESSED_MB", "200"))
MAX_DOCX_ZIP_RATIO = int(os.environ.get("MAX_DOCX_ZIP_RATIO", "100"))
# Ni la taille décompressée totale ni le ratio de compression ne bornent le
# NOMBRE d'entrées — un zip de nombreux fichiers minuscules (voire vides)
# reste sous les deux seuils ci-dessus tout en coûtant cher rien qu'à
# parcourir la table des fichiers. Confirmé par test réel : ~24 Mo (juste
# sous MAX_UPLOAD_MB) avec ~260 000 entrées minimales passe les deux
# contrôles existants en ~1,2s CPU et fait grossir la mémoire du processus
# de ~150 Mo pour cette seule requête — sur un service à un seul worker
# (aucun `--workers` dans le Dockerfile), donc une requête bloque la boucle
# d'événements pour tous les utilisateurs, et la limite mémoire du conteneur
# (1 Go, docker-compose.yml) est atteignable avec seulement quelques
# requêtes de ce type. Un vrai .docx dépasse rarement quelques dizaines
# d'entrées (contenu + styles/rels/médias) ; 5000 laisse une marge large.
MAX_DOCX_ZIP_ENTRIES = int(os.environ.get("MAX_DOCX_ZIP_ENTRIES", "5000"))

# --- Seuils CSV ---
MAX_CSV_ROWS = int(os.environ.get("MAX_CSV_ROWS", "20000"))
# Plafond fixé par l'usage réel, pas seulement par ce que le pipeline peut
# techniquement encaisser : la finalisation reste une revue humaine
# (l'utilisateur doit pouvoir relire/corriger les détections avant de
# valider), un CSV de plusieurs dizaines ou centaines de milliers de
# cellules n'est de toute façon jamais réellement révisable en pratique.
# Réduit aussi la marge de manœuvre des angles "coût de ressources" 9.6.4
# (budget de temps de détection) et 9.6.5 (taille de la page de révision) :
# à 5000 cellules, les deux restent des filets de sécurité qui ne se
# déclenchent normalement jamais, plutôt que la seule protection réelle.
MAX_CSV_CELLS = int(os.environ.get("MAX_CSV_CELLS", "5000"))
# Une seule cellule anormalement longue peut consommer du CPU/mémoire de
# façon disproportionnée à l'analyse ; on fixe explicitement cette limite
# plutôt que de dépendre de la valeur par défaut du module csv (qui varie
# selon la plateforme/version Python), cohérent avec la politique
# d'épinglage explicite adoptée pour les dépendances.
MAX_CSV_FIELD_CHARS = int(os.environ.get("MAX_CSV_FIELD_CHARS", "100000"))
csv.field_size_limit(MAX_CSV_FIELD_CHARS)

# Limite le nombre de lignes RENDUES dans la page de révision (pas le
# caviardage lui-même, qui couvre toujours job["detections"] en entier au
# moment de finaliser, même les lignes au-delà de cette limite). Un CSV de
# cellules presque vides contourne le budget de temps de détection (rien à
# analyser -> rapide) tout en produisant, sans cette limite, une page HTML
# de plusieurs dizaines de Mo avec des centaines de milliers de <td> —
# confirmé par test réel : 300 000 cellules quasi vides (419 Ko de fichier)
# -> page de 16,6 Mo. Coûteux pour le serveur (génération) et pour le
# navigateur du client (rendu), indépendamment de MAX_DETECTION_SECONDS.
MAX_REVIEW_ROWS = int(os.environ.get("MAX_REVIEW_ROWS", "2000"))

# Budget de temps global pour la phase de détection (PDF/DOCX/CSV) : chaque
# appel individuel à Presidio a son propre timeout (30s, voir _analyze_text),
# mais rien ne bornait jusqu'ici le nombre de lots séquentiels pour un
# document proche des limites de taille — confirmé par test réel : un CSV
# à la limite exacte de MAX_CSV_CELLS (300 000) nécessite ~1500 appels
# séquentiels à ~0,3s chacun, ~490s au total, sur un service à worker
# unique (aucun `--workers` dans le Dockerfile) qui bloquerait donc
# l'application pour tout le monde pendant plus de 8 minutes. 90s laisse
# une marge large pour un document légitime multi-lots tout en bornant le
# pire cas à environ 3x le timeout d'un seul appel Presidio.
MAX_DETECTION_SECONDS = int(os.environ.get("MAX_DETECTION_SECONDS", "90"))


def _reject(reason: str, status_code: int, detail: str) -> None:
    """Incrémente le compteur de rejets (metrics.DOCUMENTS_REJECTED) puis lève
    l'HTTPException correspondante — point de passage unique pour ne pas
    oublier d'instrumenter un futur rejet ajouté. `reason` doit rester une
    catégorie fermée (voir metrics.py) : jamais une valeur dérivée de
    l'entrée utilisateur."""
    metrics.DOCUMENTS_REJECTED.labels(reason=reason).inc()
    raise HTTPException(status_code=status_code, detail=detail)


def _send_alert(alert: Alert) -> None:
    """Point de passage unique pour toute alerte (voir supervision.py) —
    intercepte TOUTE exception plutôt que de la laisser remonter, y
    compris une config ALERT_SINK invalide (RuntimeError/ValueError) ou un
    hôte syslog injoignable (DNS, connexion refusée). Sans ce filet, une
    alerte échouée transformait par exemple un rejet antivirus proprement
    géré (503/400) en 500 générique non intercepté, ou tuait
    silencieusement et définitivement le thread `_cleanup_sweep_loop` (pas
    de relance) — une alerte ne doit jamais faire échouer ou geler le flux
    qu'elle est censée surveiller."""
    try:
        get_alert_sink().send(alert)
    except Exception:
        log.warning(
            "Échec d'envoi d'une alerte (source=%s, sévérité=%s) — poursuite sans bloquer le flux principal",
            alert.source, alert.severity.value, exc_info=True,
        )


def _check_detection_deadline(start_time: float) -> None:
    """Lève une HTTPException si la détection en cours dépasse
    MAX_DETECTION_SECONDS — à appeler avant chaque lot/page pour ne jamais
    laisser un document proche des limites de taille bloquer le worker
    unique pendant plusieurs minutes (voir MAX_DETECTION_SECONDS)."""
    if time.time() - start_time > MAX_DETECTION_SECONDS:
        _reject(
            "trop_volumineux",
            400,
            f"Ce document est trop volumineux pour être analysé dans le temps imparti (max {MAX_DETECTION_SECONDS}s) — réduisez sa taille ou contactez l'administrateur.",
        )

# Marqueur de remplacement pour le caviardage texte (DOCX/CSV) : une valeur
# fixe plutôt que des blocs proportionnels à la longueur d'origine, pour ne
# pas laisser fuir la longueur approximative de la donnée masquée.
REDACTION_MARKER = "[MASQUÉ]"

# Tout fichier créé par le service (document original et caviardé en transit
# dans /data/tmp, journal d'audit, fichiers temporaires de l'OCR) ne doit être
# lisible que par l'utilisateur du service. Sans ce umask, le défaut du
# conteneur (022) produisait des fichiers en 644 : sur l'hôte, le bind mount
# /var/lib/anonymiseur/workdir exposait alors chaque document (avant ET après
# caviardage) à n'importe quel utilisateur local pendant toute la durée du
# TTL, et le journal d'audit en permanence. Placé avant le premier mkdir et
# avant la création du RotatingFileHandler ci-dessous, pour couvrir tout.
os.umask(0o077)

WORKDIR = Path("/data/tmp")
WORKDIR.mkdir(parents=True, exist_ok=True)

# Jobs en attente de révision humaine (entre la détection et la validation).
# En mémoire uniquement : acceptable pour un process unique (lab), mais ne
# survit pas à un redémarrage du conteneur — un job en cours de révision au
# moment d'un redéploiement doit être relancé par l'utilisateur.
PENDING_JOBS: dict[str, dict] = {}
_PENDING_JOBS_LOCK = threading.Lock()

# Journal d'audit : emplacement séparé des fichiers temporaires, PAS soumis à
# la purge FILE_TTL_SECONDS. Ne contient jamais le nom réel du fichier ni le
# contenu du document — uniquement des métadonnées (qui, quand, quoi comme
# volumétrie de caviardage), suffisant pour un contrôle de conformité sans
# recréer un risque de fuite de données.
AUDIT_DIR = Path("/data/audit")
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

audit_log = logging.getLogger("anonymiseur.audit")
audit_log.setLevel(logging.INFO)
audit_log.propagate = False  # ne pas dupliquer dans les logs applicatifs normaux
_audit_handler = RotatingFileHandler(
    AUDIT_DIR / "audit.log", maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8"
)
_audit_handler.setFormatter(logging.Formatter("%(message)s"))
audit_log.addHandler(_audit_handler)


def _record_audit_event(**fields):
    """Ajoute une ligne JSON au journal d'audit (append-only, avec rotation)."""
    event = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
    audit_log.info(json.dumps(event, ensure_ascii=False))


THEMES_DIR = Path(__file__).parent / "themes"
COMMON_RECOGNIZERS_FILENAME = "common.json"


def _load_themes() -> dict:
    """Charge tous les fichiers de thèmes sélectionnables (app/themes/*.json),
    à l'exception de common.json qui n'est pas un thème mais un socle de
    reconnaisseurs appliqué à tous les thèmes (voir _load_common_recognizers)."""
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
    """Reconnaisseurs communs (ex: adresses postales) appliqués quel que soit
    le thème choisi, y compris si aucun thème n'est sélectionné — pour les
    faux négatifs qui ne sont pas spécifiques à un domaine métier."""
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


def _peek_zip_entry_count(raw: bytes) -> int | None:
    """
    Lit le nombre d'entrées déclaré dans l'enregistrement de fin de
    répertoire central (EOCD) d'un ZIP, sans jamais appeler
    `zipfile.ZipFile()` — c'est justement l'ouverture par `zipfile`, qui
    parse tout le répertoire central d'un coup, qui coûte cher sur une
    archive à très grand nombre d'entrées (confirmé par test réel : ~1,1s
    CPU et ~150 Mo de mémoire pour ~260 000 entrées minimales tenant dans
    ~24 Mo, sur un service à worker unique où ce temps bloque la boucle
    d'événements pour tout le monde). Renvoie None si l'EOCD est introuvable
    (laisse `zipfile.ZipFile` lever l'erreur "corrompu" habituelle).

    Gère le cas Zip64 (champ 16 bits saturé à 0xFFFF, vrai compte dans le
    "Zip64 EOCD record" localisé via le "Zip64 EOCD locator" qui précède
    l'EOCD standard) — un zip à nombre d'entrées extrême en a nécessairement
    besoin, donc l'ignorer laisserait passer exactement le cas à bloquer.
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
    Vérifie qu'une archive ZIP est bien un .docx exploitable en sécurité :
      - contient réellement word/document.xml (pas un .xlsx/.pptx renommé)
      - ne contient pas de macro VBA (word/vbaProject.bin -> .docm déguisé)
      - n'est pas une "zip bomb" (taille décompressée disproportionnée par
        rapport à la taille de l'archive, par entrée et au global, OU nombre
        d'entrées disproportionné — un ratio/volume individuellement sages
        n'empêchent pas des dizaines de milliers de fichiers minuscules,
        coûteux à eux seuls rien qu'à parcourir la table des fichiers)
    Toute condition non respectée lève une HTTPException 400 explicite.
    """
    entry_count = _peek_zip_entry_count(raw)
    if entry_count is not None and entry_count > MAX_DOCX_ZIP_ENTRIES:
        _reject(
            "structure_invalide",
            400,
            f"Structure d'archive suspecte détectée (protection anti zip-bomb, {entry_count} entrées, max {MAX_DOCX_ZIP_ENTRIES}).",
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        names = zf.namelist()
    except zipfile.BadZipFile as exc:
        metrics.DOCUMENTS_REJECTED.labels(reason="format_invalide").inc()
        raise HTTPException(status_code=400, detail="Fichier ZIP/DOCX invalide ou corrompu") from exc

    if "word/document.xml" not in names:
        _reject("format_invalide", 400, "Ce fichier n'est pas un document Word (.docx) valide.")
    if "word/vbaProject.bin" in names:
        _reject("format_invalide", 400, "Les documents avec macros (.docm) ne sont pas acceptés.")
    if len(names) > MAX_DOCX_ZIP_ENTRIES:
        # Filet de sécurité si l'EOCD n'a pas pu être lu en amont (ex.
        # commentaire ZIP mal formé) : coûte la lecture complète qu'on
        # essaie d'éviter ci-dessus, mais protège quand même contre la
        # dégradation qui suit (python-docx, lxml, etc.) sur le reste du
        # pipeline.
        _reject(
            "structure_invalide",
            400,
            f"Structure d'archive suspecte détectée (protection anti zip-bomb, {len(names)} entrées, max {MAX_DOCX_ZIP_ENTRIES}).",
        )

    total_uncompressed = sum(info.file_size for info in zf.infolist())
    if total_uncompressed > MAX_DOCX_UNCOMPRESSED_MB * 1024 * 1024:
        _reject(
            "trop_volumineux",
            400,
            f"Document trop volumineux une fois décompressé (protection anti zip-bomb, max {MAX_DOCX_UNCOMPRESSED_MB} Mo).",
        )
    for info in zf.infolist():
        if info.compress_size > 0 and (info.file_size / info.compress_size) > MAX_DOCX_ZIP_RATIO:
            _reject("structure_invalide", 400, "Structure d'archive suspecte détectée (protection anti zip-bomb).")

    return "docx"


def _strip_unicode_control_and_format_chars(value: str) -> str:
    """
    Retire tout caractère Unicode de catégorie "Other" (Cc/Cf/Co/Cs/Cn) d'une
    chaîne d'origine non fiable (en-tête HTTP) avant qu'elle ne rejoigne un
    job ou le journal d'audit.

    Point de vigilance précis (vérifié empiriquement, voir section 3.5 de
    l'audit) : `json.dumps(..., ensure_ascii=False)` neutralise déjà toute
    tentative de forger une fausse ligne JSON (les caractères de contrôle
    C0/C1, guillemets et antislashs sont échappés) — mais PAS les caractères
    de formatage bidirectionnel Unicode (catégorie Cf, ex. U+202E "Right-to-
    Left Override"), valides en UTF-8 et donc réécrits tels quels dans le
    fichier ET dans la réponse JSON de `/api/audit`. Un tel caractère dans
    `X-Auth-Request-Email` permettrait d'afficher ce champ dans un ordre
    trompeur pour quiconque relit le journal (terminal ou UI d'audit) — pas
    une faille d'intégrité du format, mais un risque de spoofing visuel sur
    un journal dont la valeur repose justement sur sa lisibilité humaine.
    """
    return "".join(ch for ch in value if unicodedata.category(ch)[0] != "C")


def _looks_like_text(raw: bytes, sample_size: int = 8192) -> bool:
    """Heuristique faible mais suffisante pour écarter un binaire arbitraire
    présenté comme un .csv : un CSV n'a pas de signature binaire propre, donc
    contrairement au PDF/DOCX on ne peut valider que l'absence d'octets nuls
    et un décodage texte réussi. Risque résiduel documenté : ne garantit pas
    que le contenu est réellement tabulaire — à couvrir dans le futur audit
    dédié (voir aussi _parse_csv_rows pour les garde-fous de volumétrie)."""
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
    Scanne le fichier brut via le moteur configuré (voir antivirus.py) avant
    tout parsing PDF/DOCX/CSV. AV_ENGINE=none (défaut) fait de ceci un no-op
    silencieux (verdict toujours propre). Le comportement en cas de verdict
    défavorable dépend d'AV_ENFORCE : blocage (HTTPException) ou simple
    journalisation d'avertissement en mode observation — jamais un échec
    silencieux, pour que le choix de laisser passer un fichier menacé reste
    visible dans les logs.

    `filename` (nom brut) n'est transmis qu'au moteur de scan lui-même
    (utile pour certains moteurs qui se basent sur l'extension) ; seul
    `filename_hash` apparaît dans les logs, comme partout ailleurs dans le
    projet (journal d'audit compris) — un nom de fichier peut contenir une
    donnée patient réelle.
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
                detail="Le service d'analyse antivirus est indisponible, veuillez réessayer plus tard.",
            ) from exc
        log.warning(
            "AV_ENFORCE=false : scan antivirus indisponible pour fichier %s, fichier traité quand même (%s)",
            filename_hash, exc,
        )
        return

    if not result.is_clean:
        # threat_name vient du serveur ICAP (en-tête X-Virus-ID/X-Infection-Found,
        # voir antivirus.py) — pas directement du contenu du fichier uploadé dans
        # un flux ICAP conforme, mais assaini par précaution avant de rejoindre le
        # journal syslog et la réponse HTTP : même risque de spoofing visuel par
        # caractère de formatage Unicode (RTL override...) que celui trouvé et
        # corrigé sur le journal d'audit (3.5), pour toute source de texte externe
        # au projet destinée à être relue par un humain.
        threat = _strip_unicode_control_and_format_chars(result.threat_name or "menace inconnue")
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
                detail=f"Menace détectée par l'antivirus ({threat}) — fichier rejeté.",
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
    Détermine le type réel du fichier à partir de son contenu binaire, jamais
    du Content-Type ou du nom de fichier déclarés par le client (falsifiables).
    JPG et JPEG sont le même format (signature \\xFF\\xD8\\xFF) — traités de
    façon identique, sans distinction.
    """
    if raw.startswith(b"%PDF-"):
        return "pdf"
    if raw.startswith(b"PK\x03\x04"):
        return _validate_docx_zip(raw)
    if raw.startswith(PNG_SIGNATURE) or raw.startswith(JPEG_SIGNATURE):
        return "image"
    if _looks_like_text(raw):
        return "csv"
    _reject("format_invalide", 400, "Format de fichier non reconnu (seuls PDF, DOCX, CSV, PNG et JPEG sont acceptés).")


def _decode_csv_bytes(raw: bytes) -> tuple[str, str]:
    """Décode les octets d'un CSV en essayant les encodages les plus courants
    en pratique (UTF-8 avec BOM Excel, UTF-8 standard, puis Windows-1252
    fréquent sur les exports Excel FR avec des accents)."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    _reject("format_invalide", 400, "Encodage de fichier CSV non reconnu (UTF-8 ou Windows-1252 attendu).")


def _detect_csv_delimiter(sample_text: str) -> str:
    """Détecte le séparateur (virgule ou point-virgule). Le point-virgule est
    très répandu en France (Excel FR utilise la virgule comme séparateur
    décimal, donc exporte les CSV avec ';')."""
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
            _reject("trop_volumineux", 400, f"Fichier CSV trop volumineux ({len(rows)}+ lignes, max {MAX_CSV_ROWS})")
    return rows


_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _neutralize_csv_formula(value: str) -> str:
    """
    Protection contre l'injection de formule CSV : si une cellule du fichier
    de sortie commence par un caractère qu'Excel/LibreOffice interprète comme
    le début d'une formule, on la préfixe d'une apostrophe pour la neutraliser
    à l'ouverture. S'applique à TOUTES les cellules du fichier produit (pas
    seulement celles caviardées) — protège la personne qui ouvrira le fichier
    anonymisé, un risque indépendant de l'anonymisation elle-même mais
    pertinent à couvrir dès maintenant plutôt que de le laisser au futur audit.
    """
    if value and value[0] in _CSV_FORMULA_TRIGGERS:
        return "'" + value
    return value


def _sweep_orphaned_files():
    """
    Supprime tout fichier de sortie plus vieux que FILE_TTL_SECONDS.

    Filet de sécurité pour les fichiers "orphelins" : si le conteneur
    redémarre (ex: redéploiement) entre la création d'un fichier et
    l'écoulement de son délai de purge individuel (_schedule_cleanup), le
    thread en mémoire qui devait le supprimer meurt avec l'ancien process et
    le fichier reste sur le disque indéfiniment. Ce balayage, lancé au
    démarrage puis répété périodiquement, rattrape ces cas.
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
    """Supprime les jobs en révision jamais finalisés au-delà de JOB_REVIEW_TTL_SECONDS."""
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


# Volumes réellement montés (voir docker-compose.yml) dont l'espace disque
# libre est surveillé. WORKDIR et AUDIT_DIR peuvent être deux points de
# montage distincts en production (bind mounts séparés) même s'ils partagent
# le même disque en lab.
_MONITORED_VOLUMES = {"workdir": WORKDIR, "audit": AUDIT_DIR}

# Seuils d'alerte espace disque : déclenché sur le plus restrictif des deux
# critères (pourcentage OU valeur absolue), pour rester pertinent aussi bien
# sur un petit volume (où 10% peut représenter plusieurs Go de marge) que sur
# un très gros volume (où 10% peut rester énorme alors que l'espace absolu
# restant est déjà critique).
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
    """Vérification de santé légère : une simple requête HTTP suffit, pas
    besoin de reproduire un vrai appel d'analyse/anonymisation ici."""
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
        # Ce thread est la seule chose qui purge les fichiers orphelins et les
        # jobs de révision expirés (1.11/1.14) — une exception non rattrapée
        # ici (ex. bug dans une vérification ajoutée plus tard) tuerait le
        # thread silencieusement et POUR TOUJOURS (pas de relance), désactivant
        # ce nettoyage jusqu'au prochain redémarrage du conteneur sans que
        # personne ne le remarque. `_send_alert` intercepte déjà les échecs
        # d'alerte eux-mêmes ; ce filet couvre tout le reste par précaution.
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
    # --- Démarrage ---
    # Valide la config antivirus dès maintenant (AV_ENGINE inconnu, ICAP_HOST
    # manquant, ICAP_PORT/ICAP_TIMEOUT_SECONDS non numériques...) : échec
    # rapide et explicite au démarrage plutôt qu'un 500 générique sur la
    # première requête /api/detect venue une fois en production.
    get_scanner()

    # Idem pour la config d'alerting (ALERT_SINK) — mais contrairement à
    # l'antivirus, une supervision mal configurée ne doit jamais empêcher le
    # service principal (anonymisation, fonction critique) de démarrer :
    # simple avertissement au démarrage, pas d'échec fatal. `_send_alert`
    # retentera de toute façon la construction à la prochaine alerte
    # (lru_cache ne mémorise pas les échecs, voir supervision.py).
    try:
        get_alert_sink()
    except Exception:
        log.error("Configuration ALERT_SINK invalide, alertes non fonctionnelles pour l'instant", exc_info=True)

    # Rattrape immédiatement les fichiers laissés par un précédent process
    # (crash, redéploiement) avant même le premier tour de boucle périodique.
    _sweep_orphaned_files()
    threading.Thread(target=_cleanup_sweep_loop, daemon=True).start()
    log.info("Balayage des fichiers orphelins démarré (contrôle toutes les 60s)")

    yield  # l'application tourne ici

    # --- Extinction ---
    log.info("Arrêt de l'application")


app = FastAPI(title="Anonymiseur de documents - PDF/DOCX/CSV", lifespan=lifespan)

ERROR_TITLES = {
    400: "Requête invalide",
    404: "Introuvable",
    413: "Fichier trop volumineux",
    422: "Formulaire incomplet",
    502: "Service indisponible",
}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Affiche une page d'erreur lisible pour un navigateur (formulaire soumis
    normalement), tout en gardant une réponse JSON classique pour un appel
    scripté/API (curl, outillage) qui ne demande pas explicitement du HTML.
    """
    wants_html = "text/html" in request.headers.get("accept", "")
    if not wants_html:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    title = ERROR_TITLES.get(exc.status_code, "Une erreur est survenue")
    return HTMLResponse(
        status_code=exc.status_code,
        content=f"""
        <!doctype html>
        <html lang="fr">
        <head><meta charset="utf-8"><title>{title} - Anonymiseur</title></head>
        <body style="font-family: sans-serif; max-width: 560px; margin: 80px auto; text-align:center;">
          <div style="font-size:3em; margin-bottom:8px;">⚠️</div>
          <h1 style="margin-bottom:8px;">{html.escape(title)}</h1>
          <p style="color:#555; font-size:1.1em;">{html.escape(str(exc.detail))}</p>
          <p style="margin-top:32px;">
            <a href="/" style="
                display:inline-block; padding:10px 24px; background:#0d6efd;
                color:white; text-decoration:none; border-radius:4px;">
              &larr; Retour à l'accueil
            </a>
          </p>
        </body>
        </html>
        """,
    )


# ---------------------------------------------------------------------------
# Plafond de taille de requête HTTP, appliqué AVANT tout parsing de formulaire
# ---------------------------------------------------------------------------
# Le contrôle MAX_UPLOAD_MB de detect_document s'exécute après `await
# file.read()`, c'est-à-dire après que Starlette a déjà reçu et stocké
# l'intégralité du corps multipart (en mémoire jusqu'à 1 Mo, puis dans un
# fichier temporaire sous /tmp — un tmpfs, donc de la mémoire comptée dans la
# limite du conteneur), puis que tout a été relu en mémoire. Aucun plafond
# n'existait en amont (ni ici, ni côté Traefik) : reproduit sur un conteneur
# jetable identique au service (limite 1 Go, profil seccomp de blocage), un
# SEUL upload chunké de 700 Mo tue le conteneur par OOM (exit 137) en quelques
# secondes — et avec lui tous les jobs en attente de révision des autres
# utilisateurs. Le rate limiting Traefik (5 req/min) ne protège pas : une
# seule requête suffit.
#
# Deux garde-fous, dans l'ordre :
#  - Content-Length déclaré supérieur au plafond → 413 immédiat, sans lire un
#    seul octet du corps.
#  - Corps chunké (sans Content-Length) ou Content-Length mensonger → chaque
#    fragment reçu est compté ; dès que le total dépasse le plafond, la
#    lecture est interrompue par une HTTPException 413 (sous-classe, pour que
#    FastAPI la laisse remonter telle quelle jusqu'à http_exception_handler au
#    lieu de la convertir en 400 générique). Rien n'a été accumulé au-delà du
#    plafond à ce moment-là.
# Le plafond laisse 2 Mo de marge au-dessus de MAX_UPLOAD_MB pour l'enrobage
# multipart et les autres champs de formulaire (manual_zones, limité par
# ailleurs à 1 Mo par Starlette). Le contrôle exact et lisible par
# l'utilisateur ("Fichier trop volumineux (x Mo, max 25 Mo)") reste celui de
# detect_document ; celui-ci est une barrière de ressources, pas d'ergonomie.
MAX_REQUEST_BODY_BYTES = (MAX_UPLOAD_MB + 2) * 1024 * 1024


# ---------------------------------------------------------------------------
# Section 3.7 : secret partagé passerelle (Traefik -> app)
# ---------------------------------------------------------------------------
# `app` faisait confiance à `X-Auth-Request-Email` (journal d'audit ET
# contrôle de propriétaire des jobs, section 1.38) sans jamais vérifier que
# la requête avait bien traversé Traefik -> oauth2-proxy. Un client externe
# ne peut pas forger cet en-tête (Traefik le supprime et le remplace avant le
# forwardAuth), MAIS un conteneur du même réseau Docker que `app`
# (`app-internal` OU `backend` : presidio, ou un voisin compromis/malveillant)
# peut joindre `app:8000` DIRECTEMENT, en contournant Traefik entièrement, et
# forger l'en-tête de toutes pièces. Vérifié empiriquement : contournement
# confirmé de bout en bout (une entrée d'audit `admin@usurpe.fr` a été écrite
# via un conteneur voisin sans jamais passer par oauth2-proxy).
#
# Parade (même principe que les secrets déjà en place) : un secret partagé,
# connu de Traefik seul et de `app`. Traefik l'injecte, en écrasant toute
# valeur cliente, sur toute requête routée vers `app` ; `app` le vérifie ICI,
# AVANT tout traitement, à temps constant (hmac.compare_digest, jamais ==).
# Absent ou incorrect -> 401 immédiat, avant même de lire X-Auth-Request-Email.
#
# Le secret est monté en Docker secret (`/run/secrets/gateway_secret`, même
# convention que oauth2_*), jamais en clair dans docker-compose.yml. Traefik
# l'injecte via un fichier de configuration dynamique gitignoré
# (`traefik/dynamic/gateway-secret.yml`), rendu par generate-secrets.sh à
# partir de la MÊME valeur.
#
# Désactivé (no-op) si aucun secret n'est configuré — dev/test locaux, où
# aucun /run/secrets n'est monté. Un avertissement explicite est journalisé
# au démarrage dans ce cas ; jamais un contournement silencieux en production.
GATEWAY_SECRET_HEADER = b"x-internal-gateway-secret"


def _read_gateway_secret() -> str:
    # Surcharge directe par variable d'env (tests) ; sinon lecture du Docker
    # secret monté par Docker au démarrage. Absent -> chaîne vide -> désactivé.
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
    """Vérifie le secret partagé injecté par Traefik (section 3.7) AVANT tout
    traitement de requête — donc avant que le moindre endpoint ne lise
    `X-Auth-Request-Email`. `/health` est exempté : un HEALTHCHECK Docker
    éventuel interroge le conteneur en loopback, pas via Traefik, et ne doit
    pas se mettre à échouer à cause de ce contrôle. Comparaison à temps
    constant. No-op si aucun secret configuré (voir GATEWAY_SECRET)."""

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return

        secret = GATEWAY_SECRET  # lu à chaque requête (surchargeable en test)
        if secret and scope.get("path") != "/health":
            provided = b""
            for name, value in scope.get("headers", []):
                if name == GATEWAY_SECRET_HEADER:
                    provided = value
                    break
            if not hmac.compare_digest(provided, secret.encode("utf-8")):
                response = await http_exception_handler(
                    Request(scope),
                    HTTPException(status_code=401, detail="Requête non autorisée"),
                )
                await response(scope, receive, send)
                return

        await self.asgi_app(scope, receive, send)


class RequestBodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=413,
            detail=f"Requête trop volumineuse (fichier limité à {MAX_UPLOAD_MB} Mo)",
        )


class _RequestBodyLimitMiddleware:
    """Middleware ASGI pur (pas BaseHTTPMiddleware : celui-ci consommerait le
    corps différemment et casserait le streaming)."""

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return

        limit = MAX_REQUEST_BODY_BYTES  # lu à chaque requête (surchargeable en test)

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
# En-têtes de sécurité HTTP, sur TOUTES les réponses (pages, aperçus, erreurs)
# ---------------------------------------------------------------------------
# Aucun en-tête de ce type n'était posé, ni ici ni par Traefik. Le plus
# important pour ce projet est `Cache-Control: no-store` : sans lui, les
# aperçus de pages (/api/preview_image — rendu du document ORIGINAL, avant
# caviardage) et les fichiers téléchargés étaient écrits dans le cache disque
# du navigateur du poste utilisateur, où ils survivent à la fermeture de la
# session et au TTL côté serveur. Les autres en-têtes sont la base attendue
# d'une application web manipulant des données de santé : anti-MIME-sniffing,
# anti-clickjacking (frame-ancestors + X-Frame-Options pour les vieux
# navigateurs), CSP limitant chargement de scripts/images/connexions à
# l'origine elle-même (les pages de révision utilisent des scripts et styles
# inline et des images data: pour les aperçus DOCX, d'où 'unsafe-inline' et
# data: — la CSP bloque surtout toute exfiltration vers un autre domaine et
# tout script externe), pas de fuite du job_id via le Referer hors origine,
# HSTS (ignoré par les navigateurs tant que la connexion n'est pas HTTPS, donc
# sans effet en test direct sur le port 8000), isolation cross-origin des
# ressources (un site tiers ne peut pas embarquer un aperçu). Un en-tête déjà
# posé explicitement par une réponse n'est jamais écrasé.
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

# /api/download sert aussi le PDF/image caviardé DANS la page "Aperçu
# (contrôle visuel)" générée par /api/finalize (<iframe>/<img> sur la même
# origine). frame-ancestors 'none' + X-Frame-Options: DENY, posés par défaut
# ci-dessus sur TOUTE réponse, empêchaient alors le navigateur d'afficher
# cette réponse dans SA PROPRE page ("Firefox ne peut ouvrir cette page"),
# bien que la requête HTTP elle-même aboutisse (200). Seules les extensions
# servies en inline (voir _INLINE_EXTENSIONS plus bas) ont besoin de ce
# relâchement ciblé à 'self' — un site tiers reste bloqué comme avant ; les
# téléchargements en pièce jointe (.docx/.csv, jamais cadrés) gardent 'none'.
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


# Ordre : le dernier ajouté est le plus externe. Les en-têtes doivent envelopper
# aussi la réponse 413 émise directement par le plafond de taille ET la réponse
# 401 de la passerelle (section 3.7). La vérification du secret passerelle
# s'exécute AVANT le plafond de corps (inutile de tamponner le corps d'un
# appelant non autorisé) mais SOUS les en-têtes de sécurité (qui enveloppent
# donc aussi son 401).
app.add_middleware(_RequestBodyLimitMiddleware)
app.add_middleware(_GatewaySecretMiddleware)
app.add_middleware(_SecurityHeadersMiddleware)


def _schedule_cleanup(path: Path, delay: int = FILE_TTL_SECONDS):
    """Supprime le fichier après `delay` secondes (purge automatique)."""

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
    Convertit les mots tout en majuscules (ex: "DURAND") en casse titre
    ("Durand") pour aider le modèle NER à les reconnaître comme noms
    propres — spaCy s'appuie beaucoup sur la casse pour cette détection.

    Important: .capitalize() ne change jamais la longueur d'un mot, donc les
    positions (start/end) renvoyées par l'analyzer restent valides pour
    découper le texte ORIGINAL (non normalisé) utilisé pour le caviardage.
    """
    return re.sub(r"\b[A-ZÀ-Ý]{2,}\b", lambda m: m.group(0).capitalize(), text)


# Variantes Unicode de tiret rencontrées dans des PDF réels (selon l'outil de
# génération/la police utilisée) qui ne correspondent pas au trait d'union
# ASCII standard attendu par les regex de reconnaissance de dates. Un
# remplacement 1-pour-1 préserve la longueur du texte, donc les positions
# (start/end) renvoyées par l'analyzer restent valides sur le texte original.
_DASH_VARIANTS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"


def _normalize_dashes(text: str) -> str:
    """Remplace les tirets typographiques (demi-cadratin, cadratin, signe
    moins mathématique...) par un trait d'union ASCII standard, pour que les
    dates au format JJ-MM-AAAA (ou similaire) soient reconnues quel que soit
    le caractère de séparation réellement utilisé dans le PDF source."""
    return text.translate({ord(c): "-" for c in _DASH_VARIANTS})


def _analyze_text(text: str, theme: dict | None = None) -> list[dict]:
    """Appelle presidio-analyzer sur un morceau de texte, renvoie les entités trouvées.

    Si un thème est fourni, ses reconnaisseurs personnalisés (ad_hoc_recognizers),
    sa liste d'exclusions (allow_list) et son seuil de sensibilité
    (score_threshold) sont envoyés avec la requête — sans jamais toucher à
    la config statique du conteneur presidio-analyzer.
    """
    if not text.strip():
        return []

    payload = {"text": text, "language": LANGUAGE}

    # Les reconnaisseurs communs s'appliquent toujours, thème choisi ou non.
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

    # Revérifié côté client (pas seulement envoyé dans la requête) : selon la
    # version de presidio-analyzer, le paramètre score_threshold n'est pas
    # toujours honoré côté serveur pour tous les reconnaisseurs. Filet de
    # sécurité peu coûteux, score absent traité comme maximal (1.0) pour ne
    # jamais rejeter une entité par excès de prudence si le champ manque.
    entities_before = entities
    entities = [e for e in entities_before if e.get("score", 1.0) >= score_threshold]

    # Diagnostic : ne loggue jamais le texte détecté (voir politique
    # d'audit), seulement type + score, pour distinguer un vrai problème de
    # seuil NER (scores variés, proches du seuil) d'un reconnaisseur
    # personnalisé à score fixe (souvent 1.0, qu'aucun seuil ne peut filtrer).
    if entities_before:
        log.info(
            "Analyse: seuil=%.2f, %d entité(s) avant filtrage %s, %d après.",
            score_threshold,
            len(entities_before),
            [(e.get("entity_type"), round(e.get("score", 1.0), 2)) for e in entities_before],
            len(entities),
        )

    # Filtrage après coup plutôt qu'une liste positive de types envoyée à
    # Presidio : on retire uniquement ce qu'on sait être du bruit (ex.
    # ORGANIZATION sur des empans multi-lignes fusionnant des fragments sans
    # rapport dans les formulaires médicaux denses), sans risquer d'exclure
    # silencieusement un type légitime qu'on n'aurait pas pensé à lister
    # (email, téléphone...).
    excluded_types = set(theme.get("excluded_entity_types", [])) if theme else set()
    if excluded_types:
        entities = [e for e in entities if e.get("entity_type") not in excluded_types]

    return entities


def _anonymize_text(text: str, entities: list[dict]) -> str:
    """Appelle presidio-anonymizer pour produire une version texte anonymisée (audit)."""
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
        # Non bloquant : le caviardage PDF ne dépend pas de cet appel
        return text


PREVIEW_ZOOM = 2.0  # facteur d'agrandissement pour le rendu des pages en image

# Défense en profondeur contre CVE-2026-3308 (MuPDF, pdf_load_image_imp) :
# le stride d'une image est calculé en entier 32 bits côté MuPDF, ce qui
# peut déborder sur des dimensions déclarées volontairement absurdes et
# provoquer une écriture hors bornes lors du décodage. La version de
# PyMuPDF utilisée ici est déjà corrigée, mais on vérifie quand même les
# dimensions déclarées (lues depuis les métadonnées de l'objet image, sans
# décodage) avant tout appel à get_pixmap(), en défense en profondeur.
#
# Même seuil réutilisé pour les images PNG/JPEG uploadées directement (voir
# _open_and_validate_image) : même principe de bombe de décompression,
# même défense (dimensions déclarées lues avant tout décodage complet).
MAX_IMAGE_PIXELS = int(os.environ.get("MAX_IMAGE_PIXELS", "40_000_000"))

# Aligne explicitement la protection anti-bombe de décompression intégrée à
# Pillow sur notre propre seuil, plutôt que de dépendre silencieusement de sa
# valeur par défaut (89 478 485, différente de MAX_IMAGE_PIXELS ci-dessus).
# L'assertion vérifie que cette protection reste bien active (jamais
# désactivée en mettant Image.MAX_IMAGE_PIXELS à None ailleurs dans le code)
# — point de vigilance demandé explicitement : une désactivation silencieuse
# de cette limite romprait la défense en profondeur ci-dessous sans qu'aucune
# erreur ne le signale avant qu'une vraie bombe de décompression ne soit
# traitée.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
assert Image.MAX_IMAGE_PIXELS is not None, "PIL.Image.MAX_IMAGE_PIXELS ne doit jamais être désactivé (None)"


def _check_page_images_sane(page: "fitz.Page") -> None:
    """
    Rejette la page si une image qui y est incrustée déclare des dimensions
    nulles/négatives ou dépassant MAX_IMAGE_PIXELS. À appeler avant tout
    page.get_pixmap() : get_images(full=True) ne fait que lire les entrées
    /Width et /Height de l'objet image dans le PDF, sans décoder les pixels.
    """
    for img in page.get_images(full=True):
        width, height = img[2], img[3]
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise HTTPException(
                status_code=400,
                detail="Cette page contient une image aux dimensions invalides.",
            )


# Types d'entités propagés sur tout le document une fois confirmés au moins
# une fois — un nom de patient qui échappe au NER dans un contexte dense
# (ex: page listant de nombreux biologistes) reste néanmoins une donnée
# identifiante qui se répète tel quel ailleurs dans le document.
PROPAGATED_ENTITY_TYPES = {"PERSON", "LOCATION"}


def _name_variants(name: str) -> set[str]:
    """
    Génère les variantes plausibles d'un nom déjà confirmé, pour le
    retrouver ailleurs dans le document même si sa casse ou l'ordre
    prénom/nom diffère d'un endroit à l'autre (observé en pratique : une
    page a \"Jean DURAND\", une autre \"DURAND JEAN\").
    Se limite à l'inversion de deux mots — au-delà, l'ordre réel est trop
    ambigu pour être deviné sans risque.
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
    Détecte les entités sensibles sans les caviarder. Pour chaque occurrence
    visuelle trouvée, renvoie à la fois sa position réelle dans le PDF (pour
    le caviardage final) et sa position à l'échelle de l'aperçu image (pour
    l'affichage cliquable côté navigateur) — les deux calculées avec la même
    matrice de zoom pour rester parfaitement alignées.

    Deux passes : (1) détection standard page par page via Presidio, (2)
    propagation des noms confirmés en passe 1 vers le reste du document, là
    où le NER a pu échapper une occurrence identique (contexte dense,
    formulaire, page différente) — voir PROPAGATED_ENTITY_TYPES.
    """
    matrix = fitz.Matrix(PREVIEW_ZOOM, PREVIEW_ZOOM)
    detections: list[dict] = []

    page_texts: list[str] = []
    propagate_candidates: dict[str, str] = {}
    already_covered: set[tuple[int, tuple[float, float, float, float]]] = set()
    detection_start = time.time()

    # --- Passe 1 : détection standard, page par page ---
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
            # Un nom de famille isolé est trop ambigu pour être propagé sans
            # risque, mais un nom de ville isolé (ex: "Ajaccio") est un bon
            # candidat même seul — d'où la règle différente selon le type.
            # Seuil de 4 caractères minimum pour un mot LOCATION isolé : une
            # abréviation courte (ex: "Enr", 3 lettres) s'est révélée capable
            # de se propager en préfixe sur d'autres mots via la recherche
            # insensible à la casse de PyMuPDF (search_for) — 4+ caractères
            # laisse passer les vrais noms de ville courts (Metz, Caen, Nice,
            # Lyon...) tout en bloquant ce type de faux positif.
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

            # Un nom propre ou une ville confirmés une fois deviennent
            # candidats à la propagation sur le reste du document, avec le
            # type d'entité d'origine conservé pour l'audit/le résumé.
            if is_propagatable:
                propagate_candidates[stripped] = entity_type

    # --- Passe 2 : propagation des noms/villes confirmés vers les autres pages ---
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
    """Calcule l'IoU (intersection sur union) entre deux rectangles [x0, y0, x1, y1]."""
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
    Regroupe les détections dont les rectangles se chevauchent fortement
    (typiquement : plusieurs recognizers différents qui détectent la même
    portion de texte, ex. le NER générique + un pattern personnalisé sur le
    même nom). Sans ce regroupement, deux zones invisiblement superposées
    peuvent exister au même endroit : cliquer sur l'une pour l'exclure
    laisse l'autre active, et la zone semble "ne pas se décaviarder".

    Chaque cluster expose un seul id cliquable côté navigateur, mais garde
    la liste de tous les ids de détection sous-jacents pour l'exclusion.
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
# Image (PNG/JPEG) - validation d'entrée, OCR + détection PII
# ---------------------------------------------------------------------------

OCR_LANGUAGE = "fra"

# Chemin absolu plutôt qu'une simple recherche dans $PATH (comportement par
# défaut de pytesseract, `tesseract_cmd = "tesseract"`) : défense en
# profondeur contre un détournement de PATH, même si le système de fichiers
# racine en lecture seule (voir docker-compose.yml, `read_only: true`) rend
# déjà ce vecteur impraticable — aucun répertoire de PATH n'est inscriptible
# à l'exécution. Le paquet Debian `tesseract-ocr` (voir Dockerfile) installe
# toujours le binaire à cet emplacement.
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

# Premier chemin de code de cette application à lancer un sous-processus
# (voir seccomp/README.md) : sans borne de temps explicite, un
# `subprocess.Popen` bloquant peut tourner indéfiniment sur une image
# pathologique/adversariale, contournant entièrement le filet
# `_check_detection_deadline` (qui ne s'exécute qu'APRÈS le retour de
# l'OCR) — même défaut de conception que celui déjà corrigé pour les appels
# Presidio (`timeout=30` explicite dans `_analyze_text`), désormais
# appliqué symétriquement ici.
MAX_OCR_SECONDS = int(os.environ.get("MAX_OCR_SECONDS", "60"))


def _open_and_validate_image(raw: bytes) -> "Image.Image":
    """
    Phase 1 - validation d'entrée pour une image PNG/JPEG uploadée : même
    exigence de sécurité que pour les images incrustées dans un PDF (voir
    _check_page_images_sane / CVE-2026-3308) — les dimensions déclarées sont
    lues AVANT tout décodage complet des pixels. `Image.open()` ne fait que
    lire l'en-tête du format (bloc IHDR pour PNG, marqueur SOF pour JPEG)
    pour déterminer `img.size` ; les données pixel compressées ne sont
    décodées qu'au premier accès réel (`.load()`, `.getdata()`...), jamais
    par `Image.open()` seul.
    """
    try:
        with warnings.catch_warnings():
            # Le DecompressionBombWarning intégré à Pillow ne couvre que la
            # zone 1x-2x le seuil (silencieux par défaut, pas une erreur) —
            # on impose notre propre rejet strict juste après pour ce cas
            # intermédiaire, au lieu de le laisser passer avec un simple
            # avertissement.
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(raw))
            width, height = img.size
            image_format = img.format
    except Image.DecompressionBombError as exc:
        raise HTTPException(
            status_code=400,
            detail="Cette image dépasse la limite de dimensions autorisée (protection anti-bombe de décompression).",
        ) from exc
    except (PILUnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise HTTPException(status_code=400, detail="Image illisible ou corrompue.") from exc

    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise HTTPException(
            status_code=400,
            detail=f"Cette image dépasse la limite de dimensions autorisée (max {MAX_IMAGE_PIXELS} pixels).",
        )
    if image_format not in ("PNG", "JPEG"):
        # Ne devrait jamais arriver : _detect_file_kind a déjà validé la
        # signature binaire — filet de sécurité si un jour un autre format
        # partageant une signature proche était mal aiguillé vers "image".
        raise HTTPException(status_code=400, detail="Format d'image non reconnu (seuls PNG et JPEG sont acceptés).")

    return img


def _run_ocr(img: "Image.Image") -> list[dict]:
    """
    Lance pytesseract.image_to_data (pas image_to_string) : renvoie le texte
    ET la position (bounding box en pixels) de chaque mot reconnu, avec
    lang="fra" — indispensable pour un texte français correctement reconnu
    (accents), voir Dockerfile pour le paquet de données linguistiques.
    Échec fermé : toute erreur du moteur OCR est une erreur propre (jamais
    une trace brute), jamais un verdict "aucune détection" implicite.
    `timeout=MAX_OCR_SECONDS` (voir plus haut) borne le sous-processus
    `tesseract` : au-delà, pytesseract le termine proprement (SIGTERM puis
    SIGKILL, voir sa fonction `kill()`) et lève une RuntimeError, jamais un
    processus zombie ou une requête bloquée indéfiniment.
    """
    try:
        data = pytesseract.image_to_data(
            img, lang=OCR_LANGUAGE, output_type=pytesseract.Output.DICT, timeout=MAX_OCR_SECONDS
        )
    except pytesseract.TesseractNotFoundError as exc:
        log.error("Binaire tesseract introuvable : %s", exc)
        raise HTTPException(status_code=503, detail="Le moteur OCR est indisponible, veuillez réessayer plus tard.") from exc
    except pytesseract.TesseractError as exc:
        log.warning("Échec de l'OCR : %s", exc)
        raise HTTPException(status_code=400, detail="Cette image n'a pas pu être analysée par l'OCR.") from exc
    except RuntimeError as exc:
        # pytesseract lève une RuntimeError nue (pas TesseractError, capturée
        # ci-dessus séparément bien qu'elle en hérite) avec le message fixe
        # "Tesseract process timeout" en cas de dépassement — voir
        # pytesseract.timeout_manager.
        log.warning("Timeout OCR après %ss : %s", MAX_OCR_SECONDS, exc)
        raise HTTPException(
            status_code=400,
            detail=f"Cette image est trop complexe pour être analysée par l'OCR dans le temps imparti (max {MAX_OCR_SECONDS}s).",
        ) from exc

    words: list[dict] = []
    for i in range(len(data.get("text", []))):
        text = data["text"][i]
        if not text or not text.strip():
            # Tesseract renvoie aussi des lignes de bounding box pour des
            # niveaux structurels (bloc/paragraphe/ligne) sans texte propre —
            # on ne garde que les mots effectivement reconnus.
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
    Reconstruit le texte complet à partir des mots OCR (séparateur espace au
    sein d'une même ligne, saut de ligne entre deux lignes/paragraphes/blocs
    différents), en gardant pour chaque mot son empan de caractères (start,
    end) dans ce texte reconstruit — permet de retrouver ensuite la ou les
    bounding box correspondant à une entité détectée par offset de caractère
    (voir _map_entities_to_word_boxes), même principe que le mapping
    page/rectangle déjà fait pour le PDF.
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
    Convertit chaque entité détectée (offset de caractères dans le texte OCR
    reconstruit) en une ou plusieurs détections en coordonnées pixels — une
    entité peut recouvrir plusieurs mots OCR (ex: "Jean Durand"), chacun
    produisant sa propre bounding box, regroupées ensuite par
    _cluster_detections comme pour le PDF.
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
    Détecte les entités sensibles dans une image sans les caviarder : OCR
    (voir _run_ocr) puis reconstruction du texte (_build_ocr_text) passée
    par la fonction d'analyse EXISTANTE du projet (_analyze_text — même
    appel Presidio, même système de thèmes, même moteur patché) avant de
    remapper chaque entité vers sa/ses bounding box (_map_entities_to_word_boxes).
    Ne réimplémente aucun appel Presidio séparé. Gère nativement le cas
    "aucun texte reconnu" : renvoie simplement une liste vide, sans erreur.
    """
    detection_start = time.time()
    words = _run_ocr(img)
    if not words:
        return []
    _check_detection_deadline(detection_start)

    text, spans = _build_ocr_text(words)
    # Mêmes normalisations que pour le PDF (aide le NER sur les mots tout en
    # majuscules et les tirets typographiques) — préservent la longueur du
    # texte caractère pour caractère, donc les offsets restent valides pour
    # remapper vers les spans calculés sur le texte original.
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
    "Accepte" toutes les révisions de suivi des modifications dans un
    élément racine XML donné : les insertions (w:ins) sont dépaquetées (le
    texte inséré devient un run normal), les suppressions (w:del) et
    déplacements sources (w:moveFrom) sont retirés entièrement.

    Nécessaire car python-docx n'expose aucune API haut niveau pour ça —
    et surtout parce que le texte d'une suppression trackée reste sinon
    présent indéfiniment dans le XML, invisible à `paragraph.runs` et donc
    à tout le reste du pipeline de détection (fuite confirmée par test lors
    de l'audit de sécurité : un nom et une date supprimés en mode suivi
    survivaient intégralement jusqu'au fichier "anonymisé" final).
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
    Déplie tous les <w:hyperlink> d'un élément racine XML donné : le texte
    affiché redevient un run normal (donc analysé par le pipeline de
    détection comme n'importe quel texte, `paragraph.runs` ne descendant
    pas dans un <w:hyperlink> — fuite confirmée par test), et la relation
    vers la cible (URL/mailto, potentiellement porteuse d'une donnée
    identifiante) est supprimée — pas seulement débranchée du corps du
    texte : la cible restait sinon présente et extractible dans les
    relations du fichier même après suppression du lien visible.
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
                pass  # relation déjà absente/partagée, rien à faire
    return unwrapped, dropped_rels


def _wipe_comments(document: WordDocument) -> int:
    """
    Retire tous les commentaires d'un document : les marqueurs dans le
    corps (w:commentRangeStart/End, le run contenant w:commentReference) et
    la ou les parties de commentaires elles-mêmes. Aucune API de
    suppression n'existe dans python-docx (seulement add_comment) — retrait
    entier plutôt que caviardage du contenu : même un commentaire
    partiellement masqué révèlerait qu'une discussion interne visait une
    personne précise, une fuite partielle de la forme de l'échange même
    sans le nom.
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
    Retire la miniature de document (docProps/thumbnail.jpeg), jamais
    analysée par le pipeline (texte uniquement) : un .docx réellement
    enregistré par Word (contrairement à un fixture généré par
    python-docx) peut y embarquer un rendu réel de la première page en
    pixels, si l'option "Enregistrer la vignette" a été active à un
    moment — potentiellement du texte identifiant visible en image, jamais
    lu ni caviardé par ailleurs. La relation vers cette partie est stockée
    au niveau racine du paquet (_rels/.rels, reltype "metadata/thumbnail"),
    pas dans word/_rels/document.xml.rels comme les relations habituelles
    du corps — d'où l'accès via document.part.package.rels plutôt que
    document.part.rels. Retrait entier (même logique que _wipe_comments) :
    ce n'est pas du texte analysable, la seule protection sûre est de ne
    pas transporter cette partie dans le fichier de sortie. Vérifié que la
    partie disparaît bien du zip de sortie et que le document reste
    ouvrable.
    """
    package = document.part.package
    to_drop = [rid for rid, rel in list(package.rels.items()) if rel.reltype == THUMBNAIL_RELTYPE]
    for rid in to_drop:
        del package.rels[rid]
    return len(to_drop)


def _wipe_core_properties(document: WordDocument) -> int:
    """
    Vide les champs de métadonnées susceptibles de porter une identité
    (auteur, dernier modificateur, commentaire/sujet/mots-clés/catégorie du
    document). Contrairement au corps du texte, ce sont des champs
    structurés dont la nature est connue par convention — inutile de les
    faire passer par le NER, on sait déjà que "auteur" est toujours un nom.
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
    Renvoie (part, root) pour la partie notes de bas de page/de fin, ou
    (None, None) si absente. `root` est un arbre lxml fraîchement analysé
    depuis part.blob — cette partie n'est pas un XmlPart standard (pas
    d'attribut .element, pas de vue live), donc toute modification doit
    être réécrite explicitement dans part._blob avant la sauvegarde, voir
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
    Renvoie la liste des parties image distinctes référencées depuis le
    corps, les en-têtes/pieds de page et les notes de bas de page/de fin —
    mêmes parties déjà traversées pour le texte, voir _iter_docx_paragraphs.
    Couvre aussi bien les images "inline" (wp:inline) que flottantes
    (wp:anchor) : la recherche se fait au niveau des relations OPC
    (reltype image), pas via document.inline_shapes qui n'expose que le
    premier cas.

    Dédoublonné par partname (`/word/media/imageN.ext`) : une même image
    référencée deux fois (même relation réutilisée à deux endroits, ou deux
    relations distinctes pointant vers un objet identique après
    déduplication déjà faite par Word) n'apparaît qu'une fois côté révision.
    Conséquence assumée : cocher une telle image en caviarde bien toutes les
    occurrences en une fois (comportement sûr — sur-caviarder n'est jamais
    le problème, contrairement à l'inverse).
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
    Image 64x64 unie noire, générée à la volée avec PyMuPDF (déjà une
    dépendance du projet — pas besoin de Pillow). Word redimensionne
    l'affichage selon les dimensions déclarées dans le XML du document
    (<a:ext cx=".." cy="..">), la taille en pixels du fichier de
    remplacement n'a donc pas besoin de correspondre à l'original.

    Réencodée dans le même format que l'original pour png/jpeg (l'immense
    majorité des captures d'écran/photos collées). Pour tout autre format
    (gif/bmp/tiff/wmf/emf...), un PNG est utilisé malgré tout, SANS changer
    la déclaration de type de la partie — mismatch assumé, documenté : rare
    en pratique pour ce cas d'usage, et la garantie de sécurité (octets
    d'origine non récupérables) tient dans tous les cas ; seule la fidélité
    de rendu dans Word peut en pâtir pour ces formats exotiques.
    """
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 64, 64))
    pix.set_rect(pix.irect, (0, 0, 0))
    fmt = _DOCX_IMAGE_PLACEHOLDER_FORMATS.get((content_type or "").lower(), "png")
    return pix.tobytes(fmt)


def _apply_docx_image_redactions(document: WordDocument, redacted_partnames: set) -> int:
    """
    Remplace le contenu binaire de chaque image sélectionnée par un carré
    noir uni — même garantie que le caviardage manuel PDF
    (_apply_manual_redactions) : l'octet d'origine ne doit survivre nulle
    part dans le fichier de sortie. Contrairement au PDF (sauvegarde
    incrémentale par défaut, nécessitant garbage=4/clean=True pour purger
    les objets pré-caviardage), l'écriture DOCX/OPC réécrit chaque partie
    une seule fois à partir de son blob courant (OpcPackage.save via
    iter_parts, dérivé du graphe de relations vivant) — aucune purge
    additionnelle nécessaire ici.

    Granularité IMAGE ENTIÈRE, pas une zone pixel précise comme pour le PDF
    : DOCX n'expose pas de mise en page fixe en coordonnées sans un moteur
    de rendu complet, tracer un rectangle précis n'est pas possible ici.
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
    Construit la section "images du document" de l'écran de révision DOCX :
    une case à cocher par image distincte, DÉCOCHÉE par défaut — mécanisme
    manuel et opt-in, même logique que les zones manuelles PDF (rien n'est
    caviardé sans action explicite de l'utilisateur, puisque ces images ne
    sont pas analysées par le NER, voir avertissement affiché juste avant).
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
                f'text-align:center;">Aperçu indisponible<br>({size_kb:.0f} Ko)</div>'
            )
        cards.append(f"""
        <label style="display:inline-block; margin:8px 12px 8px 0; text-align:center; cursor:pointer; vertical-align:top;">
          {preview}
          <div style="margin-top:4px; font-size:0.85em;">
            <input type="checkbox" class="docx-image-checkbox" data-image-id="{image_id}">
            Caviarder <span style="color:#888;">({html.escape(content_type)}, {size_kb:.0f} Ko)</span>
          </div>
        </label>
        """)

    return f"""
    <div style="margin: 0 0 16px 0; padding:12px; background:#f8f8f8; border-radius:4px;">
      <p style="margin-top:0;"><strong>{len(parts)}</strong> image(s) incrustée(s) trouvée(s) dans ce document
      (corps, en-têtes/pieds de page, notes) — non analysées automatiquement (voir avertissement ci-dessus).
      Cochez celles à remplacer par un carré noir avant de valider.</p>
      {"".join(cards)}
    </div>
    """


def _save_note_parts(note_parts: dict) -> None:
    """Réécrit le blob des parties notes de bas de page/de fin après
    modification de leurs paragraphes — nécessaire avant document.save(),
    voir _get_note_part."""
    for part, root in note_parts.values():
        part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _iter_docx_paragraphs(document: WordDocument):
    """
    Parcourt tous les paragraphes d'un document dans un ordre déterministe
    et STABLE entre deux ouvertures successives du même fichier
    (indispensable pour retrouver les mêmes indices de bloc entre la
    détection et la finalisation).

    Couvre : corps, cellules de tableaux (récursif), en-têtes et pieds de
    page de chaque section, notes de bas de page et de fin. Sur CHAQUE
    partie traversée (corps, en-têtes/pieds de page, notes), le suivi des
    modifications est aplati et les hyperliens sont dépliés AVANT
    extraction des paragraphes — voir _flatten_revisions_in /
    _unwrap_hyperlinks_in pour le pourquoi (fuites confirmées par
    l'audit de sécurité, invisibles à `paragraph.runs` sinon).

    Renvoie (blocks, note_parts) :
      - blocks : liste [(label, paragraph, table_ref), ...].
      - note_parts : dict {"footnote"|"endnote": (part, root)} pour les
        parties dont le contenu a été inclus — À REPASSER À
        _save_note_parts après édition des runs, avant document.save().

    LIMITATION CONNUE : n'inclut pas le texte des zones de texte, formes,
    SmartArt ou objets OLE incrustés, ni les images incrustées dans le
    corps (`<w:drawing>`) — python-docx ne l'expose pas / ce n'est pas du
    texte analysable par le NER. Les commentaires, métadonnées et la
    miniature de document ne sont pas caviardés mais entièrement retirés
    en amont côté finalisation (_wipe_comments / _wipe_core_properties /
    _wipe_docx_thumbnail), donc jamais présentés en révision non plus.
    """
    blocks = []

    _flatten_revisions_in(document.element)
    _unwrap_hyperlinks_in(document.element, document.part)

    for p in document.paragraphs:
        blocks.append(("corps", p, None))

    for table in document.tables:
        table_id = id(table)
        for row_idx, row in enumerate(table.rows):
            for col_idx, cell in enumerate(row.cells):
                for p in cell.paragraphs:
                    blocks.append(("tableau", p, (table_id, row_idx, col_idx)))
                for nested_table in cell.tables:
                    for nested_row in nested_table.rows:
                        for nested_cell in nested_row.cells:
                            for p in nested_cell.paragraphs:
                                blocks.append(("tableau", p, None))

    for section in document.sections:
        if section.header is not None:
            _flatten_revisions_in(section.header.part.element)
            _unwrap_hyperlinks_in(section.header.part.element, section.header.part)
            for p in section.header.paragraphs:
                blocks.append(("en-tête", p, None))
        if section.footer is not None:
            _flatten_revisions_in(section.footer.part.element)
            _unwrap_hyperlinks_in(section.footer.part.element, section.footer.part)
            for p in section.footer.paragraphs:
                blocks.append(("pied de page", p, None))

    note_parts = {}
    note_labels = {"footnote": "note de bas de page", "endnote": "note de fin"}
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
    Regroupe les détections dont les plages de caractères se chevauchent, au
    sein d'un même bloc — équivalent 1D du regroupement par IoU utilisé pour
    le PDF (_cluster_detections), pour la même raison : éviter qu'une zone
    cliquable en cache une autre invisible au même endroit.
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


# Un fragment isolé (une cellule de tableau, un en-tête de colonne, une
# ligne "Nom : Dupont" sans le reste de la phrase) prive le modèle NER du
# contexte qui l'aide à trancher entre "nom propre" et "mot ordinaire
# capitalisé" — c'est ce qui cause à la fois des faux négatifs (un nom
# réellement présent mais non détecté, faute de contexte) et des faux
# positifs (un mot ordinaire en début de cellule, capitalisé par
# _normalize_allcaps, pris pour un nom). On regroupe donc plusieurs blocs
# contigus dans un même appel à Presidio plutôt que de les analyser un par
# un, pour reconstituer un effet "page complète" comparable à ce qui
# fonctionne bien pour le PDF.
TEXT_CHUNK_MAX_CHARS = 8000
TEXT_CHUNK_MAX_BLOCKS = 200


# Mots-clés d'en-tête de colonne (première ligne d'un tableau DOCX ou d'un
# CSV) associés directement à un type d'entité : une correspondance ici
# déclenche le caviardage de TOUTE la colonne par règle structurelle, sans
# dépendre du NER. Nécessaire car une cellule isolée ("Jean Durand"
# seul, sans phrase autour) n'offre aucun contexte grammatical au modèle
# NER pour la reconnaître comme un nom — la structure du tableau (l'en-tête
# de colonne) est ici un signal bien plus fiable que le NER générique.
# Personnalisable/étendu par document sans toucher au code : ajouter une clé
# "column_entity_keywords" (dict mot-clé -> type d'entité) dans common.json
# ou dans un thème spécifique (le thème a priorité sur les valeurs communes).
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
    """Charge les mots-clés de colonne par défaut, complétés/écrasés par une
    éventuelle clé "column_entity_keywords" dans common.json (mêmes
    conventions que _load_common_recognizers)."""
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
    """Mots-clés de colonne effectifs pour un thème donné : les valeurs
    communes, éventuellement complétées/écrasées par celles du thème."""
    keywords = dict(COLUMN_ENTITY_KEYWORDS)
    if theme and theme.get("column_entity_keywords"):
        keywords.update({k.lower(): v for k, v in theme["column_entity_keywords"].items()})
    return keywords


MAX_LABEL_CHARS = 40  # une vraie étiquette de champ ("Nom", "Date de naissance") est courte ;
                       # un long paragraphe de description n'en est jamais une, même s'il
                       # contient par hasard une sous-chaîne d'un mot-clé (ex: "nom" dans
                       # "dénomination" ou "installation").


def _match_column_keyword(text: str, column_keywords: dict[str, str]) -> str | None:
    """
    Vérifie si `text` correspond à un mot-clé de colonne connu, en exigeant :
      1. Une correspondance sur un MOT ENTIER (limites \\b), jamais une simple
         sous-chaîne — "nom" ne doit PAS matcher à l'intérieur de
         "dénomination", "installation" ou "anonyme".
      2. Un texte candidat suffisamment court (MAX_LABEL_CHARS) pour être
         plausiblement une étiquette de champ, pas un paragraphe de
         description qui contiendrait le mot par hasard.
    Corrige un bug réel observé en production : sans ces deux garde-fous, un
    tableau dont la première cellule d'une ligne est une longue description
    (au lieu d'une étiquette) pouvait faire caviarder à tort toute la ligne
    (dates, cases à cocher...) dès qu'elle contenait la séquence de lettres
    d'un mot-clé n'importe où dans le texte.
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
    Détection structurelle par tableau, indépendante du NER, avec deux
    motifs reconnus (un tableau ne relève que d'un seul des deux) :

      1. EN-TÊTE DE COLONNES : la ligne 0 a au moins 2 colonnes dont
         l'intitulé correspond à un mot-clé connu (ex: "Nom | Prénom | Ville"
         en ligne 0, suivi de plusieurs lignes de données) -> toute la
         colonne est caviardée sur les lignes suivantes.
      2. ÉTIQUETTE : VALEUR PAR LIGNE : chaque ligne porte sa propre
         étiquette en première cellule non vide (ex: une ligne "Nom" -> une
         valeur, une autre ligne "Prénom" -> une autre valeur) — motif très
         courant dans les formulaires/certificats, une information par
         ligne plutôt qu'un tableau de plusieurs enregistrements. Les
         cellules suivantes de CETTE ligne sont alors caviardées.

    Le motif 1 est tenté en premier ; le motif 2 ne s'applique qu'aux
    tableaux qui n'ont pas été reconnus comme "en-tête de colonnes", pour
    qu'une vraie ligne d'en-tête ne soit jamais interprétée comme une
    étiquette (qui caviarderait alors les autres en-têtes de la même ligne).
    Voir _match_column_keyword pour les garde-fous anti-faux-positifs.
    """
    if not column_keywords:
        return []

    cells: dict[tuple, list[int]] = {}
    for block_id, (label, _, table_ref) in enumerate(blocks):
        if label == "tableau" and table_ref is not None:
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
        # --- Motif 1 : en-tête de colonnes sur la ligne 0 ---
        header_matches: dict[int, str] = {}
        for col_idx, block_ids in rows.get(0, {}).items():
            entity_type = match_keyword(cell_text(block_ids))
            if entity_type:
                header_matches[col_idx] = entity_type

        # Un en-tête sans aucune ligne de données en dessous n'est pas un
        # vrai tableau "en-tête + enregistrements" — c'est très probablement
        # une ligne unique étiquette:valeur (motif 2) où 2 colonnes matchent
        # un mot-clé par coïncidence (ex: "Nom" et "Date" sur la même ligne).
        # Sans ce garde-fou, motif 1 s'attribuerait le tableau à tort et ne
        # produirait aucune détection utile (rien à faire sur les lignes
        # suivantes puisqu'il n'y en a pas).
        if len(header_matches) >= 2 and len(rows) >= 2:
            for row_idx, cols in rows.items():
                if row_idx == 0:
                    continue
                for col_idx, block_ids in cols.items():
                    entity_type = header_matches.get(col_idx)
                    if entity_type:
                        for block_id in block_ids:
                            add_detection(detections, block_id, entity_type)
            continue  # tableau déjà traité par le motif 1

        # --- Motif 2 : étiquette : valeur, une OU PLUSIEURS paires par ligne ---
        # Une ligne peut contenir plusieurs champs côte à côte (ex: "Nom: X
        # Date: Y" dans une seule ligne de tableau) — on balaye la ligne dans
        # l'ordre des colonnes et on change d'étiquette dès qu'une nouvelle
        # cellule correspond à un mot-clé, plutôt que de supposer une seule
        # étiquette pour toute la ligne (bug réel observé : "Date" et sa
        # valeur se faisaient absorber à tort dans la détection de "Nom").
        for row_idx, cols in rows.items():
            sorted_cols = sorted(cols.items())
            current_entity_type: str | None = None
            for col_idx, block_ids in sorted_cols:
                text = cell_text(block_ids)
                if len(text) >= 2:
                    matched = match_keyword(text)
                    if matched:
                        current_entity_type = matched
                        continue  # cette cellule EST l'étiquette, pas une valeur
                if current_entity_type:
                    for block_id in block_ids:
                        add_detection(detections, block_id, current_entity_type)

    return detections


def _csv_structural_entities(rows: list[list[str]], column_keywords: dict[str, str]) -> list[dict]:
    """Équivalent CSV de _docx_table_structural_entities, mêmes deux motifs
    (en-tête de colonnes multiples en ligne 0, ou étiquette:valeur par ligne
    avec une seule colonne reconnue en première position)."""
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
    Détection générique sur une liste de blocs de texte indexés (paragraphes
    DOCX ou cellules CSV) — même logique à deux passes que _detect_pdf
    (détection standard par lots, puis propagation des noms/villes confirmés
    vers le reste du document), mais en 1D (offsets caractère) plutôt qu'en
    rectangles PDF. Voir TEXT_CHUNK_MAX_CHARS ci-dessus pour le pourquoi du
    regroupement en lots.
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
            pos += len(text) + 1  # +1 pour le séparateur "\n" inséré ci-dessous

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
                break  # un intervalle appartient à un seul bloc, inutile de continuer

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
    Regroupe les détections confirmées (non exclues) par bloc en intervalles
    fusionnés — nécessaire pour éditer le texte sans chevauchement — tout en
    conservant le compte par type d'entité pour le résumé (une même zone
    détectée par deux reconnaisseurs différents compte deux fois, comme pour
    le PDF).
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
    Remplace réellement le texte des runs couverts par `intervals` (offsets
    dans le texte concaténé du paragraphe) par REDACTION_MARKER — modifie le
    XML sous-jacent (run.text), pas un habillage visuel.

    Une même entité logique peut être partagée entre plusieurs runs (mise en
    forme mixte au milieu d'un mot, fréquent avec les vérificateurs
    orthographiques ou après un copier-coller) : dans ce cas, UN SEUL
    REDACTION_MARKER est inséré, dans le premier run touché — les runs
    suivants touchés par la MÊME entité ont leur portion simplement
    supprimée (chaîne vide), jamais un second marqueur. Sans cette règle, une
    entité étalée sur 2 runs produirait deux marqueurs concaténés
    ("[MASQUÉ][MASQUÉ]") au lieu d'un seul — bug corrigé après observation
    en usage réel (artefacts "[MASQUÉ]]]" causés par des runs mal découpés).
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

    # ops[i] : liste de (local_start, local_end, remplacement) pour le run i.
    # `remplacement` vaut REDACTION_MARKER sur le premier run touché par un
    # intervalle donné, "" pour les runs suivants du même intervalle.
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
    """Découpe `text` en segments et insère un <span> cliquable pour chaque
    cluster (zone détectée), dans l'ordre, sans chevauchement (garanti par
    _cluster_text_detections). Le texte est échappé HTML avant insertion."""
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
    """Gabarit de révision commun DOCX/CSV : surlignage inline plutôt que
    rendu image, pas de tracé manuel de zone pixel-précise comme pour le PDF
    (voir limitation documentée) — `images_html` (DOCX uniquement) permet en
    revanche de caviarder une image incrustée entière, voir
    _build_docx_images_review_section."""
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="fr">
    <head>
      <meta charset="utf-8">
      <title>Révision - Anonymiseur</title>
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
        <p><a href="/">&larr; Recommencer</a></p>
        <p>
          <strong>{total_detections}</strong> zone(s) détectée(s), surlignées en rouge.
          Cliquez sur une zone pour <strong>l'exclure</strong> du caviardage (elle passera en vert pointillé) —
          si la même personne apparaît ailleurs dans le document, toutes ses occurrences seront exclues en même temps.
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
            Valider et caviarder
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
    Exposition Prometheus (voir metrics.py). Ne passe PAS par
    l'authentification oauth2-proxy — un scraper Prometheus ne fait pas de
    dance OAuth — mais reste protégé par ipallowlist (voir le router
    Traefik dédié `app-metrics` dans docker-compose.yml, restreint par
    défaut à 127.0.0.1) plutôt que laissé ouvert à tout Internet.
    """
    body, content_type = metrics.metrics_response()
    return Response(content=body, media_type=content_type)


@app.get("/", response_class=HTMLResponse)
def upload_form():
    options = '<option value="">Aucun (détection générique uniquement)</option>'
    for key, theme in THEMES.items():
        options += f'<option value="{key}">{theme.get("label", key)}</option>'

    return f"""
    <!doctype html>
    <html lang="fr"><head><meta charset="utf-8"><title>Anonymiseur de documents</title></head>
    <body style="font-family: sans-serif; max-width: 600px; margin: 40px auto;">
      <h1>Anonymiseur de documents (PDF, DOCX, CSV, image)</h1>
      <p>Déposez un document. Vous pourrez vérifier et ajuster les zones détectées avant le caviardage final.</p>
      <form action="/api/detect" method="post" enctype="multipart/form-data">
        <p>
          <label for="theme">Type de document :</label><br>
          <select name="theme" id="theme">{options}</select>
        </p>
        <input type="file" name="file" accept=".pdf,.docx,.csv,.png,.jpg,.jpeg" required>
        <button type="submit">Analyser</button>
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
    Phase 1 du flux avec révision, commune aux quatre formats acceptés
    (PDF/DOCX/CSV/image) : détecte les entités sans les caviarder, stocke le
    job en mémoire, renvoie une page de révision (rendu image + zones
    cliquables en pixels pour le PDF et l'image ; surlignage inline pour
    DOCX/CSV — voir _build_text_review_page). Le type réel du fichier est
    déterminé à partir de son contenu binaire (_detect_file_kind), jamais du
    Content-Type déclaré par le client, qui est falsifiable.
    """
    raw = await file.read()

    size_mb = len(raw) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        raise HTTPException(
            status_code=413,
            detail=f"Fichier trop volumineux ({size_mb:.1f} Mo, max {MAX_UPLOAD_MB} Mo)",
        )

    filename_hash = hashlib.sha256((file.filename or "").encode()).hexdigest()[:12]

    _run_antivirus_scan(raw, file.filename or "", filename_hash)

    kind = _detect_file_kind(raw)

    # Quota global de jobs en attente de révision (indépendant du débit
    # limité par Traefik) : empêche l'accumulation de documents bruts en
    # mémoire au-delà d'un seuil sûr, même en restant sous la limite de
    # débit par IP.
    with _PENDING_JOBS_LOCK:
        if len(PENDING_JOBS) >= MAX_PENDING_JOBS:
            raise HTTPException(
                status_code=503,
                detail="Trop de documents en attente de révision actuellement, réessaie dans quelques minutes",
            )

    # Le champ `theme` est un champ de formulaire librement contrôlé par le
    # client (falsifiable, non borné, non contraint à un thème connu). Il
    # rejoint ensuite le journal d'audit (`_record_audit_event(theme=...)`)
    # ET le nom du fichier de sortie (`theme_slug`). Sans traitement ici :
    #  - un caractère de formatage Unicode (RTL override U+202E, catégorie
    #    Cf) y passe intact et permet le même spoofing visuel du journal que
    #    celui déjà neutralisé sur `X-Auth-Request-Email` (audit section 3.5) ;
    #  - une valeur très longue produit un `theme_slug` dépassant la limite
    #    de longueur de nom de fichier du système (Errno 36), faisant échouer
    #    la finalisation avec un message trompeur ("fichier corrompu").
    # Assaini une fois, à la source, exactement comme `user_email` ci-dessous,
    # puis borné en longueur (un vrai thème est court : medical/it/compta).
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
    """Logique de détection PDF — inchangée par rapport à la version PDF-only,
    seulement extraite dans sa propre fonction pour permettre le dispatch."""
    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="PDF illisible ou corrompu") from exc

    if doc.needs_pass:
        doc.close()
        raise HTTPException(
            status_code=400,
            detail="Ce PDF est protégé par un mot de passe. Veuillez retirer la protection (ou fournir une version non protégée) avant de l'analyser.",
        )

    if len(doc) > MAX_PDF_PAGES:
        page_count = len(doc)
        doc.close()
        raise HTTPException(
            status_code=400,
            detail=f"Ce PDF contient trop de pages ({page_count}, max {MAX_PDF_PAGES}) — "
            "une taille de fichier faible ne garantit pas un nombre de pages raisonnable.",
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
        # PyMuPDF peut réussir à ouvrir un PDF (fitz.open ne lève rien) mais
        # échouer plus tard, en cours de traitement, sur une structure
        # invalide découverte tardivement (ex. cycle dans les ressources
        # d'un objet). Sans ce filet, l'exception brute remonte jusqu'à
        # Starlette et affiche sa page d'erreur générique non stylée — pas
        # dangereux en soi, mais une fuite d'information inutile (chemins
        # internes, nom de bibliothèque) pour un rejet qui reste, au fond,
        # un simple "PDF invalide".
        log.warning("Échec du traitement PDF après ouverture réussie : %s", exc)
        raise HTTPException(
            status_code=400,
            detail="Ce PDF est corrompu ou contient une structure invalide qui empêche son traitement.",
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
    """Construit le HTML des pages avec overlays cliquables (un par cluster,
    pas par détection brute — voir _cluster_detections pour le pourquoi).
    Commun au PDF (plusieurs pages) et à l'image (une seule "page" = l'image
    entière) — voir _build_pixel_review_page pour le gabarit englobant."""
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
    """Gabarit de révision commun PDF/image : rendu image + zones cliquables
    en pixels pour exclure une détection, ET tracé manuel d'une nouvelle zone
    (manual_zones) — contrairement au gabarit texte DOCX/CSV
    (_build_text_review_page), qui n'offre pas cette possibilité."""
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="fr">
    <head>
      <meta charset="utf-8">
      <title>Révision - Anonymiseur</title>
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
        <p><a href="/">&larr; Recommencer</a></p>
        <p>
          <strong>{total_detections}</strong> zone(s) détectée(s), surlignées en rouge.
          Cliquez sur une zone pour <strong>l'exclure</strong> du caviardage (elle passera en vert pointillé) —
          si la même personne apparaît ailleurs dans le document, toutes ses occurrences seront exclues en même temps.
        </p>
        <button type="button" id="manual-mode-btn" onclick="toggleManualMode()" style="
            padding:8px 16px; background:#e9ecef; border:1px solid #ccc;
            border-radius:4px; cursor:pointer; margin-bottom:8px;">
          ✏️ Ajouter une zone à masquer
        </button>
        <form id="finalize-form" action="/api/finalize" method="post">
          <input type="hidden" name="job_id" value="{job_id}">
          <input type="hidden" name="format" value="html">
          <input type="hidden" id="excluded_ids" name="excluded_ids" value="">
          <input type="hidden" id="manual_zones" name="manual_zones" value="[]">
          <button type="button" onclick="submitFinalize()" style="
              padding:10px 20px; background:#0d6efd; color:white; border:none;
              border-radius:4px; cursor:pointer; font-size:1em;">
            Valider et caviarder
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
            ? '✅ Mode ajout actif (clique-glisse sur le document)'
            : '✏️ Ajouter une zone à masquer';
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
              zoneEl.title = 'Zone manuelle - cliquez pour supprimer';
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
          // Une même personne peut être détectée plusieurs fois dans le
          // document (voir propagation côté serveur) — exclure une
          // occurrence exclut toutes les autres du même groupe, pour éviter
          // de devoir cliquer chaque occurrence individuellement.
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
    Détection image : validation d'entrée (_open_and_validate_image), OCR +
    détection PII (_detect_image), puis même écran de révision pixel que le
    PDF (_build_pixel_review_page/_build_page_containers_html), adapté à une
    image unique plutôt qu'à des pages multiples (une seule "page", index 0).
    """
    img = _open_and_validate_image(raw)
    try:
        img.load()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Image corrompue ou tronquée, impossible de la décoder entièrement.",
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
            detail="Cette image contient une structure invalide qui empêche son traitement.",
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
        raise HTTPException(status_code=400, detail="Document Word illisible ou corrompu") from exc

    try:
        blocks, _note_parts = _iter_docx_paragraphs(document)
        if len(blocks) > MAX_DOCX_PARAGRAPHS:
            raise HTTPException(
                status_code=400,
                detail=f"Document trop volumineux ({len(blocks)} paragraphes, max {MAX_DOCX_PARAGRAPHS})",
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
            detail="Ce document Word est corrompu ou contient une structure invalide qui empêche son traitement.",
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
        label_html = f'<span class="doc-block-label">{html.escape(label)}</span>' if label != "corps" else ""
        blocks_html_parts.append(f'<div class="doc-block">{label_html}{rendered}</div>')
        rendered_count += 1

    limitation_note = (
        '<p style="color:#a15c00; font-size:0.85em; background:#fff8e6; padding:8px 12px; '
        'border-radius:4px;">⚠️ Les zones de texte, formes, objets incrustés et SmartArt de ce '
        "document ne sont pas analysés par ce moteur (limite technique connue de python-docx) — "
        "à vérifier manuellement si le document en contient. Les images incrustées ne sont pas "
        "analysées non plus (aucune détection automatique de texte dans l'image), mais peuvent "
        "être remplacées entièrement par un carré noir ci-dessous si nécessaire — contrairement "
        "au PDF, il n'existe pas d'outil pour ne caviarder qu'une partie d'une image.</p>"
    )
    if truncated:
        limitation_note += (
            f'<p style="color:#a15c00; font-size:0.85em; background:#fff8e6; padding:8px 12px; '
            f'border-radius:4px;">⚠️ Aperçu limité aux {MAX_REVIEW_ROWS} premiers paragraphes non '
            f"vides — les suivants seront quand même entièrement analysés et caviardés à la "
            f"finalisation, ils ne sont simplement pas affichés ici.</p>"
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
            detail=f"Fichier CSV trop volumineux ({total_cells} cellules, max {MAX_CSV_CELLS})",
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
            detail="Ce fichier CSV contient une structure invalide qui empêche son traitement.",
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
        f'<p style="color:#a15c00; font-size:0.85em; background:#fff8e6; padding:8px 12px; '
        f'border-radius:4px;">⚠️ Aperçu limité aux {MAX_REVIEW_ROWS} premières lignes sur '
        f'{len(rows)} — les lignes au-delà de cet aperçu seront quand même entièrement '
        f'analysées et caviardées à la finalisation, elles ne sont simplement pas affichées '
        f"ici pour ne pas surcharger la page.</p>"
        if truncated else ""
    )

    return _build_text_review_page(
        job_id=job_id,
        total_detections=len(clusters),
        blocks_html=table_html,
        extra_note=truncation_note,
    )


def _request_user(request: Request) -> str:
    """Identité de l'appelant telle que garantie par oauth2-proxy via Traefik
    (`X-Auth-Request-Email`, remplacé — jamais transmis tel quel — par le
    forwardAuth). Même assainissement qu'à la création du job, pour que la
    comparaison soit exacte."""
    return _strip_unicode_control_and_format_chars(
        request.headers.get("x-auth-request-email", "inconnu")
    )


def _get_pending_job_for(job_id: str, request: Request, pop: bool = False) -> dict:
    """Retrouve un job en attente de révision **appartenant à l'appelant**.

    Jusqu'ici, connaître un `job_id` (uuid4, imprévisible, mais présent dans
    les logs d'accès Traefik et l'historique du navigateur) suffisait pour
    voir l'aperçu du document ORIGINAL (avant caviardage) de n'importe quel
    autre utilisateur et finaliser son job à sa place. La décision 1.18 de
    l'audit (pas de contrôle de propriétaire au téléchargement) reposait sur
    « fichier déjà caviardé » — argument sans objet ici. Un job d'un autre
    utilisateur est traité exactement comme un job inexistant (404), sans
    révéler son existence, et surtout sans le retirer de la file (`pop`
    seulement une fois la propriété confirmée) — sinon un tiers pourrait
    détruire le job en cours de révision d'un autre par simple tentative."""
    user = _request_user(request)
    with _PENDING_JOBS_LOCK:
        job = PENDING_JOBS.get(job_id)
        if job is None or job.get("user_email") != user:
            raise HTTPException(status_code=404, detail="Job introuvable ou expiré")
        if pop:
            PENDING_JOBS.pop(job_id, None)
            metrics.PENDING_JOBS.set(len(PENDING_JOBS))
    return job


@app.get("/api/preview_image/{job_id}/{page_index}")
def preview_image(job_id: str, page_index: int, request: Request):
    """Rend une page du PDF (ou l'image entière, pour un job image) en
    attente de révision — uniquement pour le propriétaire du job (aperçu du
    document ORIGINAL, voir _get_pending_job_for)."""
    job = _get_pending_job_for(job_id, request)

    if job.get("kind") == "image":
        if page_index != 0:
            raise HTTPException(status_code=404, detail="Page introuvable")
        # Octets bruts d'origine (déjà validés en Phase 1 à la détection) :
        # ceci est l'APERÇU pré-caviardage, exactement comme pour le PDF —
        # le caviardage réel n'a lieu qu'à la finalisation.
        media_type = "image/png" if job["image_format"] == "PNG" else "image/jpeg"
        return Response(content=job["raw_image"], media_type=media_type)

    if job.get("kind") != "pdf":
        raise HTTPException(status_code=400, detail="Aperçu image disponible uniquement pour les jobs PDF/image")

    doc = fitz.open(stream=job["raw_pdf"], filetype="pdf")
    try:
        if page_index < 0 or page_index >= len(doc):
            raise HTTPException(status_code=404, detail="Page introuvable")

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
            detail="Cette page contient une structure invalide qui empêche son aperçu.",
        ) from exc
    finally:
        doc.close()

    return Response(content=png_bytes, media_type="image/png")


def _apply_selected_redactions(doc: fitz.Document, detections: list[dict], excluded_ids: set) -> dict:
    """
    Applique le caviardage uniquement pour les détections dont l'id n'est
    PAS dans excluded_ids (celles que l'utilisateur a décochées en révision).
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
    Applique un caviardage sur des zones tracées manuellement par l'utilisateur
    (faux négatifs corrigés à la main). Les coordonnées reçues sont en pixels
    d'aperçu (display_rect), reconverties en coordonnées PDF via PREVIEW_ZOOM,
    exactement comme pour les détections automatiques.
    """
    count = 0
    by_page: dict[int, list] = {}
    for zone in manual_zones[:200]:  # garde-fou anti-abus
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
    Vide les métadonnées susceptibles de porter une identité (auteur,
    créateur/producteur, dates de création/modification, titre, sujet,
    mots-clés) et le paquet XMP associé. `apply_redactions()` ne touche
    qu'au contenu visible des pages ; sans ce nettoyage, le document
    "anonymisé" reste daté et attribué comme l'original — même constat de
    fond que pour DOCX (_wipe_core_properties), un champ structuré dont la
    nature est connue par convention, inutile de le faire passer par le NER.
    """
    doc.set_metadata({})
    if doc.xref_xml_metadata():
        doc.del_xml_metadata()


def _finalize_pdf_job(job: dict, job_id: str, excluded_set: set, manual_zones_data: list) -> tuple[dict, Path, int]:
    """Logique de finalisation PDF."""
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
        # garbage=4 + clean=True : purge les objets devenus orphelins après
        # apply_redactions() (l'ancien contenu de page pré-caviardage n'est
        # jamais supprimé du fichier par défaut, seulement déréférencé —
        # voir constat de session vérifié sur caviar_test.pdf, texte original
        # en clair récupérable dans le fichier "anonymisé" avec n'importe
        # quel outil qui parcourt tous les objets du PDF au lieu de suivre
        # uniquement l'arbre de pages courant).
        doc.save(output_path, garbage=4, clean=True, deflate=True)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Échec de la finalisation PDF (job %s) : %s", job_id, exc)
        raise HTTPException(
            status_code=400,
            detail="Ce PDF est corrompu ou contient une structure invalide qui empêche sa finalisation.",
        ) from exc
    finally:
        doc.close()
    return summary, output_path, manual_count


_IMAGE_MODES_KEPT_AS_IS = ("RGB", "RGBA", "L", "LA")


def _normalize_image_mode_for_editing(img: "Image.Image") -> "Image.Image":
    """
    Convertit vers un mode directement dessinable/encodable avant caviardage
    (ex: palette indexée "P", CMYK...) — la couleur de remplissage du
    rectangle de caviardage dépend du mode (voir _black_fill_for_mode), donc
    on normalise d'abord vers un petit ensemble de modes connus plutôt que
    de gérer tous les modes Pillow possibles.
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
    return (0, 0, 0)  # RGB et tout mode déjà normalisé vers RGB


def _apply_selected_image_redactions(
    draw: "ImageDraw.ImageDraw", mode: str, detections: list[dict], excluded_ids: set
) -> dict:
    """Dessine un rectangle plein opaque directement dans les pixels pour
    chaque détection non exclue — mêmes garanties que le caviardage PDF
    (redact_annot) : les pixels d'origine sous le rectangle sont écrasés,
    pas seulement recouverts par un calque."""
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
    """Applique un caviardage sur des zones tracées manuellement par
    l'utilisateur (faux négatifs corrigés à la main) — mêmes coordonnées
    pixels que l'aperçu (pas de zoom appliqué pour l'image, contrairement au
    PDF), même garde-fou anti-abus MAX_MANUAL_ZONES que pour le PDF."""
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
    Dépouille ENTIÈREMENT les métadonnées avant sauvegarde : EXIF complet (y
    compris coordonnées GPS et miniature EXIF intégrée), chunks de texte PNG
    (tEXt/zTXt/iTXt) et profil ICC.

    Approche volontairement radicale plutôt qu'un retrait champ par champ
    (EXIF, puis GPS, puis miniature, puis tEXt, puis ICC...) : reconstruire
    une image neuve à partir des seuls octets de pixels bruts
    (Image.frombytes) produit un objet dont le dictionnaire `.info` est vide
    par construction — aucun risque d'oublier un champ de métadonnées
    existant ou introduit par une future version de Pillow, y compris la
    miniature EXIF intégrée (embarquée dans les octets `exif` de `.info`,
    jamais recopiée ici). `.save()` n'ajoute par défaut ni exif, ni
    icc_profile, ni pnginfo/comment si on ne les passe pas explicitement.
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
    """Logique de finalisation image : réouverture depuis les octets bruts
    stockés dans le job (jamais l'objet Pillow de la phase de détection),
    caviardage réel par rectangles pleins dans les pixels, puis dépouillement
    complet des métadonnées avant écriture du fichier de sortie."""
    try:
        img = Image.open(io.BytesIO(job["raw_image"]))
        img.load()
    except Exception as exc:
        log.warning("Échec de la finalisation image (job %s) : %s", job_id, exc)
        raise HTTPException(
            status_code=400,
            detail="Cette image est corrompue ou contient une structure invalide qui empêche sa finalisation.",
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
            detail="Cette image est corrompue ou contient une structure invalide qui empêche sa finalisation.",
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

        # Zones structurelles retirées entièrement plutôt que caviardées
        # (voir _wipe_comments/_wipe_core_properties) : uniquement à la
        # finalisation, puisqu'elles ne sont ni analysées ni présentées à
        # l'écran de révision.
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
            detail="Ce document Word est corrompu ou contient une structure invalide qui empêche sa finalisation.",
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
        # utf-8-sig (BOM) : Excel FR affiche correctement les accents sans
        # que l'utilisateur ait à choisir manuellement l'encodage à l'import.
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
            detail="Ce fichier CSV est corrompu ou contient une structure invalide qui empêche sa finalisation.",
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
    Phase 2 du flux avec révision, commune aux trois formats : applique le
    caviardage uniquement sur les détections que l'utilisateur n'a pas
    exclues (plus les zones manuelles pour le PDF et les images entières
    sélectionnées pour le DOCX — voir _apply_manual_redactions /
    _apply_docx_image_redactions, non proposé pour le CSV, format texte pur
    sans conteneur d'image), produit le fichier final dans son format
    d'origine.
    """
    # Propriété vérifiée AVANT de retirer le job de la file (voir
    # _get_pending_job_for) : un tiers ne peut ni finaliser ni détruire le
    # job en cours de révision d'un autre utilisateur.
    job = _get_pending_job_for(job_id, request, pop=True)

    excluded_cluster_ids = {i for i in excluded_ids.split(",") if i}

    # excluded_ids reçus du navigateur sont des ids de CLUSTER (une zone
    # visible peut regrouper plusieurs détections superposées) — il faut les
    # étendre vers tous les ids de détection sous-jacents avant d'appliquer
    # le caviardage, sinon une détection invisible restée "cachée derrière"
    # une autre au même endroit continuerait d'être caviardée malgré le clic.
    excluded_set = set()
    for cluster_id in excluded_cluster_ids:
        excluded_set.update(job["clusters"].get(cluster_id, []))

    redacted_image_id_set = {i for i in redacted_image_ids.split(",") if i}

    try:
        manual_zones_data = json.loads(manual_zones)
        if not isinstance(manual_zones_data, list):
            manual_zones_data = []
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        # RecursionError : un tableau JSON profondément imbriqué ("[[[[...")
        # d'à peine ~200 Ko (donc SOUS la limite de taille de partie
        # multipart de Starlette, 1 Mo) fait dépasser la profondeur de
        # récursion du décodeur `json`. Non couverte par JSONDecodeError,
        # elle remontait jusqu'à un 500 générique non maîtrisé (fuite d'une
        # trace Starlette). Traitée comme une entrée malformée ordinaire :
        # aucune zone manuelle, le reste de la finalisation se poursuit.
        manual_zones_data = []

    # Ces champs de formulaire ne passent pas par le contrôle MAX_UPLOAD_MB
    # (qui ne s'applique qu'au fichier d'origine) — on plafonne explicitement
    # leur taille pour éviter qu'un client buggé ou malveillant fasse
    # consommer du CPU/mémoire disproportionné au parsing/à l'application.
    if len(manual_zones_data) > MAX_MANUAL_ZONES:
        raise HTTPException(
            status_code=400,
            detail=f"Trop de zones manuelles ({len(manual_zones_data)}, max {MAX_MANUAL_ZONES})",
        )
    if len(excluded_cluster_ids) > MAX_EXCLUDED_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"Trop de zones exclues ({len(excluded_cluster_ids)}, max {MAX_EXCLUDED_IDS})",
        )
    if len(redacted_image_id_set) > MAX_DOCX_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Trop d'images sélectionnées ({len(redacted_image_id_set)}, max {MAX_DOCX_IMAGES})",
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
    else:  # pragma: no cover - défensif, ne devrait jamais arriver
        raise HTTPException(status_code=400, detail="Type de document inconnu")

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
        ) or "<tr><td colspan='2'>Aucune entité caviardée</td></tr>"

        ttl_minutes = FILE_TTL_SECONDS // 60
        excluded_note = (
            f"<p style='color:#555;'>{excluded_count} zone(s) exclue(s) manuellement lors de la révision.</p>"
            if excluded_count else ""
        )

        # L'aperçu inline ne fonctionne que pour le PDF (rendu natif du
        # navigateur via iframe) et l'image (balise <img> classique) — un
        # .docx/.csv ne s'affiche pas correctement inline, on propose
        # uniquement le téléchargement pour ces deux formats.
        if kind == "pdf":
            preview_html = (
                f'<h2>Aperçu (contrôle visuel)</h2>'
                f'<iframe src="{download_url}" style="width:100%; height:900px; border:1px solid #ccc;"></iframe>'
            )
        elif kind == "image":
            preview_html = (
                f'<h2>Aperçu (contrôle visuel)</h2>'
                f'<img src="{download_url}" style="max-width:100%; border:1px solid #ccc;">'
            )
        else:
            preview_html = ""

        return HTMLResponse(f"""
        <!doctype html>
        <html lang="fr">
        <head><meta charset="utf-8"><title>Résultat - Anonymiseur</title></head>
        <body style="font-family: sans-serif; max-width: 900px; margin: 40px auto;">
          <p><a href="/">&larr; Anonymiser un autre document</a></p>
          <h1>Document anonymisé</h1>

          <p>
            <strong>{total}</strong> élément(s) caviardé(s) au total.
            <a href="{download_url}" download style="
                display:inline-block; margin-left:1em; padding:8px 16px;
                background:#0d6efd; color:white; text-decoration:none;
                border-radius:4px;">
              Télécharger le document anonymisé
            </a>
          </p>
          {excluded_note}

          <table style="border-collapse: collapse; margin-bottom: 24px;">
            <thead>
              <tr>
                <th style="text-align:left; border-bottom:1px solid #ccc; padding:4px 12px 4px 0;">Type de donnée</th>
                <th style="text-align:right; border-bottom:1px solid #ccc; padding:4px 0 4px 12px;">Occurrences</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>

          <p style="color:#555; font-size:0.9em;">
            Le fichier sera automatiquement supprimé du serveur dans {ttl_minutes} minutes.
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
    # job_id est un uuid4().hex (caractères hexadécimaux uniquement), donc
    # sûr à utiliser dans un motif glob sans risque d'injection de chemin.
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=400, detail="Identifiant de job invalide")

    matches = list(WORKDIR.glob(f"{job_id}-*-anonymise.*"))
    if not matches:
        raise HTTPException(status_code=404, detail="Fichier introuvable ou déjà purgé")
    path = matches[0]

    extension = path.suffix.lower()
    media_type = _DOWNLOAD_MEDIA_TYPES.get(extension)
    if media_type is None:  # pragma: no cover - défensif, extension toujours connue en pratique
        raise HTTPException(status_code=500, detail="Format de fichier de sortie non reconnu")

    # Reconstruit un nom de téléchargement explicite (type de document +
    # référence courte) sans jamais exposer le nom du fichier original.
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
    Consultation du journal d'audit (les n dernières entrées, 50 par défaut).
    Ne contient jamais de contenu de document ni de nom de fichier en clair —
    uniquement qui, quand, quel thème, combien d'éléments caviardés.
    """
    n = max(1, min(n, 500))  # évite les valeurs négatives (slice incohérent) et les demandes excessives

    log_path = AUDIT_DIR / "audit.log"
    if not log_path.exists():
        return JSONResponse({"entries": []})

    try:
        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except OSError as exc:
        log.error("Lecture du journal d'audit impossible : %s", exc)
        raise HTTPException(status_code=500, detail="Journal d'audit temporairement indisponible") from exc

    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return JSONResponse({"entries": entries})
