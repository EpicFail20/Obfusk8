# Dependency maintenance

*Version française : [maintenance-dependances.md](maintenance-dependances.md).*

Procedure set up in phase 2 ter (2026-10-06, D-043) so that updating dependencies is a **routine operation** that can be replayed
without reinventing the method. It complements `CLAUDE.md` §6 (version checks), §7 (bugs) and §9 (approvals). Version register:
`docs/DEPENDENCIES.md`; justified gaps: `docs/DECISIONS.md`.

Every command runs from the repository root, on the test VM, with **a single stack** (`CLAUDE.md` §1). `$SCRATCH` is a working directory
on disk, **outside** the `/tmp` tmpfs (EXT-25) and outside the repository.

## 1. What is maintained

| Set | Files | Note |
|---|---|---|
| `app` Python packages | `app/requirements.txt` (top level), `app/requirements.lock` (full set, hashes) | Installed with `--require-hashes --no-deps` |
| Analyzer Python packages | `presidio/analyzer-build/requirements.lock` (full set, hashes, spaCy models included) | `check_lock.py` fails the build if the installed environment differs |
| Detection versions | `presidio/analyzer-build/versions.json` = `app/analyzer_versions.json` | `detection_config` fingerprint; both copies stay identical (checked by `app/run-tests.sh`) |
| Build-time pip | `app/requirements-build.txt`, `presidio/analyzer-build/requirements-build.txt` | Removed from the images after installation |
| Development tools | `app/requirements-dev.in`, `app/requirements-dev.txt` | Never in the images; installed in `~/.cache/obfusk8-devtools`; `regex` follows the analyzer's version (D-017) |
| Python base image (both images) | `x-python-base` of `docker-compose.build.yml`, `PYTHON_BASE` build argument of both Dockerfiles | Pinned by digest, **one place only** (D-052); §9 |
| Presidio REST server | `presidio/analyzer-build/server/` (upstream copy, MIT license) | Copied again and compared on every Presidio update (§10) |
| Third-party images | `docker-compose.yml` (and the `docker-compose.rollback-*.yml` overrides) | Pinned by digest |
| Debian packages | `apt-get upgrade` on every build | Follow the rebuild |
| GitHub Actions | `.github/workflows/` | Pinned by commit; workflow disabled (D-042) |

## 2. Listing outdated dependencies and their vulnerabilities

1. **Installed environments** (not only the files):

   ```sh
   docker run --rm --network none --entrypoint python ghcr.io/epicfail20/obfusk8-presidio-analyzer:0.2.0-dev -c \
     'import importlib.metadata as m; [print("ana", d.metadata["Name"], d.version) for d in m.distributions()]' \
     | grep -vE 'en_core_web_lg|fr_core_news_md' > "$SCRATCH/list.txt"
   grep -E '^[a-zA-Z]' app/requirements.lock     | sed -E 's/^([^=]+)==([^ ]+).*/app \1 \2/' >> "$SCRATCH/list.txt"
   grep -E '^[a-zA-Z]' app/requirements-dev.txt  | sed -E 's/^([^=]+)==([^ ]+).*/dev \1 \2/' >> "$SCRATCH/list.txt"
   ```

2. **Latest stable release and vulnerabilities of the installed version**, at the source (PyPI JSON API, OSV API):

   ```sh
   PYTHONPATH=~/.cache/obfusk8-devtools python3 tools/dependencies/outdated.py < "$SCRATCH/list.txt" > "$SCRATCH/inventory.tsv"
   grep -E 'OUTDATED|ERROR' "$SCRATCH/inventory.tsv"; awk -F'\t' '$7 != "-"' "$SCRATCH/inventory.tsv"
   ```

   For each advisory: OSV record (`https://api.osv.dev/v1/vulns/<id>`), cross-checked with the GitHub Advisory Database or the NVD; never a blog.
3. **Caps**: an "outdated" version may be capped by a dependent (`numpy<2.5.0` by Presidio, `thinc<8.4.0` by spaCy 3.8, `pydantic-core==` by
   pydantic): read the `requires_dist` (`https://pypi.org/pypi/<package>/<version>/json`).
