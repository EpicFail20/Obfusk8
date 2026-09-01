# Anonymiseur - déploiement lab sur VM Docker (Proxmox)

## 1. Copier le stack sur la VM clonée

```bash
scp -r anonymiseur-stack-lab debian@<ip-de-la-vm>:~/anonymiseur
ssh debian@<ip-de-la-vm>
cd anonymiseur
```

## 2. Ajouter `anonymiseur.lab.local` dans /etc/hosts (poste client, pas la VM)

```
<ip-de-la-vm>  anonymiseur.lab.local
```

## 3. Démarrer Keycloak seul d'abord, le configurer, puis le reste

```bash
docker compose up -d keycloak
```

- Ouvrir `http://<ip-de-la-vm>:8081` (admin/admin)
- Créer un realm `lab`
- Créer un groupe `SG-Anonymiseur-Utilisateurs`
- Créer un client OIDC `anonymiseur-app` (confidentiel), redirect URI :
  `https://anonymiseur.lab.local/oauth2/callback`
- Ajouter un mapper "Group Membership" -> claim `groups`
- Créer 1-2 utilisateurs de test, les ajouter au groupe
- Récupérer le client secret -> le mettre dans `.env`

## 4. Lancer le reste du stack

```bash
cp .env.example .env
# éditer .env avec le client secret + cookie secret générés

docker compose up -d --scale presidio-analyzer=2 --scale presidio-anonymizer=1
docker compose ps
```

(2 réplicas suffisent pour un test fonctionnel ; monte à 6/2 uniquement pour
le test de charge à 50 utilisateurs concurrents, cf. dimensionnement déjà vu :
12-16 vCPU / 24-32 Go dans ce cas.)

## 5. Vérifier

```bash
docker compose logs -f app
docker compose logs -f oauth2-proxy
```

Ouvrir `https://anonymiseur.lab.local` -> tu dois être redirigé vers Keycloak,
te connecter avec un utilisateur de test du groupe, puis atterrir sur l'app.

## Bascule vers Entra ID plus tard

Dans `oauth2-proxy.cfg`, remplacer `provider = "oidc"` par `provider =
"entra-id"` et pointer `oidc_issuer_url` vers
`https://login.microsoftonline.com/<tenant-id>/v2.0`, avec le vrai
`client_id`/`client_secret` Entra. Aucun autre fichier ne change.
