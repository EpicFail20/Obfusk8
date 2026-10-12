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
EXT-67 (D-058, D-059): no direct identifier of a user in the logs of any
container. Signs in compte-1 by cookie (Keycloak form, through the full
chain) and calls the text API with a VALID token of the extension's client
(no refused token: their claims stay in oauth2-proxy's standard log, an
accepted risk, EXT-70), then searches the logs of every container since the
start of the run for the account's e-mail (read from oauth2-proxy's
X-Auth-Request-Email answer) and username. Prints counts, never a value.

Expected: 0 everywhere once docs/patches/EXT-67-oauth2-proxy-logging.patch is
applied (observed before it: oauth2-proxy's authentication and request logs
carry the e-mail).

Usage (on the Docker host; the lab CA trusted, *.lab.local resolved to the stack):
  REQUESTS_CA_BUNDLE=~/.obfusk8-lab-ca/ca.crt BENCH_RESOLVE_ADDRESS=192.168.1.35 \\
    python3 benchmarks/e2e/check_no_identifier_in_logs.py
Exit code 1 if an identifier is found.
"""

import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_extension_oidc import api, discovery, sign_in

from obfusk8_client import BASE_URL, HTTP_ACCEPTED, HTTP_OK, login, read_accounts


def containers() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],  # noqa: S607 - docker from PATH on the host
        check=True,
        capture_output=True,
        text=True,
    )
    return [name for name in out.stdout.split() if name.startswith("obfusk8-")]


def main() -> int:
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    label, user, password = read_accounts()[0]
    session = login(user, password)
    answer = session.get(f"{BASE_URL}/oauth2/auth", timeout=30, allow_redirects=False)
    email = answer.headers.get("X-Auth-Request-Email", "")
    if answer.status_code != HTTP_ACCEPTED or "@" not in email:
        print(f"{label} : pas de session ou pas de courriel (HTTP {answer.status_code})")
        return 1
    _, body = sign_in(discovery(), user, password)
    status = api("GET", "/api/v1/version", body["access_token"]).status_code
    print(f"{label} : connexion par cookie (202) et requête avec un jeton valide ({status})")
    if status != HTTP_OK:
        return 1
    time.sleep(2)  # let the containers flush their logs
    found = 0
    for name in containers():
        logs = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            ["docker", "logs", "--since", since, name],  # noqa: S607 - docker from PATH on the host
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
        text = logs.stdout + logs.stderr
        hits = {what: text.count(value) for what, value in (("courriel", email), ("nom d'utilisateur", user)) if value}
        print(f"{name} : " + ", ".join(f"{count} {what}" for what, count in hits.items()))
        found += sum(hits.values())
    print(f"{found} identifiant(s) direct(s) trouvé(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