4. **Release notes read** for each jump: GitHub releases, project changelog, otherwise the commit comparison. Also read the new version's
   **dependencies** (`requires_dist`): FastAPI 0.142 made `opentelemetry-api` mandatory (D-043). Check that the version is **published on
   PyPI** (gunicorn 26.2.1 and 26.2.2 were GitHub releases only on 2026-10-06).
5. **Images**: upstream version (GitHub releases or registry) and current digest of the pinned tag
   (`docker buildx imagetools inspect <image>:<version>`): a tag can be rebuilt without a version change (`python:3.12-slim`, 2026-10-06).
6. **Scans**: `trivy` (HIGH, CRITICAL) on every running image, in a throwaway memory-limited container, cache on disk:

   ```sh
   docker run --rm --memory 2g -v /var/run/docker.sock:/var/run/docker.sock:ro -v ~/.cache/obfusk8-trivy/cache:/root/.cache \
     aquasec/trivy@<digest> image --quiet --scanners vuln --severity HIGH,CRITICAL <image>
   ```

7. Record the list in a working document (template: `docs/phase-2-ter-dependances.md`).

## 3. Grouping into batches, and in which order

A batch = a separately testable set, one commit, one rollback. Phase 2 ter order (D-043), from the most isolated to the widest:

| Order | Batch | Why this rank | Specific checks |
|---|---|---|---|
| 0 | `app` base image (digest, Debian packages) | Changes the ground of every later batch | `dpkg-query` difference, OCR, document flow |
| 1 | `app` HTTP server (uvicorn, uvloop, httptools, websockets, anyio) | Event loop under seccomp (EXT-11), header trust | Trace under `app-audit.json`, `test_proxy_headers_trust.py`, forged header end to end, latency |
| 2 | Web framework (FastAPI, Starlette, python-multipart, pydantic) | Body caps (EXT-22), error format, strict models | Full suite, text and document end to end |
| 3 | `app` clients and utilities (requests…) | Low risk | End to end |
| 4 | Document processing (PyMuPDF, python-docx, Pillow, pytesseract, lxml) | Content of the final files | Document-flow zones, final files (`e2e_document_flow.py`) |
| 5 | Analyzer, serving (gunicorn, Flask, Werkzeug, and everything that does not touch detection) | Detection must stay **identical** | Quality and secret benchmarks identical line by line |
| 6 | Analyzer, detection (spaCy and models, regex, phonenumbers, tldextract, Presidio) | Changes detection and `detection_config` | Benchmarks with the safeguard (§5) |
| 7 | Development tools | Outside the images | ruff, mypy, bandit, suite |
| 8 | Third-party images (patch releases only) | No code | Test account logins, trivy |

A **security fix** goes first, alone in its batch.

## 4. Updating, rebuilding, testing

**Before the first batch**: tag the running images (`obfusk8-local:avant-<phase>-<service>`), write the
`docker-compose.rollback-<phase>.yml` override (template: `docker-compose.rollback-2ter.yml`, `pull_policy: never`) and **test the rollback**
both ways (test account logins, `Seccomp: 2` in `/proc/1/status` of the `app` container).

**`app` lock** (same method for `requirements-dev.txt`, with `requirements-dev.in`):

```sh
# constraints.txt = the current lock's versions, the updated packages pinned to their target version
grep -E '^[a-zA-Z]' app/requirements.lock | sed -E 's/ .*//' | grep -v '^uvicorn==' > "$SCRATCH/constraints.txt"
echo 'uvicorn==<target>' >> "$SCRATCH/constraints.txt"     # and the top level in app/requirements.txt
docker run --rm --memory 1g -v "$PWD/app:/src:ro" -v "$SCRATCH:/out" <value of x-python-base> sh -c '
  pip install -q --root-user-action=ignore --no-cache-dir --require-hashes --no-deps -r /src/requirements-build.txt &&
  pip install -q --root-user-action=ignore --no-cache-dir --dry-run --ignore-installed --only-binary=:all: \
      --report /out/report.json -r /src/requirements.txt -c /out/constraints.txt && chmod 644 /out/report.json'
python3 tools/dependencies/lock_from_report.py "$SCRATCH/report.json" > "$SCRATCH/body.txt"
# replace the lock entries with body.txt, keeping the header; check that only the intended packages move:
git diff app/requirements.lock | grep '^[-+][a-zA-Z]'
```

