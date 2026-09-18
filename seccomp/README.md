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

## Mise à jour : support image (OCR via sous-processus `tesseract`)

Exactement le scénario anticipé ci-dessus : l'ajout du support image
(`pytesseract`, voir `requirements.txt`/`Dockerfile`) fait apparaître un
besoin réellement nouveau — c'est le tout premier chemin de code de cette
application à devoir lancer un **sous-processus** (`tesseract`, invoqué par
`pytesseract` via `subprocess.Popen`), jamais nécessaire pour
PyMuPDF/python-docx qui ne shell-out jamais.

Repassage par la méthode documentée ci-dessus (bascule sur
`app-audit.json`, scénario image rejoué contre `_handle_detect_image` réel,
`dmesg | grep 'audit: type=1326'`), puis confirmation en blocage réel
(`app-enforce.json`, conteneur autonome avec les mêmes réseaux que le
service "app") : **4 syscalls manquants**, tous directement liés à la
création/gestion d'un sous-processus (aucun n'était nécessaire avant) —

| Syscall        | Rôle |
|----------------|------|
| `vfork`        | Création du sous-processus `tesseract` (`_posixsubprocess.fork_exec`). |
| `dup2`         | Redirection des flux stdin/stdout/stderr du sous-processus vers les pipes. |
| `close_range`  | Fermeture des descripteurs de fichier hérités dans l'enfant après le fork (optimisation de `subprocess` sur CPython récent). |
| `wait4`        | Attente/récupération du sous-processus (`os.waitpid`, appelé par `pytesseract` puis par le nettoyage interne de `subprocess.Popen`). |

Point de méthode : `wait4` n'est PAS apparu dans le journal `dmesg` en mode
journalisation (`app-audit.json`) alors qu'il s'est bien révélé bloquant en
blocage réel juste après (`OSError: [Errno 38] Function not implemented`
sur `os.waitpid`) — feuille de route suivie à la lettre malgré cette
incohérence : ajouté après observation directe de l'échec en blocage réel,
puis reconfirmé par un cycle complet réussi (détection + OCR + Presidio +
caviardage + téléchargement, PNG et JPEG, via `/api/detect` et
`/api/finalize` réels) sous `app-enforce.json`. Cause de l'absence dans le
journal non élucidée (possible limite de capacité/débit du tampon `dmesg`
sur la session de test) — à garder à l'esprit : le mode journalisation seul
n'a pas suffi cette fois, la vérification en blocage réel reste
indispensable avant de considérer un profil "prêt".

`openat2` reste journalisé (comme lors de l'audit initial) mais toujours
volontairement **non ajouté** : comportement de repli déjà établi et
inoffensif (bascule silencieuse vers `openat`, déjà autorisé), confirmé de
nouveau ici sans régression.

## Mise à jour : outillage de test (`docker compose run app pytest` sous blocage réel)

Gap distinct de tout ce qui précède : **syscalls nécessaires à `pytest`
lui-même**, jamais appelés par le code applicatif — jusqu'ici invisibles
puisque la suite de tests n'avait jamais été rejouée à l'intérieur d'un
conteneur utilisant `app-enforce.json` (voir l'écart déjà noté à l'origine
entre "le profil a été validé contre l'application" et "jamais recontrôlé
contre `pytest`"). Hypothèse de départ (lecture de `_pytest/capture.py`,
`FDCaptureBase`) : `dup`/`dup2`/`dup3` suffiraient.

**Vérifiée fausse par l'observation, comme la méthode l'exige** :
`dup2` était déjà présent (ajouté pour le sous-processus `tesseract` ci-
dessus) et s'est révélé suffisant pour la capture de sortie de `pytest` —
`dup` et `dup3` ne sont jamais apparus dans le journal, sur aucune des
répétitions ci-dessous, et n'ont donc **pas** été ajoutés (aucun intérêt à
élargir la surface autorisée pour un besoin qui ne se présente pas
réellement).

