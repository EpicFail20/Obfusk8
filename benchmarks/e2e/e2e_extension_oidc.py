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
The extension's own OIDC token (option 2 of D-010, D-057) THROUGH THE FULL
CHAIN, with real tokens of the lab Keycloak (clients of keycloak/lab/).

Tokens are obtained like the extension does: authorization code flow with
PKCE S256, state and nonce, redirect to the extension's chromiumapp.org
address (never followed: read from the Location header), code exchanged at
the token endpoint with the extension's Origin, no client secret.
NO TOKEN IS EVER PRINTED; --fragments writes the payload and signature
segments of every token used to a file (mode 600) for
check_tokens_not_logged.py, to be deleted right after.

Modes (one per run; exit code 1 on any failed check):
  tokens          lab stack with ENABLE_EXTENSION_OIDC=true and
                  EXTENSION_REQUIRE_BEARER=false: Keycloak client (PKCE
                  enforced, exact redirect, no refresh token, CORS for the
                  extension, silent renewal with prompt=none), accepted and
                  refused tokens, Authorization dropped on the web routes,
                  same identity (sub) for the cookie and the token.
  expired         waits until the token has expired (5 min), then: refused.
  require-bearer  stack with EXTENSION_REQUIRE_BEARER=true: the session
                  cookie is refused on /api/v1/ (401 bearer_required), the
                  token is served, the web interface still works by cookie.
  ratelimit       cookie and token of compte-1 share one rate-limit bucket
                  (same sub, D-057 point 9): 429 after the burst; compte-2
                  is not limited.

Usage (throwaway client container of the app image, lab CA trusted):
  docker run --rm -e REQUESTS_CA_BUNDLE=/ca.crt -v ~/.obfusk8-lab-ca/ca.crt:/ca.crt:ro \\
    -e BENCH_RESOLVE_ADDRESS=192.168.1.35 -e BENCH_ACCOUNTS_FILE=/accounts \\
    -v ~/.obfusk8-test-accounts:/accounts:ro -v "$PWD:/repo:ro" -w /repo \\
    --entrypoint python ghcr.io/epicfail20/obfusk8-app:0.2.0-dev \\
    benchmarks/e2e/e2e_extension_oidc.py tokens
