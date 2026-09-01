"""
Anonymiseur de documents PDF - application de test (lab Proxmox)
-----------------------------------------------------------------
Flux :
  1. Upload d'un PDF
  2. Extraction du texte page par page (PyMuPDF)
  3. Détection des données sensibles via presidio-analyzer (par page)
  4. Localisation des entités trouvées dans la mise en page et caviardage
     réel (le texte sous-jacent est supprimé, pas juste masqué visuellement)
  5. presidio-anonymizer appelé sur le texte complet pour produire un
     rapport texte anonymisé (audit / mesure des faux négatifs)
  6. Renvoi du PDF caviardé + un résumé des types de données trouvées

Ce n'est pas un outil de production : pas de file d'attente, pas de retry,
gestion d'erreurs minimale. Suffisant pour valider le fonctionnel avant un
éventuel durcissement.
"""

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import fitz  # PyMuPDF
import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("anonymiseur")

ANALYZER_URL = os.environ.get("PRESIDIO_ANALYZER_URL", "http://presidio-analyzer:3000")
ANONYMIZER_URL = os.environ.get("PRESIDIO_ANONYMIZER_URL", "http://presidio-anonymizer:3000")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
FILE_TTL_SECONDS = int(os.environ.get("FILE_TTL_SECONDS", "600"))
JOB_REVIEW_TTL_SECONDS = int(os.environ.get("JOB_REVIEW_TTL_SECONDS", "900"))
LANGUAGE = os.environ.get("ANALYZER_LANGUAGE", "fr")

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


