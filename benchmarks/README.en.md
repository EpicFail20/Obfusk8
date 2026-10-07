# Text API benchmarks

> Version française : [`README.md`](./README.md).

**Quality** (recall, precision) and **latency** measurements of the extension's text API (`/api/v1/`, see `docs/api-extension.en.md`),
always **through the full stack**: Traefik, oauth2-proxy, Keycloak, `app` under the enforcing seccomp profile, `presidio-analyzer`.
Nothing is measured behind Traefik's back.

**No success threshold.** The first run sets the baseline; later runs compare against it. Observed false negative families are recorded
in `docs/FINDINGS.md`.

## Contents

| Directory | Purpose |
|---|---|
| `obfusk8_client.py` | Shared client: logs in through the Keycloak form like a browser, paced calls under the rate limit (60/min) |
| `collect_stack_info.sh` | Stack versions (images, Presidio, spaCy and models, theme files) for the reports; read-only |
| `secret_detection/` | Secret detection (step D): false positives on text and code without secrets, recall per format |
| `quality/` | Detection quality (step E): annotated prompt corpus and adversarial variants |
| `latency/` | Latency per prompt size, and cross effect with the document flow |
| `documents/` | Document flow (phase 2): `doc_zones_snapshot.py`, snapshot of the redaction zones offered by `/api/detect` (PDF, DOCX, CSV, image, each theme) and comparison of two snapshots (no reference zone may disappear); `pdf_localization_bench.py`, PDF detections not located or partially exposed after redaction (EXT-35) |
| `results/` | Timestamped reports (JSON and Markdown) |

## Corpus: how it is produced, why it is fictitious

The corpora are **generated** by `secret_detection/build_secret_corpus.py` and `quality/build_quality_corpus.py`, deterministically
(fixed seed): regenerating gives a byte-identical file.

- Each prompt of `quality/corpus.jsonl` is written as a sequence of **segments** (free text, or an entity of a known type). The offsets
  `{start, end, type}` are computed by concatenation, never by searching the text: the annotation is exact by construction.
- **Adversarial variants** (segment by segment, offsets recomputed): typographic dashes and apostrophes, non-breaking and narrow
  non-breaking spaces, zero-width characters, NFD form, spaced digits (phone, IBAN, social security number, card), names in capitals
  (`DURAND Camille`), multi-line addresses, names in a dense context (staff rota without sentences).
- **Fictitious values**:
  - phone numbers **only** in the ranges the French numbering plan reserves for fiction
    (Arcep decision 2019-0954, s.2.5.12 "Numéros pour œuvres audiovisuelles": 01 99 00, 02 61 91, 03 53 01, 04 65 71, 05 36 49, 06 39 98);
  - email domains reserved for documentation (`example.com`, `example.org`, RFC 2606) and an internal domain under `.invalid` (RFC 2606);
  - names: common first and last names combined at random, no real person is described;
  - IBAN, social security and card numbers generated with a valid check digit from random digits (so recognizers that validate it can
    fire); cards start with 4970;
  - secrets: provider documentation examples (the AWS example access key, the Azurite emulator key, the Telegram example token) or
    strings spelled `FAKE` / `Fictif`. Every value in a real secret format is **rebuilt at run time** by `app/tests/fake_secrets.py`
    from fragments (decision Q5 of 2026-10-04): none is written as is in the repository. The corpora `quality/corpus.jsonl` and
    `secret_detection/tp_corpus.jsonl` are no longer versioned: the benchmarks build them in memory (byte for byte the phase 1 files).

## Running

On the stack's VM, with a **synthetic** test account (never in the repository):

```sh
export BENCH_BASE_URL=https://obfusk8.lab.local BENCH_USER=<test account> BENCH_PASSWORD=<password>
export BENCH_INSECURE_TLS=1        # lab self-signed certificate
export BENCH_RESOLVE_ADDRESS=192.168.1.35  # if *.lab.local is not in /etc/hosts: the stack's BIND_ADDRESS (D-047)
sh benchmarks/collect_stack_info.sh > /tmp/stack.json
export BENCH_STACK_INFO=/tmp/stack.json
python3 benchmarks/secret_detection/run_secrets_bench.py
python3 benchmarks/quality/run_quality_bench.py      # about 1,000 requests, a little under one per second
python3 benchmarks/latency/run_latency_bench.py
```

Requirements: `ENABLE_EXTENSION_API=true`, Python 3 with `requests`. The runs write to the extension audit log (`audit-extension.log`,
metadata only); the latency benchmark creates document review jobs (synthetic CSV) that expire by themselves (`JOB_REVIEW_TTL_SECONDS`),
never finalized, so nothing is written to `audit.log`.

## Reading the results

- **Strict**: same interval and an accepted type (`ACCEPTED` in `run_quality_bench.py`; e.g. an annotated address accepts
  `STREET_ADDRESS` or `LOCATION`).
- **Overlap**: the intervals overlap and the type is accepted.
- **Masking** (type-agnostic): the annotated value is **entirely** covered by the union of the detections, whatever their type.
  This is what matters for a leak: a partially covered value leaks the rest.
- Precision only covers predicted types of the annotated vocabulary; the others (`ORGANIZATION`, `URL`…) are listed separately, since
  the corpus does not annotate them.

Limits: synthetic, modest-size corpus (257 prompts) written by the same team as the recognizers; a single test account, hence a prompt
flow limited to one heavy user.
