# Profil seccomp personnalisé — service "app"

Ce dossier contient un profil seccomp construit spécifiquement pour le
conteneur `app` (détection/anonymisation PDF/DOCX/CSV), en remplacement du
profil par défaut de Docker (`docker-default`, ~370 syscalls autorisés).
Objectif : réduire la surface d'attaque exploitable après compromission
(ex. une variante non corrigée de CVE-2026-3308 dans le traitement d'images
PDF), sans toucher aux autres services du stack.

## Méthode suivie

1. **Trace réelle des syscalls applicatifs.** Deux sessions `strace -f`
   complètes sur le service "app" réel (le même artefact d'image que celui
   construit par `docker-compose build app`) :
   - attache en direct sur le conteneur `app` déjà démarré par
     docker-compose (capture le comportement stable + un arrêt propre par
     `docker stop`, SIGTERM géré par uvicorn) ;
   - relance complète depuis `execve` (via un variant de l'image avec
     `strace` en ENTRYPOINT), pour capturer aussi les tout premiers
     syscalls du démarrage (bind/listen du serveur, imports, threads).

   Scénarios rejoués contre l'API réelle (`/api/detect` puis
   `/api/finalize` puis `/api/download`) pendant la trace :
   - PDF nominal, avec et sans thème (`test_med.pdf`, PDF médical riche en
     PII, déjà utilisé par les scripts de vérification du projet) ;
   - PDF avec zone de caviardage manuelle + `/api/preview_image` ;
   - DOCX nominal (`tests/fixtures/docx_fixture.docx`) ;
   - DOCX avec charge XXE (`tests/fixtures/xxe_fixture.docx`) ;
   - DOCX zip-bomb (générée à la volée, même principe que
     `build_zipbomb_entries.py`) — rejetée avant tout traitement lourd ;
   - CSV nominal (`tests/fixtures/csv_fixture.csv`) et CSV avec injection
     de formule ;
   - cas d'erreur : PDF corrompu/tronqué, type de fichier inconnu, moteur
     Presidio injoignable (502) ;
   - balayage périodique des fichiers orphelins et purge différée
     (`FILE_TTL_SECONDS`) ;
   - suite de tests unitaires du projet (`pytest tests/`, 20 tests) ;
   - arrêt propre du service (SIGTERM → `Arrêt de l'application` →
     `exit_group(0)`).