def _load_themes() -> dict:
    """Charge tous les fichiers de thèmes disponibles (app/themes/*.json)."""
    themes = {}
    for path in sorted(THEMES_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                themes[path.stem] = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Thème illisible, ignoré: %s (%s)", path.name, exc)
    return themes


THEMES = _load_themes()
log.info("Thèmes chargés: %s", list(THEMES.keys()))


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
    for path in WORKDIR.glob("*-anonymise.pdf"):
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
    if stale:
        log.info("Jobs en révision expirés purgés: %d", len(stale))


def _cleanup_sweep_loop(interval_seconds: int = 60):
    while True:
        time.sleep(interval_seconds)
        _sweep_orphaned_files()
        _sweep_stale_jobs()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Démarrage ---
    # Rattrape immédiatement les fichiers laissés par un précédent process
    # (crash, redéploiement) avant même le premier tour de boucle périodique.
    _sweep_orphaned_files()
    threading.Thread(target=_cleanup_sweep_loop, daemon=True).start()
    log.info("Balayage des fichiers orphelins démarré (contrôle toutes les 60s)")

    yield  # l'application tourne ici

    # --- Extinction ---
    log.info("Arrêt de l'application")


app = FastAPI(title="Anonymiseur de documents - PDF", lifespan=lifespan)

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
          <h1 style="margin-bottom:8px;">{title}</h1>
          <p style="color:#555; font-size:1.1em;">{exc.detail}</p>
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
    Convertit les mots tout en majuscules (ex: "CARROLAGGI") en casse titre
    ("Carrolaggi") pour aider le modèle NER à les reconnaître comme noms
    propres — spaCy s'appuie beaucoup sur la casse pour cette détection.

    Important: .capitalize() ne change jamais la longueur d'un mot, donc les
    positions (start/end) renvoyées par l'analyzer restent valides pour
    découper le texte ORIGINAL (non normalisé) utilisé pour le caviardage.
    """
    return re.sub(r"\b[A-ZÀ-Ý]{2,}\b", lambda m: m.group(0).capitalize(), text)


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
    if theme:
        if theme.get("ad_hoc_recognizers"):
            payload["ad_hoc_recognizers"] = theme["ad_hoc_recognizers"]
        if theme.get("allow_list"):
            payload["allow_list"] = theme["allow_list"]
        if theme.get("allow_list_match"):
            payload["allow_list_match"] = theme["allow_list_match"]
        if theme.get("score_threshold") is not None:
            payload["score_threshold"] = theme["score_threshold"]

    try:
        resp = requests.post(f"{ANALYZER_URL}/analyze", json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        log.error("Appel presidio-analyzer échoué: %s", exc)
        raise HTTPException(status_code=502, detail="Moteur d'analyse indisponible") from exc


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


def _detect_pdf(doc: fitz.Document, theme: dict | None = None) -> list[dict]:
    """
    Détecte les entités sensibles sans les caviarder. Pour chaque occurrence
    visuelle trouvée, renvoie à la fois sa position réelle dans le PDF (pour
    le caviardage final) et sa position à l'échelle de l'aperçu image (pour
    l'affichage cliquable côté navigateur) — les deux calculées avec la même
    matrice de zoom pour rester parfaitement alignées.
    """
    matrix = fitz.Matrix(PREVIEW_ZOOM, PREVIEW_ZOOM)
    detections: list[dict] = []

    for page_index, page in enumerate(doc):
        page_text = page.get_text()
        normalized_text = _normalize_allcaps(page_text)
        entities = _analyze_text(normalized_text, theme=theme)

        for entity in entities:
            entity_text = page_text[entity["start"] : entity["end"]]
            stripped = entity_text.strip()
            if len(stripped) < 3:
                continue

            entity_type = entity.get("entity_type", "UNKNOWN")
            for rect in page.search_for(entity_text):
                display_rect = rect * matrix
                detections.append(
                    {
                        "id": uuid.uuid4().hex[:12],
                        "page": page_index,
                        "entity_type": entity_type,
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
            clusters.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "page": page,
                    "display_rect": [x0, y0, x1, y1],
                    "entity_types": sorted({g["entity_type"] for g in group}),
                    "member_ids": [g["id"] for g in group],
                }
            )

    return clusters


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def upload_form():
    options = '<option value="">Aucun (détection générique uniquement)</option>'
    for key, theme in THEMES.items():
        options += f'<option value="{key}">{theme.get("label", key)}</option>'

    return f"""
    <!doctype html>
    <html lang="fr"><head><meta charset="utf-8"><title>Anonymiseur de documents</title></head>
    <body style="font-family: sans-serif; max-width: 600px; margin: 40px auto;">
      <h1>Anonymiseur de documents (PDF)</h1>
      <p>Déposez un PDF. Vous pourrez vérifier et ajuster les zones détectées avant le caviardage final.</p>
      <form action="/api/detect" method="post" enctype="multipart/form-data">
        <p>
          <label for="theme">Type de document :</label><br>
          <select name="theme" id="theme">{options}</select>
        </p>
        <input type="file" name="file" accept="application/pdf" required>
        <button type="submit">Analyser</button>
      </form>
    </body>
    </html>
    """


@app.post("/api/detect")
async def detect_pdf(
    request: Request,
    file: UploadFile = File(...),
    theme: str = Form(default=""),
):
    """
    Phase 1 du flux avec révision : détecte les entités sans les caviarder,
    stocke le job en mémoire, renvoie une page HTML avec les pages du PDF
    en image et les zones détectées surlignées et cliquables (clic = exclure
    du caviardage final). L'utilisateur peut aussi tracer manuellement de
    nouvelles zones à caviarder (faux négatifs corrigés à la main).
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés")

    raw = await file.read()

    # file.content_type est déclaré par le client donc falsifiable (un
    # fichier malveillant peut prétendre être un PDF). On vérifie la
    # signature binaire réelle en tête de fichier, seule preuve fiable.
    if not raw.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=400,
            detail="Le fichier ne correspond pas à un PDF valide (signature binaire absente)",
        )

    size_mb = len(raw) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        raise HTTPException(
            status_code=413,
            detail=f"Fichier trop volumineux ({size_mb:.1f} Mo, max {MAX_UPLOAD_MB} Mo)",
        )

    selected_theme = THEMES.get(theme) if theme else None
    if theme and selected_theme is None:
        log.warning("Thème inconnu demandé (%r), poursuite sans thème", theme)

    filename_hash = hashlib.sha256((file.filename or "").encode()).hexdigest()[:12]
    user_email = request.headers.get("x-auth-request-email", "inconnu")

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

    detections = _detect_pdf(doc, theme=selected_theme)
    clusters = _cluster_detections(detections)

    page_sizes = []
    for page in doc:
        r = page.rect
        page_sizes.append((round(r.width * PREVIEW_ZOOM), round(r.height * PREVIEW_ZOOM)))
    doc.close()

    job_id = uuid.uuid4().hex
    with _PENDING_JOBS_LOCK:
        PENDING_JOBS[job_id] = {
            "raw_pdf": raw,
            "detections": detections,
            "clusters": {c["id"]: c["member_ids"] for c in clusters},
            "theme": theme,
            "filename_hash": filename_hash,
            "user_email": user_email,
            "size_mb": size_mb,
            "created_at": time.time(),
        }

    log.info(
        "Job %s en révision: fichier#%s, %d détection(s) (%d zone(s) après regroupement), thème=%s",
        job_id, filename_hash, len(detections), len(clusters), theme or "aucun",
    )

    # Construit le HTML des pages avec overlays cliquables (un par cluster,
    # pas par détection brute — voir _cluster_detections pour le pourquoi)
    pages_html = []
    for page_index, (width, height) in enumerate(page_sizes):
        overlays = "".join(
            f'<div class="detection" data-id="{c["id"]}" '
            f'title="{", ".join(c["entity_types"])}" '
            f'style="left:{c["display_rect"][0]:.0f}px; top:{c["display_rect"][1]:.0f}px; '
            f'width:{c["display_rect"][2] - c["display_rect"][0]:.0f}px; '
            f'height:{c["display_rect"][3] - c["display_rect"][1]:.0f}px;" '
            f'onclick="this.classList.toggle(\'excluded\')"></div>'
            for c in clusters if c["page"] == page_index
        )
        pages_html.append(f"""
        <div class="page-container" data-page="{page_index}" style="position:relative; width:{width}px; height:{height}px; margin-bottom:16px;">
          <img src="/api/preview_image/{job_id}/{page_index}" width="{width}" height="{height}" style="display:block;">
          {overlays}
        </div>
        """)

    total_detections = len(clusters)

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
          Cliquez sur une zone pour <strong>l'exclure</strong> du caviardage (elle passera en vert pointillé).
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

      {''.join(pages_html)}

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


@app.get("/api/preview_image/{job_id}/{page_index}")
def preview_image(job_id: str, page_index: int):
    """Rend une page du PDF en attente de révision sous forme d'image PNG."""
    with _PENDING_JOBS_LOCK:
        job = PENDING_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job introuvable ou expiré")

    doc = fitz.open(stream=job["raw_pdf"], filetype="pdf")
    if page_index < 0 or page_index >= len(doc):
        doc.close()
        raise HTTPException(status_code=404, detail="Page introuvable")

    matrix = fitz.Matrix(PREVIEW_ZOOM, PREVIEW_ZOOM)
    pix = doc[page_index].get_pixmap(matrix=matrix)
    png_bytes = pix.tobytes("png")
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


@app.post("/api/finalize")
async def finalize_pdf(
    request: Request,
    job_id: str = Form(...),
    excluded_ids: str = Form(default=""),
    manual_zones: str = Form(default="[]"),
    response_format: str = Form(default="json", alias="format"),
):
    """
    Phase 2 du flux avec révision : applique le caviardage uniquement sur
    les détections que l'utilisateur n'a pas exclues, plus les zones
    ajoutées manuellement (faux négatifs corrigés à la main), produit le
    PDF final.
    """
    with _PENDING_JOBS_LOCK:
        job = PENDING_JOBS.pop(job_id, None)

    if job is None:
        raise HTTPException(status_code=404, detail="Job introuvable ou expiré, veuillez relancer l'analyse")

    excluded_cluster_ids = {i for i in excluded_ids.split(",") if i}

    # excluded_ids reçus du navigateur sont des ids de CLUSTER (une zone
    # visible peut regrouper plusieurs détections superposées) — il faut les
    # étendre vers tous les ids de détection sous-jacents avant d'appliquer
    # le caviardage, sinon une détection invisible restée "cachée derrière"
    # une autre au même endroit continuerait d'être caviardée malgré le clic.
    excluded_set = set()
    for cluster_id in excluded_cluster_ids:
        excluded_set.update(job["clusters"].get(cluster_id, []))

    try:
        manual_zones_data = json.loads(manual_zones)
        if not isinstance(manual_zones_data, list):
            manual_zones_data = []
    except (json.JSONDecodeError, TypeError):
        manual_zones_data = []

    doc = fitz.open(stream=job["raw_pdf"], filetype="pdf")
    summary = _apply_selected_redactions(doc, job["detections"], excluded_set)
    manual_count = _apply_manual_redactions(doc, manual_zones_data)
    if manual_count:
        summary["MANUEL"] = summary.get("MANUEL", 0) + manual_count

    theme = job["theme"]
    theme_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", theme) if theme else "document"
    output_path = WORKDIR / f"{job_id}-{theme_slug}-anonymise.pdf"
    doc.save(output_path)
    doc.close()

    total = sum(summary.values())
    excluded_count = len(excluded_cluster_ids)
    log.info(
        "Job %s finalisé: %d entité(s) caviardée(s), %d zone(s) exclue(s), %d zone(s) manuelle(s)",
        job_id, total, excluded_count, manual_count,
    )

    _record_audit_event(
        job_id=job_id,
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
              Télécharger le PDF anonymisé
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

          <h2>Aperçu (contrôle visuel)</h2>
          <p style="color:#555; font-size:0.9em;">
            Le fichier sera automatiquement supprimé du serveur dans {ttl_minutes} minutes.
          </p>
          <iframe src="{download_url}" style="width:100%; height:900px; border:1px solid #ccc;"></iframe>
        </body>
        </html>
        """)

    return JSONResponse(
        {
            "job_id": job_id,
            "entities_found": summary,
            "total_redactions": total,
            "manually_excluded": excluded_count,
            "manually_added": manual_count,
            "download_url": download_url,
        }
    )


@app.get("/api/download/{job_id}")
def download(job_id: str):
    # job_id est un uuid4().hex (caractères hexadécimaux uniquement), donc
    # sûr à utiliser dans un motif glob sans risque d'injection de chemin.
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=400, detail="Identifiant de job invalide")

    matches = list(WORKDIR.glob(f"{job_id}-*-anonymise.pdf"))
    if not matches:
        raise HTTPException(status_code=404, detail="Fichier introuvable ou déjà purgé")
    path = matches[0]

    # Reconstruit un nom de téléchargement explicite (type de document +
    # référence courte) sans jamais exposer le nom du fichier original.
    theme_slug = path.name[len(job_id) + 1 : -len("-anonymise.pdf")]
    public_filename = f"caviarde_{theme_slug}_{job_id[:8]}.pdf"

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=public_filename,
        content_disposition_type="inline",
    )


@app.get("/api/audit")
def read_audit_log(n: int = 50):
    """
    Consultation du journal d'audit (les n dernières entrées, 50 par défaut).
    Ne contient jamais de contenu de document ni de nom de fichier en clair —
    uniquement qui, quand, quel thème, combien d'éléments caviardés.
    """
    log_path = AUDIT_DIR / "audit.log"
    if not log_path.exists():
        return JSONResponse({"entries": []})

    with open(log_path, encoding="utf-8") as f:
        lines = f.readlines()[-n:]

    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return JSONResponse({"entries": entries})
