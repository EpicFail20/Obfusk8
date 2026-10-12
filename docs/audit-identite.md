# Identité dans l'audit : pseudonymisation et ré-identification (note pour la DPO)

> English version: [`audit-identity.md`](./audit-identity.md).
> Décision humaine du 2026-10-12 (D-057 point 9). Procédure de ré-identification **proposée, à valider par la DPO**.

## 1. Ce que l'audit enregistre

Depuis la phase « option 2 », l'identité de l'utilisateur, dans **tous** les flux (interface documents et API texte de l'extension), est le
**`sub`** du fournisseur d'identité : un identifiant technique opaque (au laboratoire, l'identifiant interne Keycloak, de forme UUID),
transmis par oauth2-proxy dans l'en-tête `X-Auth-Request-User`. Le courriel n'est plus utilisé comme identité.

Le `sub` sert de clé pour :
- le **journal d'audit** des documents (`/var/log/anonymiseur-audit/audit.log`, champ `user`) et de l'API texte (`audit-extension.log`,
  champ `user`) ;
- le **quota** de documents en attente par utilisateur et la **propriété** d'un document (aperçu, finalisation, annulation) ;
- la **limitation de débit** par utilisateur (Traefik).

L'audit ne contient jamais de contenu (texte, valeurs détectées, noms de fichiers en clair) : seulement des métadonnées (utilisateur,
types et nombres d'entités, longueurs, durées, identifiants de requête).

## 2. Portée de la pseudonymisation

L'audit est **pseudonymisé**, pas anonymisé : le `sub` permet de retrouver la personne par l'annuaire du fournisseur d'identité. Les
données restent donc des données personnelles au sens du RGPD.

Autres endroits où la correspondance existe (observé au laboratoire le 2026-10-12) :
- **journal du conteneur oauth2-proxy** : une ligne par connexion associe le `sub`, le courriel et le nom d'utilisateur
  (`[AuthSuccess] … Session{email:… user:<sub> PreferredUsername:…}`), ainsi que les réclamations d'un jeton refusé (EXT-67). Rotation :
  3 fichiers de 10 Mio (`docker-compose.yml`) ; la durée de conservation réelle dépend du volume de connexions ;
- **fournisseur d'identité** : l'annuaire lui-même, et ses journaux de connexion.

## 3. Procédure de ré-identification (proposition)

**Quand** : enquête sur un incident de sécurité, demande d'une autorité, exercice d'un droit par la personne concernée. Jamais pour un
suivi d'activité.

**Qui** :
- la demande est formulée et motivée par la DPO (ou le RSSI pour un incident, avec information de la DPO) ;
- l'exécution est faite par un administrateur du fournisseur d'identité, **distinct** de l'administrateur d'obfusk8 (séparation des
  rôles : celui qui lit l'audit ne détient pas l'annuaire).

**Comment** :
- *Keycloak (laboratoire)* : le `sub` est l'identifiant de l'utilisateur. Console d'administration, royaume `lab`, « Users », recherche
  par identifiant ; ou `kcadm.sh get users/<sub> -r lab --fields username,email`.
- *Entra ID (établissement)* : le `sub` est **propre à chaque application** (EXT-68) et ne se recherche pas directement dans l'annuaire.
  À trancher avant le pilote (une seule inscription d'application pour oauth2-proxy et l'API, ou autre mécanisme). Tant que ce n'est pas
  tranché, la ré-identification passe par le journal du conteneur oauth2-proxy (§2), dans sa fenêtre de conservation.

**Avec quelle trace** :
- la demande, son motif, la personne qui l'exécute, la date et le résultat sont consignés dans le **registre de la DPO** (hors
  d'obfusk8) ;
- l'action de l'administrateur est tracée par le fournisseur d'identité (au laboratoire, les événements d'administration de Keycloak ne
  sont **pas** activés : `eventsEnabled: false`, observé ; à activer dans un fournisseur réel) ;
- rien n'est ajouté à obfusk8 : l'audit n'est jamais modifié ni enrichi du nom retrouvé.

## 4. Points à valider par la DPO

1. La procédure du §3 (qui, comment, quelle trace).
2. La durée de conservation des journaux du conteneur oauth2-proxy, qui contiennent la correspondance `sub` ↔ courriel (§2).
3. Le traitement d'EXT-68 avant un pilote sous Entra ID.
4. La mention de l'audit pseudonymisé dans l'information des agents et dans l'AIPD.
