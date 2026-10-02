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
Builds the secrets corpus (deterministic, no randomness): writes
fp_corpus.jsonl (ordinary text and code WITHOUT any secret, to count false
positives) and tp_corpus.jsonl (one fictitious secret per documented format,
each embedded in a realistic prompt, to measure recall).

Every value is fictitious: provider documentation examples
(AKIAIOSFODNN7EXAMPLE, the Azurite emulator key, the Telegram doc token) or
strings spelled FAKE/Fictif. Domains use the reserved .invalid TLD (RFC 2606)
except where the format itself is the point.

Usage: python3 benchmarks/secret_detection/build_corpus.py
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAFAKEFAKEFAKEuF4x0aBcDeFgHiJkLmNoPqRsTuVwXyZ01234\n"
    "abcdEFGHijklMNOPqrstUVWXfakeFAKEfakeFAKEfakeFAKEfakeFAKEfake5678\n"
    "-----END RSA PRIVATE KEY-----"
)

# --- Ordinary text and code: NO secret anywhere -----------------------------
FP = [
    (
        "fr",
        "prose",
        "Bonjour à tous, la réunion de lancement aura lieu mardi à 14 h en salle B. Merci de préparer "
        "un point d'avancement de cinq minutes chacun. Le compte rendu sera partagé sur l'espace d'équipe.",
    ),
    (
        "fr",
        "prose",
        "Suite à la panne d'hier, j'ai réinitialisé le mot de passe du poste d'accueil comme demandé ; "
        "l'utilisateur devra le changer à la première connexion. Pensez à vider le cache du navigateur.",
    ),
    (
        "fr",
        "prose",
        "Le secret de fabrication de cette recette tient en trois mots : patience, beurre et four chaud. "
        "Laissez reposer la pâte une heure au frais avant de l'étaler.",
    ),
    (
        "fr",
        "prose",
        "Peux-tu résumer ce texte en trois points ? Il parle de la politique de sécurité : les jetons "
        "d'accès expirent après une heure, et la clé USB chiffrée reste la règle pour les transferts.",
    ),
    (
        "fr",
        "prose",
        "Le passe Navigo est remboursé à 50 % par l'employeur. Envoyez le justificatif avant le 15 du mois.",
    ),
    (
        "fr",
        "ticket",
        "Ticket INC-20931 : le service d'impression ne répond plus depuis la mise à jour. Redémarrage "
        "effectué à 09:42, retour à la normale constaté à 09:47. Cause probable : file d'attente saturée.",
    ),
    (
        "fr",
        "ticket",
        "Demande : créer un compte pour la nouvelle stagiaire, profil standard, sans droits "
        "d'administration. Le mot de passe initial sera transmis par téléphone, pas par courriel.",
    ),
    (
        "fr",
        "contrat",
        "Article 7 \u2013 Confidentialité. Les parties s'engagent à garder secrètes les informations "
        "échangées pendant toute la durée du contrat et cinq ans après son terme.",
    ),
    (
        "fr",
        "rh",
        "Rappel : l'entretien annuel se déroule en deux temps, bilan de l'année puis objectifs. "
        "Le formulaire est disponible dans l'outil RH, rubrique Carrière.",
    ),
    (
        "fr",
        "facture",
        "Facture n° 2026-0412 du 3 mars 2026, montant HT 1 250,00 €, TVA 20 %, total TTC 1 500,00 €. "
        "Référence commande 4500012345, échéance 30 jours fin de mois.",
    ),
    (
        "en",
        "prose",
        "Please summarize the attached meeting notes. The token budget for this task is about two "
        "thousand words, and the key points are the release date and the open risks.",
    ),
    (
        "en",
        "prose",
        "Our password policy requires twelve characters minimum and rotation every ninety days; "
        "the reset link expires after fifteen minutes.",
    ),
    ("en", "prose", "The secret to a good code review is asking questions rather than giving orders."),
    (
        "code",
        "python",
        "import os\n\nDB_PASSWORD = os.environ['DB_PASSWORD']\nAPI_TOKEN = os.getenv('API_TOKEN')\n"
        "\ndef connect():\n    return create_engine(f'postgresql://{USER}@{HOST}/app', pool_size=5)\n",
    ),
    (
        "code",
        "python",
        "class Tokenizer:\n    def __init__(self, max_tokens: int = 1024):\n        self.max_tokens = "
        "max_tokens\n\n    def count(self, text: str) -> int:\n        return len(text.split())\n",
    ),
    (
        "code",
        "python",
        "PASSWORD_MIN_LENGTH = 12\nSECRET_KEY_FILE = '/run/secrets/app_key'\n"
        "token_type = 'bearer'\nif not password:\n    raise ValueError('password required')\n",
    ),
    (
        "code",
        "js",
        "const password = process.env.DB_PASSWORD;\nconst apiKey = config.get('apiKey');\n"
        "fetch(url, { headers: { Authorization: `Bearer ${token}` } });\n",
    ),
    (
        "code",
        "js",
        "export function hash(input) {\n  return crypto.createHash('sha256').update(input).digest('hex');\n}\n"
        "// e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n",
    ),
    (
        "code",
        "java",
        'public class UserService {\n    @Value("${spring.datasource.password}")\n    private String '
        "password;\n    private static final int TOKEN_TTL_SECONDS = 3600;\n}\n",
    ),
    (
        "code",
        "sql",
        "CREATE TABLE users (id UUID PRIMARY KEY, email TEXT NOT NULL, password_hash TEXT NOT NULL, "
        "created_at TIMESTAMPTZ DEFAULT now());\nSELECT id, email FROM users WHERE id = "
        "'123e4567-e89b-12d3-a456-426614174000';\n",
    ),
    (
        "code",
        "yaml",
        "apiVersion: v1\nkind: Secret\nmetadata:\n  name: app-credentials\ntype: Opaque\n"
        'stringData:\n  password: "{{ .Values.dbPassword }}"\n  token: "${API_TOKEN}"\n',
    ),
    (
        "code",
        "yaml",
        "services:\n  db:\n    image: postgres:16\n    environment:\n      POSTGRES_PASSWORD_FILE: "
        "/run/secrets/db_password\n    secrets:\n      - db_password\n",
    ),
    (
        "code",
        "shell",
        "#!/bin/sh\nset -eu\nread -r -s -p 'Mot de passe : ' password\necho\n"
        "git log --oneline -3\n# 1a2b3c4 Corrige le test\n# 9f8e7d6 Ajoute la page d'accueil\n",
    ),
    (
        "code",
        "shell",
        "ssh-keygen -t ed25519 -C 'poste-dev'\ncat ~/.ssh/id_ed25519.pub\n"
        "git clone ssh://git@forge.exemple.invalid/equipe/projet.git\n",
    ),
    (
        "code",
        "config",
        "server {\n    listen 443 ssl;\n    ssl_certificate /etc/nginx/certs/site.pem;\n"
        "    ssl_certificate_key /etc/nginx/certs/site.key;\n    location / { proxy_pass http://app:8000; }\n}\n",
    ),
    (
        "code",
        "json",
        '{"name": "obfusk8-ui", "version": "1.4.2", "scripts": {"build": "vite build", "test": '
        '"vitest"}, "dependencies": {"jsonwebtoken": "^9.0.0"}}',
    ),
    (
        "code",
        "pem-public",
        "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAfakeFAKEfakeFAKE\n"
        "-----END PUBLIC KEY-----",
    ),
    (
        "code",
        "pem-cert",
        "-----BEGIN CERTIFICATE-----\nMIIDdzCCAl+gAwIBAgIEAgAAuTANBgkqhkiG9w0BAQUFADBafake\n-----END CERTIFICATE-----",
    ),
    (
        "fr",
        "chiffres",
        "Commande 4500012345 livrée ; SIRET de l'entrepôt 123 456 789 00012 ; colis 6A12345678901 ; "
        "lot 2026-03-0457 ; numéro de série SN-88231-XK.",
    ),
    (
        "fr",
        "chiffres",
        "Le tableau compte 1 234 567 lignes ; le temps moyen est de 12:34:56 ; "
        "la version 3.14.159 est déployée sur 42 serveurs.",
    ),
]