"""

import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from obfusk8_client import (
    BASE_URL,
    EXTENSION_ORIGIN,
    HTTP_ACCEPTED,
    HTTP_OK,
    HTTP_TOO_MANY_REQUESTS,
    login,
    read_accounts,
)

ISSUER = os.environ.get("E2E_OIDC_ISSUER", "http://keycloak.lab.local:8080/realms/lab")
CLIENT_ID = "obfusk8-extension"
OTHER_CLIENT_ID = "obfusk8-test-autre-application"
EXTENSION_ID = EXTENSION_ORIGIN.removeprefix("chrome-extension://")
REDIRECT_URI = f"https://{EXTENSION_ID}.chromiumapp.org/"
OTHER_REDIRECT_URI = REDIRECT_URI + "autre-application"
AUDIENCE = "obfusk8-api"
HTTP_FOUND = 302
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
TEXT = "Bonjour, je suis Camille Fictive, joignable au 06 12 34 56 78."
TEXT_BURST = 20  # burst of text-ratelimit (docker-compose.yml)
FAILURES: list[str] = []
USED_TOKENS: list[str] = []


def check(label: str, ok: bool, observed: object) -> None:
    print(f"{'OK  ' if ok else 'ECHEC'} {label} : {observed}")
    if not ok:
        FAILURES.append(label)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def claims(token: str) -> dict[str, Any]:
    """Payload of a JWT, NOT verified: only to read what the issuer put in."""
    payload = token.split(".")[1]
    return dict(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))))


def discovery() -> dict[str, Any]:
    response = requests.get(f"{ISSUER}/.well-known/openid-configuration", timeout=30)
    response.raise_for_status()
    return dict(response.json())


class Flow:
    """One authorization request, as the extension builds it."""

    def __init__(self, client_id: str = CLIENT_ID, redirect_uri: str = REDIRECT_URI) -> None:
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.verifier = b64url(secrets.token_bytes(32))
        self.state = b64url(secrets.token_bytes(16))
        self.nonce = b64url(secrets.token_bytes(16))

    def url(self, config: dict[str, Any], **extra: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid",
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge": b64url(hashlib.sha256(self.verifier.encode("ascii")).digest()),
            "code_challenge_method": "S256",
            **extra,
        }
        return f"{config['authorization_endpoint']}?{urlencode(params)}"


def follow_to_redirect(session: requests.Session, response: requests.Response, redirect_uri: str) -> str | None:
    """Follows Keycloak's own redirects until one targets the client's
    redirect URI (returned, never requested), or a page is reached (None)."""
    for _ in range(10):
        location = response.headers.get("Location")
        if response.status_code not in (301, 302, 303) or not location:
            return None
        if location.startswith(redirect_uri):
            return location
        response = session.get(location, allow_redirects=False, timeout=30)
    return None


def authorize(
    session: requests.Session, config: dict[str, Any], flow: Flow, user: str, password: str, **extra: str
) -> str | None:
    """Authorization request; signs in on Keycloak's form when it shows one."""
    response = session.get(flow.url(config, **extra), allow_redirects=False, timeout=30)
    location = follow_to_redirect(session, response, flow.redirect_uri)
    if location is not None or response.status_code != HTTP_OK:
        return location
    form = re.search(r'<form[^>]+id="kc-form-login"[^>]+action="([^"]+)"', response.text)
    if not form:
        return None
    response = session.post(
        html.unescape(form.group(1)),
        data={"username": user, "password": password, "credentialId": ""},
        allow_redirects=False,
        timeout=30,
    )
    return follow_to_redirect(session, response, flow.redirect_uri)


def redeem(
    config: dict[str, Any], flow: Flow, location: str, origin: str | None = EXTENSION_ORIGIN
) -> tuple[requests.Response, dict[str, Any]]:
    query = parse_qs(urlsplit(location).query)
    response = requests.post(
        config["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": flow.redirect_uri,
            "client_id": flow.client_id,
            "code_verifier": flow.verifier,
        },
        headers={"Origin": origin} if origin is not None else {},
        timeout=30,
    )
    body = dict(response.json()) if response.headers.get("content-type", "").startswith("application/json") else {}
    for key in ("access_token", "id_token", "refresh_token"):
        if key in body:
            USED_TOKENS.append(body[key])
    return response, body


def sign_in(
    config: dict[str, Any], user: str, password: str, client_id: str = CLIENT_ID, redirect_uri: str = REDIRECT_URI
) -> tuple[requests.Session, dict[str, Any]]:
    session = requests.Session()
    flow = Flow(client_id, redirect_uri)
    location = authorize(session, config, flow, user, password)
    if location is None:
        raise RuntimeError(f"no authorization code for {client_id}")
    query = parse_qs(urlsplit(location).query)
    if query.get("state") != [flow.state]:
        raise RuntimeError("state differs: response rejected")
    # The extension's exchange carries its Origin (CORS); another application
    # is modelled as a server-side client, without one.
    response, body = redeem(config, flow, location, EXTENSION_ORIGIN if client_id == CLIENT_ID else None)
    if "id_token" not in body:
        raise RuntimeError(f"token exchange for {client_id}: HTTP {response.status_code} {body.get('error')}")
    if claims(body["id_token"]).get("nonce") != flow.nonce:
        raise RuntimeError("nonce differs: response rejected")
    return session, body


