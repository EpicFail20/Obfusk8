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
Availability of the document flow THROUGH THE FULL CHAIN (EXT-51, EXT-47,
D-048), with two test accounts (compte-1, compte-2 of BENCH_ACCOUNTS_FILE,
reported by label only). Synthetic CSV only; jobs are cancelled or left to
expire, never finalized (nothing in audit.log but cancellation events).

Modes (one per run; exit code 1 on any failed check):
  quota       compte-1 fills its own quota (429 + Retry-After), compte-2
              still uploads; cancellation frees the place; cancelling
              compte-2's job, a request without X-Obfusk8-Action and
              requests from another origin are refused. Requests paced
              under the per-IP rate limit (E2E_PACE seconds).
  burst       compte-1 sends BURST_REQUESTS cancellations back to back:
              Traefik answers 429 once the per-user burst (10) is spent.
  after-burst run right after `burst` FROM ANOTHER CLIENT ADDRESS (another
              container): compte-1 is still limited (per-user bucket),
              compte-2 is not.

Usage (throwaway client container of the app image; `burst` and
`after-burst` from two containers, hence two source addresses):
  docker run --rm -e BENCH_INSECURE_TLS=1 -e BENCH_RESOLVE_ADDRESS=192.168.1.35 \\
    -e BENCH_ACCOUNTS_FILE=/accounts -v ~/.obfusk8-test-accounts:/accounts:ro \\
    -v "$PWD:/repo:ro" -w /repo --entrypoint python ghcr.io/epicfail20/obfusk8-app:0.2.0-dev \\
    benchmarks/e2e/e2e_availability.py quota