# --- One fictitious secret per documented format, inside a realistic prompt --
TP = [
    ("pem", "Pourquoi ma clé ne marche pas ?\n" + PEM, [PEM]),
    (
        "pem-truncated",
        "Voici le début de ma clé : -----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\nla suite est coupée",
        ["-----BEGIN OPENSSH PRIVATE KEY-----"],
    ),
    (
        "jwt",
        "Ce jeton est-il expiré ? eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaWN0aWYiLCJleHAiOjE3MDAwMDAwMDB9."
        "FAKEsignatureFAKEsignatureFAKE",
        ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaWN0aWYiLCJleHAiOjE3MDAwMDAwMDB9.FAKEsignatureFAKEsignatureFAKE"],
    ),
    (
        "uri-credentials",
        "Erreur de connexion avec DATABASE_URL=postgresql://appuser:Fict1f-S3cret@db.exemple.invalid"
        ":5432/app, une idée ?",
        ["Fict1f-S3cret"],
    ),
    (
        "uri-credentials",
        "redis://default:FakeRedisPass42@cache.exemple.invalid:6379/0 ne répond pas",
        ["FakeRedisPass42"],
    ),
    ("assignment", "Mon .env contient DB_PASSWORD=Fict1fDbPass et l'appli plante.", ["Fict1fDbPass"]),
    ("assignment", 'config.yaml :\nadmin:\n  password: "Fictif mot2passe"\n', ["Fictif mot2passe"]),
    (
        "assignment",
        'Réponse de l\'API : {"access_token": "FAKEaccessTOKEN123456", "expires_in": 3600}',
        ["FAKEaccessTOKEN123456"],
    ),
    ("assignment", "Pour le compte de test, le mot de passe est Lune#Fictive42.", ["Lune#Fictive42"]),
    (
        "assignment",
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ["wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"],
    ),
    (
        "http-auth",
        "curl -H 'Authorization: Bearer FAKEbearerTOKENfake0123456789' https://api.exemple.invalid",
        ["FAKEbearerTOKENfake0123456789"],
    ),
    ("aws", "Les clés sont AKIAIOSFODNN7EXAMPLE et je ne sais plus laquelle utiliser.", ["AKIAIOSFODNN7EXAMPLE"]),
    (
        "google",
        "La clé AIzaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGewQe renvoie une erreur 403.",
        ["AIzaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGewQe"],
    ),
    (
        "azure",
        "DefaultEndpointsProtocol=https;AccountName=fictif;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50"
        "uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;EndpointSuffix=core.windows.net",
        ["Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="],
    ),
    (
        "azure-sas",
        "Le lien https://fictif.blob.core.windows.net/c/f.pdf?sv=2015-04-05&sr=b&sig="
        "9aCzs76n0E7y5BpEi2GvsSv433BZa22leDOZXX%2BXXIU%3D a expiré ?",
        ["9aCzs76n0E7y5BpEi2GvsSv433BZa22leDOZXX%2BXXIU%3D"],
    ),
    (
        "github",
        "git push refuse mon jeton ghp_FAKEfakeFAKEfakeFAKEfake0123456789",
        ["ghp_FAKEfakeFAKEfakeFAKEfake0123456789"],
    ),
    ("gitlab", "Le runner utilise glrt-FakeFakeFake0123456789 et échoue.", ["glrt-FakeFakeFake0123456789"]),
    (
        "slack",
        "Le bot Slack (xoxb-FICTIF-NON-VALIDE-FAKEFAKEFAKEFAKEFAKE000) ne poste plus.",
        ["xoxb-FICTIF-NON-VALIDE-FAKEFAKEFAKEFAKEFAKE000"],
    ),
    (
        "telegram",
        "Mon bot 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11 ne reçoit rien.",
        ["123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"],
    ),
    (
        "stripe",
        "En test j'utilise sk_test_FAKEFAKEFAKEFAKE1234 et le webhook whsec_FAKEFAKEFAKEFAKE1234.",
        ["sk_test_FAKEFAKEFAKEFAKE1234", "whsec_FAKEFAKEFAKEFAKE1234"],
    ),
]


def main() -> None:
    with open(HERE / "fp_corpus.jsonl", "w", encoding="utf-8") as f:
        for i, (lang, kind, text) in enumerate(FP, 1):
            f.write(
                json.dumps({"id": f"fp-{i:03d}", "lang": lang, "kind": kind, "text": text}, ensure_ascii=False) + "\n"
            )
    with open(HERE / "tp_corpus.jsonl", "w", encoding="utf-8") as f:
        for i, (family, text, secrets) in enumerate(TP, 1):
            for s in secrets:
                if s not in text:
                    raise ValueError(f"{family}: secret not in its text")
            f.write(
                json.dumps(
                    {"id": f"tp-{i:03d}", "family": family, "text": text, "secrets": secrets}, ensure_ascii=False
                )
                + "\n"
            )
    print(f"{len(FP)} textes sans secret, {len(TP)} textes avec secret")


if __name__ == "__main__":
    main()