2. **Base = profil par défaut de Docker.** Le profil par défaut n'est plus
   distribué comme fichier JSON statique dans ce Docker/containerd
   (`docker info` indique `seccomp,profile=builtin` : il est généré au vol
   par `containerd/contrib/seccomp/seccomp_default.go`, v2.3.5, correspondant
   à la version installée ici). Ce fichier source a servi de référence pour :
   - la liste complète des ~370 syscalls autorisés par défaut (pour ne
     retenir que ceux effectivement observés) ;
   - deux règles de durcissement reprises à l'identique (pas un
     assouplissement, une restriction supplémentaire gratuite) :
     - `socket` : autorisé pour toutes les familles d'adresses **sauf**
       `AF_VSOCK`/`AF_ALG` (canaux hyperviseur / API crypto noyau, jamais
       utilisés par cette appli) ;
     - `clone` : autorisé uniquement **sans** les flags de création de
       namespace (`CLONE_NEWNS`/`NEWUTS`/`NEWIPC`/`NEWUSER`/`NEWPID`/
       `NEWNET`/`NEWCGROUP`) — cohérent avec `cap_drop: ALL` sur ce service
       dans `docker-compose.yml`, qui ne lui donnera jamais `CAP_SYS_ADMIN` ;
     - `clone3` : renvoie `ENOSYS` (pas un blocage silencieux différent) —
       glibc bascule alors immédiatement sur `clone()`, exactement le
       comportement du profil par défaut de Docker aujourd'hui pour un
       conteneur sans `CAP_SYS_ADMIN` (voir moby/moby#42681). ll ne s'agit
       pas d'une syscall en plus mais du même comportement déjà en vigueur.

3. **Syscalls de bootstrap runc.** Une trace app-level seule ne voit pas les
   syscalls que `runc` exécute *avant* l'`execve` final vers le process
   applicatif (drop de privilèges vers `appuser` uid/gid 1000, application
   des capabilities, etc.) : ils tournent dans le même process mais avant
   que la commande du conteneur ne soit lancée. Découverts en rejouant, sous
   `strace`, le bundle OCI exact généré par Docker pour ce service
   (`/run/containerd/.../config.json`, capturé pendant que le conteneur réel
   tournait) directement via `runc run`, avec le profil restreint en
   construction et **en blocage réel (`SCMP_ACT_ERRNO`)** — chaque syscall
   manquant provoquait un échec de démarrage explicite et reproductible
   (`unable to apply bounding set`, `unable to setup user: setgroups`,
   `chdir to cwd failed`, etc.), ajouté un par un jusqu'à cycle complet
   démarrage → service → arrêt propre sans aucune erreur. Ce sont des
   besoins du runtime conteneur, pas du code de `main.py` — nécessaires à
   *tout* conteneur non-root avec `cap_drop: ALL`, indépendamment de ce
   qu'il exécute.

4. **Marge de sécurité documentée.** Un seul ajout non observé pendant la
   session de trace mais lié à un chemin de code réel et identifié en
   lisant `main.py` : `rename`/`renameat`/`renameat2`, utilisés par
   `RotatingFileHandler` pour la rotation du journal d'audit
   (`_record_audit_event`, déclenchée seulement au bout de 10 Mo de
   journal cumulés — jamais atteint pendant la session de test).

## Fichiers

- `app-audit.json` — **profil actuellement déployé** sur le service "app"
  (`docker-compose.yml`). Action par défaut `SCMP_ACT_LOG` : ne bloque
  strictement rien, journalise seulement les syscalls hors de la liste
  autorisée (visibles via `dmesg` / `journalctl -k`, entrées
  `audit: type=1326 ... subj=docker-default ... syscall=<numéro>`).
- `app-enforce.json` — même liste de syscalls, action par défaut
  `SCMP_ACT_ERRNO` (blocage réel, retourne `ENOSYS`). **Pas encore appliqué
  au service "app"** — voir statut ci-dessous.

## Résultat de la vérification en mode journalisation (étape 4)

Après bascule de `docker-compose.yml` sur `app-audit.json` et relance de
**tous** les scénarios ci-dessus contre le service réel (conteneur
`anonymiseur-app-1`, PID hôte vérifié), plus un redémarrage complet et un
arrêt propre : **trois syscalls journalisés, zéro imprévu** —

| Syscall         | Occurrences | Analyse |
|-----------------|------------:|---------|
| `io_uring_setup`  | 2  | Tentative optionnelle d'uvloop/libuv d'utiliser io_uring pour son pool de threads fs. Échoue proprement (`ENOSYS`), libuv bascule sur epoll — comportement déjà celui du profil par défaut de Docker aujourd'hui (`io_uring_*` n'y figure pas non plus), donc **aucune régression**. |
| `io_uring_enter`  | 23 | Conséquence du même mécanisme ci-dessus. |
| `openat2`         | 6  | Tentative ponctuelle de la libc d'ouvrir un chemin via la variante moderne d'`openat`; retombe sur `openat` (déjà autorisé) sans erreur visible côté application. |

Confirmé indépendamment par un test direct en **blocage réel**
(`SCMP_ACT_ERRNO`, pas seulement journalisation) : le bundle OCI exact du
service "app", avec `app-enforce.json`, complète un cycle démarrage →
"Application startup complete" → arrêt propre (`SIGTERM` →
"Application shutdown complete" → `exit_group(0)`) sans aucune erreur.
Les trois syscalls du tableau ci-dessus y apparaissent bien en `ENOSYS`,
sans conséquence observable (comportement de repli déjà démontré).

## Statut : prêt pour un passage en blocage réel, à activer manuellement

**Aucun syscall légitime manquant n'a été détecté.** Sur la base de ce qui
précède, le profil semble prêt à passer en blocage réel. Conformément à la
demande, ce passage n'a **pas** été effectué automatiquement — c'est une
action manuelle explicite :

```yaml
# docker-compose.yml, service "app", à modifier soi-même :
      - seccomp=./seccomp/app-enforce.json   # au lieu de app-audit.json
```

puis `docker compose up -d app` et revérifier le comportement en usage réel
(y compris, dans l'idéal, un cycle complet de rotation du journal d'audit à
10 Mo, non atteint pendant cette session — c'est l'unique chemin de code
couvert par marge plutôt que par observation directe).

Si un besoin légitime apparaît après la bascule (nouveau format de
document, nouvelle dépendance, mise à jour de PyMuPDF/python-docx...),
revenir à `app-audit.json` le temps de rejouer les scénarios et identifier
le syscall manquant via `dmesg | grep 'audit: type=1326'`, puis l'ajouter à
la liste dans les deux fichiers.