**Analyzer lock**: same principle inside the same base image (`x-python-base`), with `-v "$PWD/presidio/analyzer-build:/src:ro"`,
`-r names.txt` (names of every entry but the models, `presidio-analyzer` included) and `-c constraints.txt`. The resolution must return **exactly** the constrained set (no package added or dropped); the two
spaCy model lines are kept by hand. If spaCy, Presidio or the model changes: `versions.json` **and** `app/analyzer_versions.json`, then
rebuild `app` **as well** (`detection_config` fingerprint).

**Build and tests**:

```sh
docker compose -f docker-compose.yml -f docker-compose.build.yml build --no-cache <service>
app/run-tests.sh                       # under seccomp/app-enforce.json; never inside the app container
ENABLE_EXTENSION_API=true docker compose up -d <service>   # text API enabled for the measurements only
```

**System calls** (batches 0 to 4, and any compiled library of `app`): `app` under `app-audit.json` through a Compose override kept outside
the repository (`security_opt: !override`), scenarios replayed (`benchmarks/e2e/e2e_text_api.py`, `benchmarks/e2e/e2e_document_flow.py`,
clean restart), then `sudo dmesg | grep 'audit: type=1326'`. Known and deliberately not allowed: `io_uring_setup`, `io_uring_enter`,
`openat2`. Any other call: method of `seccomp/README.md`, justified addition; **stop** before a sensitive call (`ptrace`, `mount`, `bpf`,
`unshare`, `setns`, `keyctl`, `perf_event_open`, `process_vm_*`).

**Benchmarks** (test accounts read at run time from `~/.obfusk8-test-accounts`, by a script that prints nothing, under **bash**):
`benchmarks/quality/run_quality_bench.py`, `benchmarks/secret_detection/run_secrets_bench.py`,
`benchmarks/documents/doc_zones_snapshot.py` (then `--compare` against the reference), `benchmarks/latency/run_latency_bench.py`
(`BENCH_CONTENTION_ROUNDS=1 BENCH_CONTENTION_DOCUMENTS=3`), `benchmarks/e2e/check_no_content_in_logs.py`.
One account per benchmark run in parallel (per-user rate limit, `MAX_PENDING_JOBS`).

**Final scans**: `trivy` on every built image; `pip-audit` on the installed environments
(`pip-audit --path <site-packages> --vulnerability-service osv`, in a container of the image, tools mounted).

**Commit**: one per batch; the message says what changed and what was checked (figures). `docs/DEPENDENCIES.md` updated.

## 5. Safeguards and rollback

- **Detection**: if masking on the main corpus drops by more than **0.02** from the reference (0.973 on 2026-10-06), or if a secret is no
  longer detected: **stop**, present the figures, human decision. A smaller variation is reported, not corrected.
- **Two failed attempts** on a batch (`CLAUDE.md` §7): back to the previous version for that batch, gap in `DECISIONS.md` (target version,
  cause, lifting condition), next batch.
- **Rolling back a batch**: `git revert <batch commit>` then rebuild; **the whole phase**:
  `docker compose -f docker-compose.yml -f docker-compose.rollback-<phase>.yml up -d`.
- **Disk**: never `docker image prune -a` nor `docker system prune -a` (D-043); delete by ID only the intermediate images built during the work.
- **End of work**: `ENABLE_EXTENSION_API=false` in `.env`, stack redeployed in that state.

## 6. What requires a human decision

- **Minor or major** version of a third-party image (Keycloak, oauth2-proxy, Traefik…); **Python** version.
- **New production dependency**, including one imposed by an update (FastAPI 0.142 and `opentelemetry-api`).
- **Major** version of a package (gunicorn 26, filelock 4, setuptools 84 in phase 2 ter).
- Any gap to the latest stable release, and its lifting condition.
- Any **detection change** beyond the measured effect of an update; any crossing of the safeguard.
- Any sensitive system call added to the seccomp profile.
- Changes to `docker-compose.yml`, `oauth2-proxy.cfg`, Traefik or a Dockerfile beyond versions (`CLAUDE.md` §9).

