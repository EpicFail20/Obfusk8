"""
branding.py : logo, favicon et bandeau d'en-tête Obfusk8.

Usage dans app/main.py (juste après la création de `app`) :

    from branding import install_branding
    app = FastAPI(...)
    install_branding(app)

Ce que ça fait :
  - sert /logo.svg, /favicon.svg (et redirige /favicon.ico) ;
  - injecte le favicon et un petit bandeau logo + nom dans TOUTES les pages
    HTML (formulaire, révision, résultat, pages d'erreur), sans toucher aux
    f-strings existantes ;
  - ne modifie jamais les réponses non HTML (PDF, PNG d'aperçu, JSON).

100 % hors ligne : aucune police ni ressource externe (pile de polices système).
"""
import re

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="20 22 176 176" role="img" aria-label="Obfusk8">
<path fill="#0a0a0a" fill-rule="evenodd" d="M40 108 C28 20 150 12 148 108 Z M63 84 a9 9 0 1 0 18 0 a9 9 0 1 0 -18 0 Z M103 84 a9 9 0 1 0 18 0 a9 9 0 1 0 -18 0 Z"/>
<circle cx="75" cy="86" r="4" fill="#0a0a0a"/>
<circle cx="109" cy="86" r="4" fill="#0a0a0a"/>
<g fill="none" stroke="#0a0a0a" stroke-width="10" stroke-linecap="round">
<path d="M52 104 C50 136 36 148 30 166"/>
<path d="M76 106 C74 140 88 152 78 178"/>
<path d="M100 106 C100 140 112 156 102 180"/>
<path d="M124 106 C126 140 110 154 120 178"/>
<path d="M142 104 C146 136 156 146 162 166"/>
</g>
<g fill="#0a0a0a">
<rect x="158" y="42" width="14" height="14"/>
<rect x="176" y="58" width="11" height="11"/>
<rect x="160" y="70" width="9" height="9"/>
<rect x="180" y="84" width="7" height="7"/>
<rect x="166" y="96" width="6" height="6"/>
<rect x="186" y="104" width="4" height="4"/>
<rect x="172" y="120" width="4" height="4"/>
</g>
</svg>"""

BADGE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="Obfusk8">
<rect width="256" height="256" rx="56" fill="#f4f1ea"/>
<g transform="translate(28 28) scale(1.0227)">
<svg viewBox="20 22 176 176" width="176" height="176">
<path fill="#0a0a0a" fill-rule="evenodd" d="M40 108 C28 20 150 12 148 108 Z M63 84 a9 9 0 1 0 18 0 a9 9 0 1 0 -18 0 Z M103 84 a9 9 0 1 0 18 0 a9 9 0 1 0 -18 0 Z"/>
<circle cx="75" cy="86" r="4" fill="#0a0a0a"/>
<circle cx="109" cy="86" r="4" fill="#0a0a0a"/>
<g fill="none" stroke="#0a0a0a" stroke-width="10" stroke-linecap="round">
<path d="M52 104 C50 136 36 148 30 166"/>
<path d="M76 106 C74 140 88 152 78 178"/>
<path d="M100 106 C100 140 112 156 102 180"/>
<path d="M124 106 C126 140 110 154 120 178"/>
<path d="M142 104 C146 136 156 146 162 166"/>
</g>
<g fill="#0a0a0a">
<rect x="158" y="42" width="14" height="14"/>
<rect x="176" y="58" width="11" height="11"/>
<rect x="160" y="70" width="9" height="9"/>
<rect x="180" y="84" width="7" height="7"/>
<rect x="166" y="96" width="6" height="6"/>
<rect x="186" y="104" width="4" height="4"/>
<rect x="172" y="120" width="4" height="4"/>
</g>
</svg>
</g>
</svg>"""

_HEAD_SNIPPET = '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'

_BRAND_BAR = (
    '<div data-obfusk8-brand style="display:flex; align-items:center; gap:20px; '
    'padding:28px 0 8px; text-align:left;">'
    '<img src="/logo.svg" alt="" width="72" height="72" style="display:block;">'
    '<span style="font-family: ui-sans-serif, system-ui, -apple-system, \'Segoe UI\', '
    'Roboto, Helvetica, Arial, sans-serif; font-size:2.5em; font-weight:700; '
    'letter-spacing:-0.02em; color:#0a0a0a;">Obfusk8</span>'
    '</div>'
)

_HEAD_END = re.compile(r"</head>", re.IGNORECASE)
_BODY_START = re.compile(r"<body[^>]*>", re.IGNORECASE)

_CACHE = {"Cache-Control": "public, max-age=86400"}


def _inject(html: str) -> str:
    """Ajoute favicon + bandeau, une seule fois, sans casser une page sans <head>/<body>."""
    if "data-obfusk8-brand" in html:
        return html
    html = _HEAD_END.sub(lambda m: _HEAD_SNIPPET + m.group(0), html, count=1)
    html = _BODY_START.sub(lambda m: m.group(0) + _BRAND_BAR, html, count=1)
    return html


def install_branding(app: FastAPI) -> None:
    @app.get("/logo.svg", include_in_schema=False)
    def logo_svg():
        return Response(LOGO_SVG, media_type="image/svg+xml", headers=_CACHE)

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon_svg():
        return Response(BADGE_SVG, media_type="image/svg+xml", headers=_CACHE)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon_ico():
        return RedirectResponse("/favicon.svg", status_code=307)

    @app.middleware("http")
    async def inject_branding(request: Request, call_next):
        response = await call_next(request)
        if not response.headers.get("content-type", "").startswith("text/html"):
            return response

        raw = b"".join([chunk async for chunk in response.body_iterator])
        data = _inject(raw.decode("utf-8", errors="replace")).encode("utf-8")

        new = Response(content=data, status_code=response.status_code)
        new.raw_headers = [
            (k, v) for k, v in response.raw_headers if k.lower() != b"content-length"
        ] + [(b"content-length", str(len(data)).encode())]
        return new