Ce qui est réellement apparu, en répétant `docker compose run app pytest`
sous `app-audit.json` (`SCMP_ACT_LOG`) et en inspectant `dmesg` après
chaque exécution — **non-déterministe d'une exécution à l'autre** (chaque
`docker compose run` démarre un conteneur neuf, `/tmp` en tmpfs vide à
chaque fois ; la variation vient de chemins de code internes à `pytest`/
`tesseract` qui ne s'exécutent pas systématiquement, pas d'un état
résiduel) — a nécessité **6 exécutions successives** avant d'obtenir 3
répétitions consécutives sans aucun nouveau syscall :

| Syscall              | Origine (comm observé) | Rôle probable |
|----------------------|-------------------------|---------------|
| `setfsuid`, `setfsgid` | `pytest` | `os.access(path, ..., effective_ids=True)` — vérification de propriété du répertoire temporaire partagé avant d'y créer un dossier numéroté (`_pytest.tmpdir`, protection contre les attaques par lien symbolique sur un `/tmp` multi-utilisateur). |
| `ftruncate`, `sigaltstack` | `pytest` | `sigaltstack` : pile de signal alternative installée par `faulthandler.enable()` (`_pytest.faulthandler`) — c'est exactement l'appel dont l'absence avait été contournée par `-p no:faulthandler` avant cette investigation. `ftruncate` : écriture des fichiers de cache (`.pytest_cache/v/cache/lastfailed`, etc.). |
| `symlink`, `chmod`, `umask` | `pytest` | Suite du même mécanisme `_pytest.tmpdir` : création/mise à jour du lien symbolique `pytest-current` vers le dernier répertoire numéroté, avec permissions explicites. |
| `recvmsg`, `sched_getaffinity` | `tesseract` | Détection du nombre de cœurs disponibles par le runtime OpenMP de `tesseract` pour dimensionner son pool de threads (voir `tesseract --version`, "Found OpenMP") — apparaît uniquement quand un test exerçant l'OCR réel (`test_detect_image_sans_texte_renvoie_liste_vide`, etc.) s'exécute avant que l'affinité ne soit mise en cache par le runtime. |

Tous ajoutés à `app-audit.json` **et** `app-enforce.json`, classés comme
syscalls d'**outillage de test** (jamais appelés par `main.py`/`antivirus.py`
/`metrics.py`/`supervision.py` eux-mêmes) plutôt que comme besoins
applicatifs — pour qu'un futur lecteur ne suppose pas à tort qu'ils servent
au traitement PDF/DOCX/CSV/image.

**Revérification complète, dans l'ordre imposé** :
1. Sous `app-audit.json` mis à jour : 3 exécutions consécutives de
   `docker compose run app pytest tests/` sans aucun nouveau syscall
   journalisé (seul `openat2`, déjà connu et accepté, reste visible sur le
   bootstrap `runc`).
2. Bascule de `docker-compose.yml` sur `app-enforce.json` mis à jour,
   `docker compose up -d --force-recreate app`.
3. Cycle de non-régression sur le service vivant : démarrage propre,
   requête HTTP réelle de bout en bout sur le format image (`/api/detect` →
   `/api/finalize` → `/api/download`, OCR + Presidio réels, 200 partout),
   arrêt propre (`docker stop`, `SIGTERM` → "Arrêt de l'application" →
   `Application shutdown complete`).
4. **Test décisif** : `docker compose run app pytest tests/ -v` dans le
   conteneur en blocage réel — **97/97 tests passés**, répété **5 fois de
   suite** sans un seul échec (la non-déterminisme constatée en mode
   journalisation aurait pu se reproduire ici ; elle ne s'est pas
   manifestée, cohérent avec le fait que tous les syscalls concernés sont
   désormais explicitement autorisés).