**Systematic review at every cycle**: FastAPI (D-043 point 1: stay on 0.141.1 as long as no security fix exists only in 0.142+ and
OpenTelemetry is mandatory; on the day of the upgrade, telemetry explicitly disabled in `FastAPI(...)` and a test proving that nothing is
exported); docker-socket-proxy (D-040 point 8); capped numpy and thinc; gunicorn capped by Presidio (`<26.0.0`, EXT-60); Python version (§9).

## 7. Cadence (approved on 2026-10-06, D-044 point 2)

| Trigger | Proposed delay | Content |
|---|---|---|
| **Emergency**: CRITICAL advisory, or HIGH exploitable in our use, on a dependency **in production** (`app` or analyzer package, base image, Traefik, oauth2-proxy, Keycloak in production) | Exposure analysis within 24 business hours, fix within 72 h if exposed | Single security batch, full procedure; without an upstream fix, documented compensating measure |
| HIGH or MODERATE advisory fixed upstream | At the latest at the next monthly cycle; earlier if exposed | Security batch |
| **Full review** | **Monthly** (first week of the month), approved | Whole §2, batches of §3, short report |
| Minor or major version change (images, Python) | Quarterly, or on decision | Dedicated phase |

Approved emergency: exposure analysis within **24 business hours**, fix within **72 h** if exposed. Alert sources: §8.

## 8. Alert sources between cycles

**GitHub security alerts** (D-044 point 2): repository *Settings → Code security*, **Dependabot alerts** enabled; **Dependabot security
updates** and **version updates** disabled (no automatic pull request: every fix follows the procedure above). Coverage, according to the
official documentation read on 2026-10-06 (*Dependency graph supported package ecosystems*, *Supported ecosystems and repositories*):

| Repository file | Recognized by the dependency graph? | Consequence |
|---|---|---|
| `app/requirements.txt` | Yes (`requirements.txt` name, pip ecosystem) | Alerts on the **top-level packages** of `app` only |
| `app/requirements.lock`, `presidio/analyzer-build/requirements.lock` | **No** according to the documentation (pip: `requirements.txt` and `Pipfile.lock` only) | Neither the transitive dependencies of `app` nor **any** analyzer package are covered |
| `app/requirements-dev.txt`, `requirements-build.txt` | Not checked (name other than `requirements.txt`) | — |
| Images (`FROM`, `docker-compose.yml`) | No: Docker is supported for version updates only, not for alerts | No alert on images |

Limits: the graph is only computed on the **default branch** (`main`, behind `feat/text-api` until the merge); not checked by observation
(the SBOM export `GET /repos/EpicFail20/obfusk8/dependency-graph/sbom` answers 404 without authentication): to be checked by the
administrator in *Insights → Dependency graph* after the merge into `main`.

**GitHub alerts alone therefore do not cover our locks.** Proposed complementary source (human decision):

1. **OSV on the locks and the installed environments, weekly**, with the tool already in the repository (no new dependency): §2 steps 1
   and 2 (`tools/dependencies/outdated.py`). OSV aggregates the GitHub Advisory Database and the PyPA advisory database: the same source as
   the GitHub alerts, applied to **all** our packages. Plus `pip-audit` and `trivy` on the running images (§2 step 6).
2. **Administrator subscribed** to the security advisories (*Watch → Custom → Security alerts*) of the upstream repositories: Presidio,
   spaCy, FastAPI, Starlette, uvicorn, PyMuPDF, Pillow, lxml, Traefik, oauth2-proxy, Keycloak, and to python.org security announcements.
3. Option to assess (not adopted without a decision): rename the locks to `requirements.txt` in dedicated directories so that GitHub reads
   them; a file-naming convention change, to be checked after the merge into `main`, with no effect on the images.

The stack stays without Internet access: these checks run on the VM, never from a container of the stack.

## 9. Changing the Python version (Python phase, D-052)