"""

import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from obfusk8_client import BASE_URL, HTTP_ACCEPTED, HTTP_OK, HTTP_TOO_MANY_REQUESTS, login, read_accounts

PACE = float(os.environ.get("E2E_PACE", "13"))  # under the per-IP limit: 5/min
QUOTA = int(os.environ.get("E2E_QUOTA", "3"))  # MAX_PENDING_JOBS_PER_USER of the stack
BURST_REQUESTS = 12  # burst of upload-user-ratelimit (10) + 2
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
CSV = b"nom,ville\nCamille Fictive,Lyon\n"
FAILURES: list[str] = []


def check(label: str, ok: bool, observed: object) -> None:
    print(f"{'OK  ' if ok else 'ECHEC'} {label} : {observed}")
    if not ok:
        FAILURES.append(label)


def detect(session: Any, headers: dict[str, str] | None = None) -> Any:
    time.sleep(PACE)
    files = {"file": ("fictif.csv", CSV, "text/csv")}
    return session.post(f"{BASE_URL}/api/detect", files=files, data={"theme": ""}, headers=headers or {}, timeout=60)


def job_id(response: Any) -> str:
    match = re.search(r'name="job_id" value="([0-9a-f]{32})"', response.text)
    return match.group(1) if match else ""


def cancel(session: Any, job: str, headers: dict[str, str] | None = None, pace: bool = True) -> Any:
    if pace:
        time.sleep(PACE)
    sent = {"X-Obfusk8-Action": "cancel", "Accept": "application/json", **(headers or {})}
    return session.post(f"{BASE_URL}/api/cancel/{job}", headers=sent, timeout=30)


def origin_of(response: Any) -> str:
    """'app' when the application answered (JSON detail), 'edge' otherwise."""
    return "app" if '"detail"' in response.text else "edge"


def quota(first: Any, second: Any) -> None:
    jobs = []
    for index in range(QUOTA):
        response = detect(first)
        jobs.append(job_id(response))
        sent = response.status_code == HTTP_OK and bool(jobs[-1])
        check(f"compte-1 envoi {index + 1}/{QUOTA}", sent, response.status_code)
    over = detect(first, {"Accept": "application/json"})
    retry_after = over.headers.get("Retry-After", "")
    check(
        "compte-1 au-delà du quota : 429 de l'application avec Retry-After",
        over.status_code == HTTP_TOO_MANY_REQUESTS and origin_of(over) == "app" and retry_after.isdigit(),
        f"{over.status_code} {origin_of(over)} Retry-After={retry_after}",
    )
    other = detect(second)
    other_job = job_id(other)
    check("compte-2 envoie pendant ce temps", other.status_code == HTTP_OK and bool(other_job), other.status_code)

    stolen = cancel(first, other_job)
    check("compte-1 annule le document de compte-2 : 404", stolen.status_code == HTTP_NOT_FOUND, stolen.status_code)
    unknown = cancel(first, uuid.uuid4().hex)
    check(
        "document inconnu : même réponse",
        (unknown.status_code, unknown.text) == (stolen.status_code, stolen.text),
        unknown.status_code,
    )
    no_header = first.post(f"{BASE_URL}/api/cancel/{jobs[0]}", headers={"Accept": "application/json"}, timeout=30)
    check("annulation sans X-Obfusk8-Action : 403", no_header.status_code == HTTP_FORBIDDEN, no_header.status_code)
    foreign = cancel(first, jobs[0], {"Origin": "https://attaquant.invalid"})
    check("annulation depuis une autre origine : 403", foreign.status_code == HTTP_FORBIDDEN, foreign.status_code)
    foreign_detect = detect(first, {"Origin": "https://attaquant.invalid", "Accept": "application/json"})
    check(
        "envoi depuis une autre origine : 403",
        foreign_detect.status_code == HTTP_FORBIDDEN,
        foreign_detect.status_code,
    )
    # The application's 403 must reach the client as is: rewritten into
    # oauth2-proxy's sign-in page, it cleared the session cookie (a forced
    # sign-out from any other site, observed on 2026-10-07).
    still = first.get(f"{BASE_URL}/oauth2/auth", timeout=30, allow_redirects=False)
    check(
        "session intacte après un refus d'origine",
        still.status_code == HTTP_ACCEPTED and "_oauth2_proxy=" not in foreign_detect.headers.get("Set-Cookie", ""),
        still.status_code,
    )

    own = cancel(first, jobs[0])
    check("compte-1 annule son document : 200", own.status_code == HTTP_OK, own.status_code)
    again = detect(first)
    check("compte-1 envoie de nouveau après l'annulation", again.status_code == HTTP_OK, again.status_code)
    for job in [*jobs[1:], job_id(again)]:
        cancel(first, job)
    cancel(second, other_job)


def burst(first: Any) -> None:
    statuses = [cancel(first, uuid.uuid4().hex, pace=False) for _ in range(BURST_REQUESTS)]
    observed = [f"{r.status_code} {origin_of(r)}" for r in statuses]
    limited = [r for r in statuses if r.status_code == HTTP_TOO_MANY_REQUESTS and origin_of(r) == "edge"]
    check("compte-1 en rafale : 429 de Traefik après la rafale", bool(limited), observed)


def after_burst(first: Any, second: Any) -> None:
    mine = cancel(first, uuid.uuid4().hex, pace=False)
    check(
        "compte-1 depuis une autre adresse : toujours limité",
        mine.status_code == HTTP_TOO_MANY_REQUESTS and origin_of(mine) == "edge",
        f"{mine.status_code} {origin_of(mine)}",
    )
    theirs = cancel(second, uuid.uuid4().hex, pace=False)
    check("compte-2 au même moment : non limité", theirs.status_code == HTTP_NOT_FOUND, theirs.status_code)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    accounts = read_accounts()
    if len(accounts) < 2 or mode not in ("quota", "burst", "after-burst"):
        print("usage: e2e_availability.py quota|burst|after-burst (two accounts needed)")
        return 2
    sessions = [login(user, password) for _, user, password in accounts[:2]]
    if mode == "quota":
        quota(*sessions)
    elif mode == "burst":
        burst(sessions[0])
    else:
        after_burst(*sessions)
    print(f"{mode} : {len(FAILURES)} échec(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