def api(
    method: str,
    path: str,
    token: str | None = None,
    origin: str | None = EXTENSION_ORIGIN,
    session: Any = None,
    **kw: Any,
) -> requests.Response:
    headers = dict(kw.pop("headers", {}))
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if origin is not None and (method == "POST" or session is not None):
        # A cookie session of the benchmark client carries the application's
        # Origin (document routes); on /api/v1/ it stands for the extension.
        headers["Origin"] = origin
    client = session if session is not None else requests
    response: requests.Response = client.request(
        method, f"{BASE_URL}{path}", headers=headers, allow_redirects=False, timeout=60, **kw
    )
    return response


def forged_other_issuer(token: str) -> str:
    """Same claims, issuer of another realm, signed with a key of our own:
    what an impostor provider would hand out."""
    body = claims(token)
    body["iss"] = ISSUER.rsplit("/", 1)[0] + "/master"
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64url(json.dumps(body).encode())
    signature = b64url(hmac.new(secrets.token_bytes(32), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def tampered(token: str) -> tuple[str, str]:
    header, payload, signature = token.split(".")
    body = claims(token)
    body["sub"] = "00000000-0000-4000-8000-000000000000"
    flipped = signature[:-4] + ("AAAA" if signature[-4:] != "AAAA" else "BBBB")
    return f"{header}.{payload}.{flipped}", f"{header}.{b64url(json.dumps(body).encode())}.{signature}"


def oauth2_identity(token: str | None = None, session: Any = None) -> str:
    """X-Auth-Request-User that oauth2-proxy answers for this credential."""
    response = api("GET", "/oauth2/auth", token=token, session=session)
    return response.headers.get("X-Auth-Request-User", "") if response.status_code == HTTP_ACCEPTED else ""


def keycloak_client(config: dict[str, Any], user: str, password: str) -> dict[str, Any]:
    """The client of keycloak/lab/obfusk8-extension-client.json; returns the token response."""
    session = requests.Session()
    flow = Flow()
    page = session.get(
        flow.url(config, code_challenge_method="plain", code_challenge=flow.verifier), allow_redirects=False, timeout=30
    )
    location = page.headers.get("Location", "")
    check(
        "PKCE : méthode plain refusée par Keycloak",
        "error=" in location or page.status_code >= 400,
        (page.status_code, parse_qs(urlsplit(location).query).get("error")),
    )
    page = session.get(
        flow.url(config).replace("&code_challenge_method=S256", "").split("&code_challenge=")[0],
        allow_redirects=False,
        timeout=30,
    )
    location = page.headers.get("Location", "")
    check(
        "PKCE : requête sans défi refusée",
        "error=" in location or page.status_code >= 400,
        (page.status_code, parse_qs(urlsplit(location).query).get("error")),
    )
    page = session.get(Flow(redirect_uri=REDIRECT_URI + "autre").url(config), allow_redirects=False, timeout=30)
    check(
        "Redirection : URI différente refusée (correspondance exacte)",
        page.status_code == 400 and "Location" not in page.headers,
        page.status_code,
    )

    flow = Flow()
    code_location = authorize(session, config, flow, user, password)
    query = parse_qs(urlsplit(code_location or "").query)
    check(
        "Code d'autorisation rendu sur chromiumapp.org, state renvoyé à l'identique",
        code_location is not None and query.get("state") == [flow.state],
        code_location is not None,
    )
    response, body = redeem(config, flow, code_location or "")
    check(
        "Échange du code sans secret client",
        response.status_code == HTTP_OK and "access_token" in body,
        response.status_code,
    )
    check(
        "CORS : Keycloak autorise l'origine de l'extension (Web origins)",
        response.headers.get("Access-Control-Allow-Origin") == EXTENSION_ORIGIN,
        response.headers.get("Access-Control-Allow-Origin"),
    )
    check("Aucun jeton de rafraîchissement", "refresh_token" not in body, sorted(body))
    access = body["access_token"]
    access_claims, id_claims = claims(access), claims(body["id_token"])
    check(
        "nonce de l'ID token égal à celui envoyé",
        id_claims.get("nonce") == flow.nonce,
        "égal" if id_claims.get("nonce") == flow.nonce else "différent",
    )
    audience = access_claims.get("aud")
    check("Audience du jeton d'accès : obfusk8-api seulement", audience in (AUDIENCE, [AUDIENCE]), audience)
    check(
        "Durée de vie du jeton d'accès : 5 min",
        access_claims["exp"] - access_claims["iat"] == 300,
        access_claims["exp"] - access_claims["iat"],
    )
    check(
        "Jeton : sub non vide, groupe autorisé présent",
        bool(access_claims.get("sub")) and "/SG-Anonymiseur-Utilisateurs" in access_claims.get("groups", []),
        "groupes" if access_claims.get("groups") else "sans groupes",
    )

    replay = redeem(config, flow, code_location or "")[0]
    check("Code d'autorisation à usage unique (rejeu refusé)", replay.status_code == 400, replay.status_code)

    silent = Flow()
    silent_location = authorize(session, config, silent, user, password, prompt="none")
    check(
        "Renouvellement silencieux (prompt=none) avec la session du fournisseur",
        silent_location is not None and "code" in parse_qs(urlsplit(silent_location).query),
        silent_location is not None,
    )
    fresh = requests.Session()
    page = fresh.get(Flow().url(config, prompt="none"), allow_redirects=False, timeout=30)
    error = parse_qs(urlsplit(page.headers.get("Location", "")).query).get("error")
    check("Sans session du fournisseur, prompt=none refusé (login_required)", error == ["login_required"], error)
    return body


def server_checks(config: dict[str, Any], user: str, password: str, body: dict[str, Any]) -> None:
    """Accepted and refused tokens on /api/v1/."""
    access = body["access_token"]
    access_claims = claims(access)
    version = api("GET", "/api/v1/version", access)
    check("Jeton valide : GET /api/v1/version 200", version.status_code == HTTP_OK, version.status_code)
    analyzed = api("POST", "/api/v1/text/analyze", access, json={"text": TEXT})
    check("Jeton valide : POST analyze 200", analyzed.status_code == HTTP_OK, analyzed.status_code)
    pseudo = api("POST", "/api/v1/text/pseudonymize", access, json={"text": TEXT})
    check("Jeton valide : POST pseudonymize 200", pseudo.status_code == HTTP_OK, pseudo.status_code)
    sub_hash = hashlib.sha256(access_claims["sub"].encode()).hexdigest()[:16]
    print(f"     request_id analyze={analyzed.headers.get('X-Request-Id')}", end=" ")
    print(f"pseudonymize={pseudo.headers.get('X-Request-Id')} sub_sha256={sub_hash}")
    no_origin = api("POST", "/api/v1/text/analyze", access, origin=None, json={"text": TEXT})
    check(
        "Règle d'origine maintenue avec un jeton : POST sans Origin 403",
        no_origin.status_code == HTTP_FORBIDDEN
        and no_origin.headers.get("content-type", "").startswith("application/json"),
        no_origin.status_code,
    )

    _, other = sign_in(config, user, password, OTHER_CLIENT_ID, OTHER_REDIRECT_URI)
    check(
        "Jeton de l'autre application : audience autre-application",
        claims(other["access_token"]).get("aud") in ("autre-application", ["autre-application"]),
        claims(other["access_token"]).get("aud"),
    )
    bad_signature, bad_payload = tampered(access)
    impostor = forged_other_issuer(access)
    USED_TOKENS.extend([bad_signature, bad_payload, impostor])
    refused = {
        "jeton d'une autre application du même émetteur": other["access_token"],
        "ID token de l'extension (audience obfusk8-extension)": body["id_token"],
        "signature altérée": bad_signature,
        "charge utile altérée (autre sub)": bad_payload,
        "autre émetteur (clé de l'imposteur)": impostor,
        "chaîne qui n'est pas un JWT": "pas-un-jwt",
    }
    for label, token in refused.items():
        for method, path in (("GET", "/api/v1/version"), ("POST", "/api/v1/text/analyze")):
            response = api(method, path, token, json={"text": TEXT} if method == "POST" else None)
            ok = response.status_code == HTTP_FORBIDDEN and not response.headers.get("content-type", "").startswith(
                "application/json"
            )
            check(f"Refusé par oauth2-proxy : {label}, {method} {path.rsplit('/', 1)[-1]}", ok, response.status_code)
    nothing = api("GET", "/api/v1/version")
    check("Ni jeton ni cookie : 401", nothing.status_code == HTTP_UNAUTHORIZED, nothing.status_code)


def web_routes(access: str) -> None:
    """The web routers drop Authorization (D-057 point 2)."""
    # oauth2-errors answers an anonymous request with the sign-in page under a
    # 302 rewritten from 401 (no Location): the token must change nothing.
    anonymous = api("GET", "/")
    home = api("GET", "/", access)
    check(
        "Interface web avec le seul jeton : traitée comme anonyme (page de connexion)",
        home.status_code == anonymous.status_code == HTTP_FOUND and home.text == anonymous.text,
        (home.status_code, anonymous.status_code),
    )
    garbage = api(
        "POST", "/api/cancel/" + "0" * 32, "pas-un-jwt", origin=BASE_URL, headers={"X-Obfusk8-Action": "cancel"}
    )
    check(
        "Faux jeton sur une route web : 401 anonyme, pas le 403 d'oauth2-proxy (en-tête retiré avant oidc-auth)",
        garbage.status_code == HTTP_UNAUTHORIZED,
        garbage.status_code,
    )
    cancel = api(
        "POST",
        "/api/cancel/" + "0" * 32,
        access,
        origin=BASE_URL,
        headers={"X-Obfusk8-Action": "cancel", "Accept": "application/json"},
    )
    check("Annulation avec le seul jeton : 401", cancel.status_code == HTTP_UNAUTHORIZED, cancel.status_code)
    detect = api(
        "POST",
        "/api/detect",
        access,
        origin=BASE_URL,
        files={"file": ("fictif.csv", b"nom\nCamille Fictive\n", "text/csv")},
        data={"theme": ""},
    )
    check(
        "Dépôt de document avec le seul jeton : pas servi (redirection)",
        detect.status_code == HTTP_FOUND,
        detect.status_code,
    )
    preview = api("GET", "/api/preview_image?job_id=" + "0" * 32 + "&page=0", access)
    check("Aperçu avec le seul jeton : pas servi (redirection)", preview.status_code == HTTP_FOUND, preview.status_code)


def one_identity(user: str, password: str, access: str) -> None:
    """Same sub for the cookie and the token (D-057 point 9); cookie flow unchanged."""
    access_claims = claims(access)
    cookie_session = login(user, password)
    by_cookie, by_token = oauth2_identity(session=cookie_session), oauth2_identity(token=access)
    check(
        "Même identité (sub) pour le cookie et le jeton",
        by_cookie != "" and by_cookie == by_token == access_claims["sub"],
        "identiques" if by_cookie == by_token else "différentes",
    )
    web = cookie_session.get(f"{BASE_URL}/", allow_redirects=False, timeout=30)
    check("Parcours par cookie inchangé : interface web 200", web.status_code == HTTP_OK, web.status_code)
    by_session = api("GET", "/api/v1/version", session=cookie_session)
    check(
        "Parcours par cookie inchangé : /api/v1/version 200 (EXTENSION_REQUIRE_BEARER=false)",
        by_session.status_code == HTTP_OK,
        by_session.status_code,
    )


def mode_tokens(config: dict[str, Any], user: str, password: str) -> None:
    body = keycloak_client(config, user, password)
    server_checks(config, user, password, body)
    web_routes(body["access_token"])
    one_identity(user, password, body["access_token"])


def mode_expired(config: dict[str, Any], user: str, password: str) -> None:
    _, body = sign_in(config, user, password)
    access = body["access_token"]
    check("Jeton neuf accepté", api("GET", "/api/v1/version", access).status_code == HTTP_OK, "avant échéance")
    wait = claims(access)["exp"] - time.time() + 5
    print(f"     attente de l'échéance : {wait:.0f} s")
    time.sleep(max(wait, 0))
    for method, path in (("GET", "/api/v1/version"), ("POST", "/api/v1/text/analyze")):
        response = api(method, path, access, json={"text": TEXT} if method == "POST" else None)
        check(f"Jeton expiré refusé : {method} {path}", response.status_code == HTTP_FORBIDDEN, response.status_code)


def mode_require_bearer(config: dict[str, Any], user: str, password: str) -> None:
    _, body = sign_in(config, user, password)
    access = body["access_token"]
    cookie_session = login(user, password)
    for method, path in (("GET", "/api/v1/version"), ("POST", "/api/v1/text/analyze")):
        response = api(method, path, session=cookie_session, json={"text": TEXT} if method == "POST" else None)
        detail = (
            response.json().get("detail")
            if response.headers.get("content-type", "").startswith("application/json")
            else None
        )
        check(
            f"Cookie refusé sur l'API : {method} {path} 401 JSON",
            response.status_code == HTTP_UNAUTHORIZED and detail is not None,
            response.status_code,
        )
        response = api(method, path, access, json={"text": TEXT} if method == "POST" else None)
        check(f"Jeton servi : {method} {path} 200", response.status_code == HTTP_OK, response.status_code)
    web = cookie_session.get(f"{BASE_URL}/", allow_redirects=False, timeout=30)
    check("Interface web par cookie inchangée : 200", web.status_code == HTTP_OK, web.status_code)


def mode_ratelimit(config: dict[str, Any], first: tuple[str, str], second: tuple[str, str]) -> None:
    _, body = sign_in(config, *first)
    access = body["access_token"]
    cookie_session = login(*first)
    other_session = login(*second)
    statuses = []
    for i in range(TEXT_BURST + 5):
        # Alternates cookie and token: one bucket if both carry the same sub.
        response = (
            api("GET", "/api/v1/version", access) if i % 2 else api("GET", "/api/v1/version", session=cookie_session)
        )
        statuses.append(response.status_code)
    limited = statuses.count(HTTP_TOO_MANY_REQUESTS)
    check(
        "Cookie et jeton du même agent : un seul seau, 429 après la rafale",
        limited > 0 and statuses[:TEXT_BURST].count(HTTP_OK) >= TEXT_BURST - 1,
        f"{statuses.count(HTTP_OK)} x 200, {limited} x 429",
    )
    other = api("GET", "/api/v1/version", session=other_session)
    check("Autre agent non limité", other.status_code == HTTP_OK, other.status_code)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["tokens", "expired", "require-bearer", "ratelimit"])
    parser.add_argument("--fragments", type=Path, help="file (mode 600) receiving the segments of every token used")
    args = parser.parse_args()
    accounts = read_accounts()
    first = (accounts[0][1], accounts[0][2])
    print(f"Compte : {accounts[0][0]} ; émetteur : {ISSUER}")
    config = discovery()
    try:
        if args.mode == "tokens":
            mode_tokens(config, *first)
        elif args.mode == "expired":
            mode_expired(config, *first)
        elif args.mode == "require-bearer":
            mode_require_bearer(config, *first)
        else:
            print(f"Second compte : {accounts[1][0]}")
            mode_ratelimit(config, first, (accounts[1][1], accounts[1][2]))
    finally:
        if args.fragments is not None:
            fd = os.open(args.fragments, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as out:
                for token in USED_TOKENS:
                    for segment in token.split(".")[1:]:
                        if len(segment) >= 16:
                            out.write(segment[-40:] + "\n")
    print(f"{len(FAILURES)} échec(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