`docker-compose.yml` redéployé avec `app-enforce.json` mis à jour —
confirmé lui-même inchangé par rapport à avant cette investigation (toujours
`seccomp=./seccomp/app-enforce.json`), seul le contenu du profil a changé.

## Revue de sécurité : conséquences de l'ajout d'un sous-processus (demande explicite de l'utilisateur)

L'ajout du support image est le premier code de cette application à
réellement lancer un sous-processus (`tesseract`, via `pytesseract`). Revue
ciblée sur ce point précis, au-delà du simple ajout des syscalls
nécessaires — code de `pytesseract` (`/usr/local/lib/python3.12/site-
packages/pytesseract/pytesseract.py`) lu intégralement, pas seulement sa
documentation.

**Points vérifiés sans problème trouvé** :
- **Pas d'injection de commande possible** : `subprocess.Popen(cmd_args, ...)` reçoit une **liste** d'arguments, jamais `shell=True`, et rien dans `cmd_args` n'est dérivé du contenu de l'image ou d'une entrée utilisateur — `lang` est la constante fixe `"fra"` (`OCR_LANGUAGE`), aucun paramètre `config` n'est jamais passé par notre code (reste vide côté appelant, donc toujours le même argument fixe `-c tessedit_create_tsv=1` côté pytesseract).
- **L'image elle-même ne parvient jamais à `tesseract` sous sa forme brute uploadée** : pytesseract réencode l'objet `PIL.Image` via `image.save(...)` dans un nouveau fichier temporaire avant de l'exécuter — un fichier PNG/JPEG malveillant conçu pour exploiter un bug du décodeur *de tesseract* ne survit pas au premier passage par le décodeur *Pillow* (déjà utilisé pour notre propre validation) : c'est une image fraîchement ré-encodée par Pillow, structurellement valide, que `tesseract` reçoit. Réduction d'attaque non intentionnelle mais réelle.
- **Fichiers temporaires** : noms aléatoires sécurisés (`tempfile.NamedTemporaryFile`), pas de fenêtre de type TOCTOU/symlink exploitable ; nettoyage dans un bloc `finally` même en cas d'exception.
- **`PATH` non détournable** : tous les répertoires de `$PATH` (`/usr/local/bin`, `/usr/bin`, etc.) sont sur le système de fichiers racine en lecture seule (`read_only: true`) — confirmé par test direct (`touch` échoue avec `Read-only file system`). `/tmp` (seul répertoire inscriptible avec `/data/tmp`/`/data/audit`) est monté `noexec,nosuid` par défaut par Docker Compose (confirmé via `mount`), donc inexploitable même pour y déposer un binaire.
- **Corrigé quand même par précaution** (défense en profondeur, coût nul) : `pytesseract.pytesseract.tesseract_cmd` épinglé au chemin absolu `/usr/bin/tesseract` plutôt que la recherche `$PATH` par défaut de la bibliothèque.