The Python version is **a human decision** (§6). It is defined in **one place only**: the `x-python-base` anchor of
`docker-compose.build.yml` (official image `python:X.Y.Z-slim@sha256:…`), passed to both Dockerfiles as the `PYTHON_BASE` build argument,
with no default (a build without it fails). The publishing workflow reads the same line.

1. **At the source**: latest stable release on python.org (no release candidate); digest with
   `docker buildx imagetools inspect python:X.Y.Z-slim`; `cpXY` Linux x86_64 **binary wheels** published on PyPI for **every** compiled
   entry of the three locks (`app`, analyzer, development), and a compatible `requires_python` (Presidio, spaCy and the thinc stack declare
   a `<3.N` cap); "What's New" notes read (removals, `asyncio`, `multiprocessing`, Unicode database).
2. **Separate batches**, in this order: `app`, then the analyzer; each with its rollback (§4).
3. **Locks**: all three regenerated in the new base, with the **same versions** as constraints (§4): only the hashes of the compiled
   wheels may change (check: `git diff` limited to `--hash` lines). Development tools reinstalled in `~/.cache/obfusk8-devtools` (keep the
   previous directory for rollback); ruff `target-version` and mypy `python_version` in `app/pyproject.toml`.
4. **`versions.json` and `app/analyzer_versions.json`**: `python` field set to the new version (the `detection_config` fingerprint
   changes: the interpreter can change detection, Unicode database). The analyzer build and `app/tests/test_python_base.py` fail as long
   as the anchor, the interpreter and these files disagree.
5. **Seccomp**: full trace under `app-audit.json` (§4, system calls), kernel log filtered on the period; any new call identified
   (instruction address in `/proc/<pid>/maps`, minimal start-ups) before deciding. Move to 3.14: one `open` by mimalloc, refused with no
   effect (`seccomp/README.md`).
6. **Benchmarks** with the safeguard (§5) at each batch, latency, `trivy`, `pip-audit`, analyzer reproducibility (two builds without
   cache, same package set).

## 10. Updating Presidio, spaCy or the models (analyzer built on our own base)

Since the Python phase, the analyzer no longer depends on the image published by the Presidio project: the library comes from its PyPI
wheel (in the lock), the REST server from `presidio/analyzer-build/server/`, the models from their GitHub files (in the lock, hash-pinned).

**Presidio** (human decision if detection changes, §6):

1. New release on PyPI **and** matching GitHub tag; `requires_dist` of the wheel read (caps of numpy, spaCy, gunicorn of the `server`
   extra…).
2. **Server files** (condition of D-052 point 1): copy `app.py`, `logging.ini`, `entrypoint.sh` and `LICENSE` again from
   `presidio-analyzer/` of the new tag (`https://raw.githubusercontent.com/data-privacy-stack/presidio/<tag>/presidio-analyzer/<file>`),
   **compare** them with the previous version (`git diff presidio/analyzer-build/server/`), review every difference, update the hash table
   of `server/README.md`.
3. **Patched files**: the Dockerfile checks the hash of the original `spacy_recognizer.py` and `conf/default_recognizers.yaml` before
   replacing or patching them. If the build fails on these checks, compare the upstream original with our patch
   (`patches/spacy_recognizer.py`, `patch_recognizers.py`), carry the upstream changes into the patch, then update both hashes in the
   Dockerfile.
4. Analyzer lock (§4), `versions.json` and `app/analyzer_versions.json`, rebuild of **both** images, benchmarks with the safeguard.

**spaCy**: same path (lock, `versions.json`); check model compatibility (`spacy>=X,<Y` in their metadata).

**spaCy models**: published only as GitHub release files of `explosion/spacy-models`, **with no published digest** (D-052 point 8). For a
new version: download the wheel into `$SCRATCH`, check its size against the GitHub API
(`/repos/explosion/spacy-models/releases/tags/<model>-<version>`), compute its SHA-256, write it into the lock line (`name @ URL
--hash=sha256:…`); `check_lock.py` then compares the hash recorded at install time. The hash proves "same file as the one checked that
day", not authenticity at the origin: record the date and source in `docs/DEPENDENCIES.md`.

