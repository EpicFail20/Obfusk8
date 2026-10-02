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
Text API end to end THROUGH THE FULL CHAIN (Traefik -> oauth2-proxy ->
app under the enforcing seccomp profile -> presidio-analyzer), phase 1
step F dynamic tests: nominal analyze/pseudonymize, rejected inputs (415,
400, 422, 413 in the app and at the edge), tricky Unicode, unauthenticated
request, application-side queue overflow (429), per-user Traefik rate limit.
Synthetic content only. The CANARY values are then searched for in every
container log and both audit logs by check_no_content_in_logs.sh.

Usage: python3 benchmarks/e2e/e2e_text_api.py   (environment: see benchmarks/obfusk8_client.py)
Run it with nothing else using the stack: the rate-limit checks assume a full bucket.
"""

import concurrent.futures
import json
import sys
import time
import unicodedata
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from obfusk8_client import BASE_URL as BASE
from obfusk8_client import login

CANARY = "Zébulon Canarihaut"
results: list[bool] = []


def check(name: str, cond: object, detail: str = "") -> None:
    results.append(bool(cond))
    print(f"[{'OK ' if cond else 'KO '}] {name}{(' — ' + detail) if detail else ''}")


s = login()
J = {"Accept": "application/json"}

# --- version ---------------------------------------------------------------
r = s.get(f"{BASE}/api/v1/version", headers=J, timeout=30)
v = r.json()
check("version 200", r.status_code == 200 and v["api_version"] == "1.0", json.dumps(v, ensure_ascii=False))
check("version: X-Request-ID", len(r.headers.get("x-request-id", "")) == 32)
check("version: analyzer_recognizers renseigné", v["analyzer_recognizers"] is not None)
check("version: Cache-Control no-store", r.headers.get("cache-control") == "no-store")

# --- analyze ---------------------------------------------------------------
text = (
    f"😀 Bonjour, je suis {CANARY}, joignable au 06 12 34 56 78 ou zebulon.canarihaut@exemple.invalid. "
    f"{CANARY} habite à Rennes."
)
r = s.post(f"{BASE}/api/v1/text/analyze", json={"text": text}, timeout=30)
a = r.json()
check("analyze 200", r.status_code == 200, f"{len(a.get('entities', []))} entité(s)")
ents = a["entities"]
check("analyze: positions = chaîne reçue", all(text[e["start"] : e["end"]].strip() for e in ents))
utf16 = text.encode("utf-16-le")
check(
    "analyze: positions UTF-16 cohérentes",
    all(
        utf16[2 * e["start_utf16"] : 2 * e["end_utf16"]].decode("utf-16-le") == text[e["start"] : e["end"]]
        for e in ents
    ),
)
check("analyze: aucun score", all("score" not in e for e in ents))
found = {(e["entity_type"], text[e["start"] : e["end"]]) for e in ents}
print("     détections :", sorted(t for t, _ in found))
check(
    "analyze: nom détecté deux fois (propagation)", sum(1 for e in ents if text[e["start"] : e["end"]] == CANARY) == 2
)

# --- pseudonymize ----------------------------------------------------------
r = s.post(f"{BASE}/api/v1/text/pseudonymize", json={"text": text, "theme": "medical"}, timeout=30)
p = r.json()
check("pseudonymize 200 (thème médical)", r.status_code == 200 and p["theme"] == "medical")
check(
    "pseudonymize: valeurs absentes du texte",
    CANARY not in p["text"] and "06 12 34 56 78" not in p["text"] and "zebulon.canarihaut" not in p["text"],
    p["text"],
)
restored = p["text"]
for m in p["mapping"]:
    restored = restored.replace(m["placeholder"], m["original"])
check("pseudonymize: restauration exacte côté client", restored == text)


# --- rejected inputs ---------------------------------------------------------
def post_raw(body: bytes, ctype: str = "application/json") -> requests.Response:
    return s.post(f"{BASE}/api/v1/text/analyze", data=body, headers={"Content-Type": ctype, **J}, timeout=30)


cases = [
    ("415 text/plain", post_raw(json.dumps({"text": CANARY}).encode(), "text/plain"), 415),
    ("400 JSON invalide", post_raw(b'{"text": "Zebulon'), 400),
    ("400 UTF-8 invalide", post_raw(b'{"text": "\xff"}'), 400),
    ("422 champ score_threshold refusé", post_raw(json.dumps({"text": CANARY, "score_threshold": 0.99}).encode()), 422),
    ("422 texte vide", post_raw(b'{"text": ""}'), 422),
    ("422 thème inconnu", post_raw(json.dumps({"text": CANARY, "theme": "inconnu"}).encode()), 422),
    ("413 texte > MAX_TEXT_CHARS (application)", post_raw(json.dumps({"text": "a" * 20001}).encode()), 413),
]
for name, resp, expected in cases:
    body = resp.text
    check(
        name,
        resp.status_code == expected and "request_id" in resp.json() and CANARY not in body and "Zebulon" not in body,
        f"{resp.status_code} {body[:120]}",
    )

big = b'{"text": "' + b"a" * 250_000 + b'"}'
r = post_raw(big)
check(
    "413 corps > 244096 octets (bordure Traefik)",
    r.status_code == 413 and "request_id" not in r.text,
    f"{r.status_code} {r.text[:60]!r}",
)

# --- tricky Unicode: accepted, offsets on the received string ----------------
for label, t in [
    ("espace insécable", f"Appeler {CANARY} au 06 12 34 56 78"),
    ("largeur nulle", "Appeler Zé\u200bbulon Canari\u200bhaut au 06 12 34 56 78"),
    ("NFD", unicodedata.normalize("NFD", f"Appeler {CANARY} au 06 12 34 56 78")),
    ("contrôle bidi", f"Appeler \u202e{CANARY}\u202c au 06 12 34 56 78"),
]:
    r = s.post(f"{BASE}/api/v1/text/analyze", json={"text": t}, timeout=30)
    ok = r.status_code == 200 and all(0 <= e["start"] < e["end"] <= len(t) for e in r.json()["entities"])
    check(f"Unicode piégeux accepté ({label})", ok, str(sorted(e["entity_type"] for e in r.json()["entities"])))

# --- unauthenticated ---------------------------------------------------------
anon = requests.Session()
anon.verify = s.verify
r = anon.post(f"{BASE}/api/v1/text/analyze", json={"text": CANARY}, allow_redirects=False, timeout=30)
check(
    "non authentifié -> 302 + page de connexion (sans Location)",
    r.status_code == 302 and "Sign In" in r.text and CANARY not in r.text,
    f"{r.status_code} {r.headers.get('location')}",
)

# --- app-side concurrency: 20k-char texts in parallel ------------------------
long_text = ("Bonjour Camille Martin, appelez le 06 12 34 56 78. " * 400)[:20000]


def one(_: int) -> tuple[int, str | None]:
    resp = s.post(f"{BASE}/api/v1/text/analyze", json={"text": long_text}, timeout=60)
    return resp.status_code, resp.headers.get("retry-after")


time.sleep(25)  # let the per-user Traefik bucket refill before the burst
with concurrent.futures.ThreadPoolExecutor(max_workers=14) as pool:
    codes = list(pool.map(one, range(14)))
statuses = sorted(c for c, _ in codes)
check(
    "concurrence: file bornée -> 429 de l'application au-delà de 1+8",
    statuses.count(429) >= 1 and 200 in statuses,
    str(statuses),
)

# --- Traefik per-user rate limit ---------------------------------------------
time.sleep(25)
responses = [s.get(f"{BASE}/api/v1/version", timeout=30) for _ in range(40)]
limited = [c for c in responses if c.status_code == 429]
check(
    "limitation de débit Traefik (rafale 20)",
    15 <= sum(c.status_code == 200 for c in responses) <= 25 and limited and limited[0].headers.get("retry-after"),
    f"{sum(c.status_code == 200 for c in responses)} x 200, {len(limited)} x 429",
)

print(f"\n{sum(results)}/{len(results)} vérifications réussies")
sys.exit(0 if all(results) else 1)