**🔴 Problème réel trouvé et corrigé** : **aucun timeout n'était appliqué** à l'appel `pytesseract.image_to_data()` (`timeout=0` par défaut = `proc.communicate()` sans aucune limite). Une image pathologique/adversariale (ou un bug du moteur tesseract lui-même) pouvait faire tourner le sous-processus indéfiniment, sans jamais être rattrapé par `_check_detection_deadline` (qui ne s'exécute qu'**après** le retour de l'OCR) — même défaut de conception que celui déjà corrigé pour Presidio (`timeout=30` explicite dans `_analyze_text`), non répliqué symétriquement lors de l'ajout initial de l'OCR. **Corrigé** : `MAX_OCR_SECONDS` (60s par défaut, configurable) passé explicitement à `pytesseract.image_to_data()`, avec gestion propre du `RuntimeError('Tesseract process timeout')` levé par pytesseract (converti en 400, jamais une trace brute).

**Découverte en corrigeant ce point** : le correctif lui-même a révélé un **second gap seccomp**, cette fois bien applicatif (pas de l'outillage de test) — `kill()`, appelée par pytesseract pour terminer proprement le sous-processus après expiration du timeout (`process.terminate()` → `os.kill(pid, SIGTERM)`), utilise le syscall `kill` (62), absent de la liste (`tgkill`, déjà présent, cible un *thread* précis dans le processus courant, pas un PID arbitraire — insuffisant ici). Confirmé par un test réel (pas un mock : vrai sous-processus `tesseract` lancé avec un timeout de 1ms, donc garanti de dépasser) qui échouait sous blocage réel avec `OSError: [Errno 38] Function not implemented` sur `os.kill`, resterait invisible en usage normal (le timeout de 60s n'est presque jamais atteint par une image légitime) mais aurait laissé un sous-processus non tuable — donc un **vrai processus zombie/orphelin qui continue de consommer CPU** — en cas de timeout réel en production, jusqu'à épuisement de la limite mémoire/CPU du conteneur. Ajouté aux deux profils.

**Revue du compromis seccomp lui-même (l'angle explicitement demandé)** : avant cet ajout, un attaquant obtenant une exécution de code arbitraire dans le processus `app` (par un futur bug quelconque, sans rapport avec l'OCR) ne pouvait **créer aucun nouveau processus** — `vfork`/`fork`/`clone` avec les flags nécessaires étaient bloqués ; seul `execve` (déjà présent) permettait un remplacement du processus courant, une primitive nettement moins utile (perte du serveur en cours d'exécution, pas de canal de sortie). **Depuis cet ajout, cette primitive existe** : `vfork` + `dup2` + `close_range` + `wait4` + `execve` donnent la capacité standard de lancer n'importe quel exécutable avec entrées/sorties redirigées, tout en gardant le serveur `app` vivant. C'est une augmentation réelle et assumée de la surface disponible *après* une éventuelle compromission — mitigée par plusieurs contrôles déjà en place, indépendamment de ce changement :
- `cap_drop: ALL` + `no-new-privileges:true` : aucune capability, aucune élévation via un binaire setuid.
- Système de fichiers racine en lecture seule : impossible de déposer un nouveau binaire exécutable n'importe où sur le `PATH` ou ailleurs sur la partie en lecture seule.
- `/tmp` en `noexec` (voir plus haut) : même avec une capacité d'écriture arbitraire dans `/tmp`, rien n'y est exécutable.
- Réseaux `app-internal`/`backend` tous deux `internal: true`, sans route sortante vers Internet — un reverse shell lancé depuis le conteneur ne peut pas téléphoner vers l'extérieur ; seul un pivot vers `presidio-analyzer`/`presidio-anonymizer` (déjà accessibles en HTTP depuis `app` de toute façon) resterait possible.

**Angle non couvert, laissé en observation (pas un défaut introduit par ce changement)** : `/data/tmp`/`/data/audit` (bind mounts `ext4`, distincts du `tmpfs` `/tmp`) ne sont **pas** montés `noexec` — confirmé par `mount`. Ce n'est pas un vecteur direct via le flux d'upload actuel (l'appli n'y écrit que des documents ré-encodés par Pillow/PyMuPDF/python-docx, jamais des octets bruts arbitraires nommés par l'attaquant), et ce n'est pas spécifique à l'ajout du sous-processus (ces montages étaient déjà ainsi avant). Mais la capacité de sous-processus rend ce genre de détail plus pertinent qu'avant : si un attaquant obtenait un jour une primitive d'écriture de fichier arbitraire dans `/data/tmp`, il pourrait maintenant l'exécuter. Ajouter `noexec`/`nosuid` à ces deux montages serait une amélioration défensive raisonnable, mais modifier des montages bind existants en production est un changement d'infrastructure distinct, non trivial à faire sans risquer de casser l'écriture des fichiers de sortie — **laissé à la décision de l'utilisateur**, pas fait ici.
