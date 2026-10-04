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
Fictitious secrets in their REAL formats, rebuilt at run time.

Decision Q5 of 2026-10-04: no literal in a real secret format anywhere in
the repository, provider documentation examples included. The tests
(app/tests) and the benchmark corpora (benchmarks/) get these values from
here, assembled from fragments none of which has the format on its own:
secret scanners (and GitHub push protection) see no secret in the sources,
the tests and corpora see exactly the same strings as before.

Rebuilt: every value whose format identifies a provider or a kind of secret
(AWS, Google Cloud, Azure, GitHub, GitLab, Slack, Telegram, Stripe keys and
tokens, PEM private keys, JWT, URIs with credentials). Plain fictitious
passwords ("Fict1fDbPass") carry no format and stay literal where they are
used. Every value is fictitious: provider documentation examples or strings
spelled FAKE / Fictif.
"""


def _join(*parts: str) -> str:
    return "".join(parts)


def pem_begin(kind: str = "RSA") -> str:
    """'-----BEGIN <kind> PRIVATE KEY-----' (kind may be empty)."""
    return _join("-----BEGIN ", f"{kind} " if kind else "", "PRIVATE", " KEY-----")


def pem_end(kind: str = "RSA") -> str:
    return _join("-----END ", f"{kind} " if kind else "", "PRIVATE", " KEY-----")


def pem(kind: str, body: str) -> str:
    return f"{pem_begin(kind)}\n{body}\n{pem_end(kind)}"


PGP_BEGIN = _join("-----BEGIN PGP ", "PRIVATE KEY", " BLOCK-----")

# AWS documentation examples (access key id, temporary access key id, secret key).
AWS_ACCESS_KEY = _join("AK", "IA", "IOSFODNN7", "EXAMPLE")
AWS_TEMPORARY_ACCESS_KEY = _join("AS", "IA", "IOSFODNN7", "EXAMPLE")
AWS_ACCESS_KEY_LETTERS = _join("AK", "IA", "FICTIVEONLY", "LETTERS")
AWS_SECRET_KEY = _join("wJalrXUtnFEMI", "/K7MDENG/", "bPxRfiCYEXAMPLEKEY")
# Google Cloud documentation example (Manage API keys).
GOOGLE_API_KEY = _join("AI", "za", "SyDaGmWKa4JsXZ", "-HjGw7ISLn_3namBGewQe")
# Azurite storage emulator key (Microsoft documentation) and a documentation SAS signature.
AZURITE_ACCOUNT_KEY = _join(
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50", "uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)
AZURE_SAS_SIGNATURE = _join("9aCzs76n0E7y5BpEi2GvsSv433BZa22", "leDOZXX%2BXXIU%3D")
GITHUB_TOKEN = _join("gh", "p_", "FAKEfake" * 3, "0123456789")
GITHUB_FINE_GRAINED_TOKEN = _join("github", "_pat_", "11FAKEFAKE0_", "fakeFAKEfake0123456789")
GITLAB_TOKEN = _join("gl", "pat-", "FakeFakeFake", "0123456789")
GITLAB_RUNNER_TOKEN = _join("gl", "rt-", "FakeFakeFake", "0123456789")
GITLAB_DEPLOY_TOKEN = _join("gl", "dt-", "FakeFakeFake", "0123456789")
SLACK_BOT_TOKEN = _join("xo", "xb-", "FICTIF-NON-VALIDE-", "FAKE" * 5, "000")
SLACK_BOT_TOKEN_SHORT = _join("xo", "xb-", "0000000000-", "FAKE" * 3)
SLACK_REFRESH_TOKEN = _join("xo", "xe.xapp-1-", "FAKE" * 3, "0")
# Telegram Bot API documentation example.
TELEGRAM_BOT_TOKEN = _join("123456", ":", "ABC-DEF1234ghIkl", "-zyx57W2v1u123ew11")
STRIPE_TEST_KEY = _join("sk", "_test_", "FAKE" * 4, "1234")
STRIPE_TEST_KEY_SHORT = _join("sk", "_test_", "FAKE" * 3, "1234")
STRIPE_RESTRICTED_KEY = _join("rk", "_live_", "FAKE" * 3, "1234")
STRIPE_WEBHOOK_SECRET = _join("wh", "sec_", "FAKE" * 4, "1234")
STRIPE_WEBHOOK_SECRET_SHORT = _join("wh", "sec_", "FAKE" * 3, "1234")
JWT_HS256 = _join("ey", "JhbGciOiJIUzI1NiJ9", ".", "ey", "JzdWIiOiJmaWN0aWYifQ", ".", "c2lnbmF0dXJlLWZpY3RpdmU")
JWT_UNSIGNED = _join("ey", "JhbGciOiJub25lIn0", ".", "ey", "Jpc3MiOiJmaWN0aWYifQ", ".")
JWT_EXPIRED = _join(
    "ey",
    "JhbGciOiJIUzI1NiJ9",
    ".",
    "ey",
    "JzdWIiOiJmaWN0aWYiLCJleHAiOjE3MDAwMDAwMDB9",
    ".",
    "FAKEsignature" * 2,
    "FAKE",
)


def credentials_uri(scheme: str, user: str, password: str, host: str) -> str:
    """'<scheme>://<user>:<password>@<host>' — the credential-in-URI format."""
    return _join(scheme, "://", user, ":", password, "@", host)
