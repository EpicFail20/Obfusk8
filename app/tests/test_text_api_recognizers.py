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
Text API recognizers (app/themes/extension/*.json) and, since phase 2, the
common recognizers of every flow (app/themes/common.json): each pattern is compiled
EXACTLY as presidio-analyzer compiles an ad hoc recognizer — `regex` module
(same version as in the analyzer image, requirements-dev.in) with
IGNORECASE|DOTALL|MULTILINE — then checked on synthetic true and false
positives, and probed for catastrophic backtracking at the MAX_TEXT_CHARS cap.

Every value below is FICTITIOUS (provider documentation examples such as
AKIAIOSFODNN7EXAMPLE, or obviously fake strings).

The `regex` module is a development dependency only: skipped where absent
(e.g. inside the production image, which ships pytest — EXT-09).
"""

import sys
import time
from pathlib import Path

import pytest

regex = pytest.importorskip("regex")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402
import text_api  # noqa: E402

PRESIDIO_FLAGS = regex.DOTALL | regex.MULTILINE | regex.IGNORECASE
# Since phase 2 (decision Q4, EXT-08/EXT-23), the card, NIR and any-domain
# email recognizers are common to every flow (app/themes/common.json): the
# common recognizers are checked here too, with the extension's own.
RECOGNIZERS = [
    *main.COMMON_RECOGNIZERS,
    *text_api.load_extension_recognizers(main.THEMES_DIR / "extension", main.THEMES),
]
COMPILED = [
    (r["name"], r["supported_entity"], regex.compile(p["regex"], PRESIDIO_FLAGS))
    for r in RECOGNIZERS
    for p in r["patterns"]
]


def _found(text: str) -> set[tuple[str, str]]:
    """Matches on the text EXACTLY as presidio-analyzer receives it: after
    main.py's length-preserving normalization (all-caps words capitalized,
    typographic dashes). Regression: case-sensitive PEM markers matched the
    raw text in these tests but never reached Presidio as "RSA PRIVATE KEY"
    (normalized to "Rsa Private Key") — a false negative found by the
    end-to-end secrets benchmark. Values are read back from the raw text."""
    normalized = main._normalize_dashes(main._normalize_allcaps(text))
    assert len(normalized) == len(text)
    return {(entity, text[m.start() : m.end()]) for _, entity, c in COMPILED for m in c.finditer(normalized)}


def _values(text: str, entity: str = "SECRET") -> set[str]:
    return {value for e, value in _found(text) if e == entity}


def test_invariants_de_tous_les_reconnaisseurs():
    """Shape expected by Presidio's ad hoc API, French language (the
    analyzer always runs with ANALYZER_LANGUAGE=fr), and a score that no
    threshold can filter out: every theme and DEFAULT_SCORE_THRESHOLD are
    at most 0.4 — a secret must never be dropped by score (doctrine 0.2)."""
    thresholds = [main.DEFAULT_SCORE_THRESHOLD] + [t.get("score_threshold", 0) for t in main.THEMES.values()]
    for r in RECOGNIZERS:
        assert r["name"] and r["supported_entity"] and r["patterns"], r
        assert r["supported_language"] == main.LANGUAGE
        for p in r["patterns"]:
            assert p["name"] and p["score"] > max(thresholds), (r["name"], p["name"])


# ---------------------------------------------------------------------------
# True positives (one per documented format) and false positives
# ---------------------------------------------------------------------------

PEM_BODY = "MIIEowIBAAKCAQEAuF4x0aBcDeFgHiJkLmNoPqRsTuVwXyZ\nabcdEFGHijklMNOPqrstUVWX0123"


@pytest.mark.parametrize(
    "text,expected",
    [
        # RFC 7468 / legacy labels, complete block
        (
            f"cle :\n-----BEGIN RSA PRIVATE KEY-----\n{PEM_BODY}\n-----END RSA PRIVATE KEY-----\nfin",
            f"-----BEGIN RSA PRIVATE KEY-----\n{PEM_BODY}\n-----END RSA PRIVATE KEY-----",
        ),
        (
            f"-----BEGIN ENCRYPTED PRIVATE KEY-----\n{PEM_BODY}\n-----END ENCRYPTED PRIVATE KEY-----",
            f"-----BEGIN ENCRYPTED PRIVATE KEY-----\n{PEM_BODY}\n-----END ENCRYPTED PRIVATE KEY-----",
        ),
        # OpenSSH (sshkey.c MARK_BEGIN), truncated paste: header and base64 body
        (
            f"-----BEGIN OPENSSH PRIVATE KEY-----\n{PEM_BODY}\npuis du texte",
            f"-----BEGIN OPENSSH PRIVATE KEY-----\n{PEM_BODY}",
        ),
        # RFC 9580 s.6.2.1 with armor headers
        (
            "-----BEGIN PGP PRIVATE KEY BLOCK-----\nVersion: Fictif 1.0\n\nlQOYBGFakeFakeFakeFake\n=AbCd\n"
            "-----END PGP PRIVATE KEY BLOCK-----",
            "-----BEGIN PGP PRIVATE KEY BLOCK-----\nVersion: Fictif 1.0\n\nlQOYBGFakeFakeFakeFake\n=AbCd\n"
            "-----END PGP PRIVATE KEY BLOCK-----",
        ),
        # RFC 7519 s.3.1 / s.6.1 (unsecured, empty signature)
        (
            "jeton eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaWN0aWYifQ.c2lnbmF0dXJlLWZpY3RpdmU fin",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaWN0aWYifQ.c2lnbmF0dXJlLWZpY3RpdmU",
        ),
        ("eyJhbGciOiJub25lIn0.eyJpc3MiOiJmaWN0aWYifQ. suite", "eyJhbGciOiJub25lIn0.eyJpc3MiOiJmaWN0aWYifQ."),
        # RFC 3986 userinfo
        (
            "DATABASE_URL=postgresql://appuser:Fict1f-S3cret@db.exemple.invalid:5432/app",
            "postgresql://appuser:Fict1f-S3cret@db.exemple.invalid:5432",
        ),
        (
            "mongodb+srv://lecteur:Pa55Fictif@cluster0.exemple.invalid/base",
            "mongodb+srv://lecteur:Pa55Fictif@cluster0.exemple.invalid",
        ),
        # Assignments
        ('export MYSQL_ROOT_PASSWORD="Fictif mot2passe"', '"Fictif mot2passe"'),
        ("password: hunter22", "hunter22"),
        ('{"api_key": "abcd1234efgh"}', '"abcd1234efgh"'),
        ("spring.datasource.password=Fict1fJdbc", "Fict1fJdbc"),
        ("Le mot de passe est Lune#Fictive42 pour le compte de test.", "Lune#Fictive42"),
        ("mdp : Soleil2026!", "Soleil2026!"),
        (
            "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ),
        # Azure (Microsoft Learn connection string page, emulator example key)
        (
            "DefaultEndpointsProtocol=https;AccountName=fictif;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IF"
            "suFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;EndpointSuffix=core.windows.net",
            "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==",
        ),
        (
            "https://fictif.blob.core.windows.net/c?sv=2015-04-05&sr=b&sig=9aCzs76n0E7y5BpEi2GvsSv433BZa22leDOZXX%2BXXIU%3D",
            "9aCzs76n0E7y5BpEi2GvsSv433BZa22leDOZXX%2BXXIU%3D",
        ),
        # HTTP Authorization
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123", "abcdefghijklmnopqrstuvwxyz0123"),
        ("curl -H 'Authorization: Basic dXNlcjpmaWN0aWY='", "dXNlcjpmaWN0aWY="),
        # Providers (documentation example values or obviously fake)
        ("cle AKIAIOSFODNN7EXAMPLE fin", "AKIAIOSFODNN7EXAMPLE"),
        ("temporaire ASIAIOSFODNN7EXAMPLE", "ASIAIOSFODNN7EXAMPLE"),
        ("cle AKIAFICTIVEONLYLETTERS", "AKIAFICTIVEONLYLETTERS"),  # all letters: capitalized by normalization
        ("key=AIzaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGewQe", "AIzaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGewQe"),
        ("ghp_FAKEfakeFAKEfakeFAKEfake0123456789", "ghp_FAKEfakeFAKEfakeFAKEfake0123456789"),
        ("github_pat_11FAKEFAKE0_fakeFAKEfake0123456789", "github_pat_11FAKEFAKE0_fakeFAKEfake0123456789"),
        ("glpat-FakeFakeFake0123456789", "glpat-FakeFakeFake0123456789"),
        ("gldt-FakeFakeFake0123456789", "gldt-FakeFakeFake0123456789"),
        ("xoxb-0000000000-FAKEFAKEFAKE", "xoxb-0000000000-FAKEFAKEFAKE"),
        ("xoxe.xapp-1-FAKEFAKEFAKE0", "xoxe.xapp-1-FAKEFAKEFAKE0"),
        ("bot 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"),
        ("sk_test_FAKEFAKEFAKE1234", "sk_test_FAKEFAKEFAKE1234"),
        ("rk_live_FAKEFAKEFAKE1234", "rk_live_FAKEFAKEFAKE1234"),
        ("whsec_FAKEFAKEFAKE1234", "whsec_FAKEFAKEFAKE1234"),
    ],
)
def test_secret_detecte(text, expected):
    found = _values(text)
    assert any(expected in value or value == expected for value in found), (expected, found)
    assert max((len(v) for v in found if expected in v or v in expected), default=0) >= len(expected) - 1


@pytest.mark.parametrize(
    "text",
    [
        "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA\n-----END PUBLIC KEY-----",
        "-----BEGIN CERTIFICATE-----\nMIIDdzCCAl+gAwIBAgIEAgAAuTANBgkqhkiG9w0BAQUFADBa\n-----END CERTIFICATE-----",
        "password = os.environ['DB_PASSWORD']",
        "password = process.env.DB_PASSWORD",
        'password: "${DB_PASSWORD}"',
        "password: {{ vault_password }}",
        "enabled: true\npassword_reset_url = https://exemple.invalid/reset",
        "token_type: bearer ; max_tokens: 1024 ; secret_santa = oui",
        "PASSWORD_MIN_LENGTH = 12",
        # Regressions from the end-to-end false positive measurement (fp-014, fp-016, fp-017):
        "if not password:\n    raise ValueError('password required')",
        "API_TOKEN = os.getenv('API_TOKEN')",
        "const apiKey = config.get('apiKey');",
        "token = request.headers['X-Token']",
        "akiaiosfodnn7example",  # AWS prefixes are uppercase (case-sensitive pattern)
        "pk_live_FAKEFAKEFAKE1234",  # Stripe publishable key: public by design
        "ssh://git@forge.exemple.invalid/projet.git",  # no password in the userinfo
        "La réunion est à 12:30 dans la salle Basic.",
        "Le mot de passe a été réinitialisé hier.",
        "sha256: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "uuid 123e4567-e89b-12d3-a456-426614174000",
    ],
)
def test_pas_de_faux_positif_secret(text):
    assert _values(text) == set(), _values(text)


def test_identifiants_couverts_ext08_ext23():
    text = (
        "Carte 4970 1012 3456 7890 ; NIR 1 84 12 75 123 456 78 ; "
        "contact jean.dupont@hopital.local ou j.martin@intranet.chu.internal"
    )
    found = _found(text)
    assert ("CREDIT_CARD", "4970 1012 3456 7890") in found
    assert any(e == "FR_NIR" and "84 12 75 123 456 78" in v for e, v in found), found
    assert ("EMAIL_ADDRESS", "jean.dupont@hopital.local") in found
    assert ("EMAIL_ADDRESS", "j.martin@intranet.chu.internal") in found


# ---------------------------------------------------------------------------
# ReDoS: adversarial inputs at the MAX_TEXT_CHARS cap
# ---------------------------------------------------------------------------

CAP = text_api.TextApiSettings().max_text_chars
ADVERSARIAL = [
    "-----BEGIN RSA PRIVATE KEY-----\n" + "A" * CAP,
    "-----BEGIN " + "A " * (CAP // 2),
    ("-----BEGIN RSA PRIVATE KEY-----\n" + "QUJD " * 10) * (CAP // 100),
    "eyJ" + "a" * CAP + ".eyJ" + "b" * 10,
    "eyJa.eyJ" * (CAP // 8),
    "a://" + "u" * 256 + ":" + "p" * (CAP - 300),
    "x://u:" * (CAP // 6),
    "password=" * (CAP // 9),
    "password " * (CAP // 9) + "= x",
    "mot de passe " * (CAP // 13),
    "Authorization: Bearer " + "a" * CAP,
    "AKIA" + "A" * CAP,
    "123:" * (CAP // 4),
    "1" * 15 + ":" + "a-" * (CAP // 2),
    "a" * 64 + "@" + "b." * (CAP // 2),
    ".@" * (CAP // 2),
    "4" + " 123" * (CAP // 4),
]


@pytest.mark.parametrize("probe", range(len(ADVERSARIAL)))
def test_pas_de_retour_arriere_catastrophique(probe):
    """Each pattern over each adversarial input at the size cap: must stay
    far below the analysis budget (MAX_TEXT_ANALYSIS_SECONDS = 10 s). The
    bound, 0.5 s per input for ALL patterns together, leaves the time to the
    NER model, which dominates (0.71 s measured at 20 000 characters)."""
    text = ADVERSARIAL[probe][:CAP]
    started = time.perf_counter()
    for _, _, compiled in COMPILED:
        for _ in compiled.finditer(text):
            pass
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"probe {probe}: {elapsed:.2f} s"
