# Plan d'audit de sécurité — Projet Anonymiseur de documents

**Dernière mise à jour :** session 8 — constat critique **[VÉRIFIÉ]** sur le PDF, symétrique à la section 9.1 DOCX de la session 7 : le fichier de sortie "anonymisé" contenait toujours, en clair et récupérable par n'importe quel outil qui parcourt tous les objets du PDF, le nom, la date de naissance et le numéro de dossier du patient d'origine — alors que la page rendue affichait bien le caviardage. Corrigé (purge des objets orphelins + métadonnées) et revérifié par un test de bout en bout via les vrais points d'entrée (`_handle_detect_pdf` / `_finalize_pdf_job`), conformément à la leçon de la session 7. Voir section 10.

**Session 7** — extension du traitement aux formats DOCX et CSV (jusque-là PDF uniquement), avec un lot de constats sévères : 6 fuites de données structurelles confirmées par test dans le format DOCX (indépendantes de tout réglage de détection), plus un patch du moteur Presidio lui-même pour traiter un biais de détection resté hors de portée du seul réglage applicatif.

**Note de traçabilité** : une session intermédiaire (entre la session 6 ci-dessous et celle-ci) a corrigé des faux positifs de détection sur du vocabulaire médical dans `medical.json` (allow_list étendue, exclusion du type `ORGANIZATION`, motif de numéro de dossier) — visible dans l'état actuel de ce fichier, mais cette session n'a jamais été documentée dans ce plan d'audit à l'époque. Signalé ici pour ne pas laisser croire que l'historique ci-dessous est complet à 100 % ; son contenu exact (raisonnement, alternatives écartées) n'a pas pu être reconstitué avec confiance pour cette mise à jour.

**Périmètre :** stack applicative (Traefik, app FastAPI, Presidio analyzer/anonymizer), hébergement (VM Proxmox/Docker), traitement de fichiers uploadés — **désormais PDF, DOCX et CSV** (jusqu'à la session 7, seul le PDF était couvert).

**Hors périmètre (exclu volontairement) :** l'authentification/identité (Keycloak en lab, remplacé par Entra ID en cible) — à l'exception du point 1.28 (durée de session) et du point 1.29 (isolation réseau), qui survivent tous deux à la bascule Entra sans être remis à plat automatiquement.

**Légende des statuts :** ✅ Résolu · 🟡 Risque accepté / reporté / à décider (documenté) · ⏳ Toujours ouvert

---

## 0. Méthodologie

1. **Revue statique / configuration**
2. **Tests dynamiques ciblés** (lab uniquement, jamais de vraies données patient)
3. **Recommandation d'un test d'intrusion externe (pentest)** avant toute mise en situation réelle

**Rappel de gouvernance (session 5, toujours valable) :** vérifier que les documents de test utilisés restent bien fictifs/anonymisés, pas de vraies données patient.

**Principe méthodologique renforcé cette session** : chaque constat de la section 9 marqué **[VÉRIFIÉ]** a été reproduit par un test réel exécuté pendant la session (fichier construit, pipeline exécuté, résultat inspecté), pas seulement déduit par lecture de code. Un bug a d'ailleurs été détecté *pendant la vérification elle-même* (voir 9.1) parce que le premier passage de test appelait des fonctions internes plutôt que les vrais points d'entrée — la leçon a été appliquée immédiatement en refaisant les tests via les fonctions publiques réelles. Réaffirme la valeur de tester plutôt que de supposer, déjà actée aux sessions précédentes (voir 8, leçon session 6).

---

## 1. Risques techniques facilement identifiables

*(Table complète des points 1.1 à 1.29 inchangée depuis la session 6 — voir versions précédentes du document. Aucun nouveau point numéroté cette session ; les constats DOCX/CSV, plus nombreux et de nature différente, sont regroupés en section 9 plutôt qu'ajoutés ici en vrac.)*

---

## 2 à 5. Sections inchangées

Voir versions précédentes du document pour le détail complet (échappement de conteneur, injection, gestion d'erreurs, dépendances).

---

## 6. Isolation de l'accès internet sortant (session 6, inchangé)

Voir version précédente du document pour le détail complet (réseau `app-internal`, limitation Docker sur `internal: true` et publication de ports, résiduel assumé sur `traefik`).

---

## 7. Tests différés au pentest final (inchangé)

Vérification `Range`/`FileResponse`, abus de surface HTTP (Burp), retest 3.7 une fois `/api/audit` corrigé.

---

## 9. Extension DOCX/CSV et durcissement Presidio (nouveau, session 7)

### 9.1 Fuites de données structurelles DOCX — 🔴 CRITIQUE, ✅ toutes corrigées et testées

**Le constat de fond** : la fonction qui parcourait un `.docx` pour y chercher des données sensibles (`_iter_docx_paragraphs`) ne lisait que `paragraph.runs`, qui ne couvre que le texte "visible" en lecture normale. Plusieurs zones d'un `.docx` stockent du texte *ailleurs* dans la structure XML, invisibles à cette API — donc jamais analysées, jamais caviardées, livrées telles quelles dans le fichier "anonymisé" final. **Ce n'est pas un problème de qualité de détection (comme la section "qualité PII" de la session 5) — ces fuites contournent la détection entièrement, quel que soit le réglage du NER ou du seuil.**

Six zones concernées, chacune **[VÉRIFIÉE]** par construction d'un document de test réel et exécution du pipeline complet :

| Zone | Constat | Test |
|---|---|---|
| Suivi des modifications (texte supprimé) | Un texte supprimé en mode suivi (`<w:del>`) reste présent dans le XML — c'est le mécanisme qui permet d'annuler la suppression | Nom + date supprimés en mode suivi : **0 détection**, survivaient intégralement dans le fichier "anonymisé" final |
| Métadonnées du document | Auteur, dernier modificateur, commentaire/sujet (`docProps/core.xml`) jamais ouverts par le pipeline | Auteur, dernier modificateur et un commentaire contenant un numéro de dossier retrouvés intacts dans le fichier final |
| Texte affiché des hyperliens | `paragraph.runs` ne descend pas dans un `<w:hyperlink>` | Un lien "Contacter [Nom]" : le nom n'était jamais vu par la détection |
| Cible des hyperliens (URL) | Stockée dans `word/_rels/document.xml.rels`, partie séparée du corps | Une adresse email en cible de lien mailto retrouvée intacte même après suppression du lien visible |
| Commentaires Word | Partie séparée (`word/comments.xml`), référencée depuis le corps sans jamais être lue | Un commentaire de relecture contenant un nom : jamais lu, jamais retiré |
| Notes de bas de page / de fin | Parties séparées (`word/footnotes.xml`/`endnotes.xml`), aucune API haut niveau python-docx pour les éditer | Une note contenant un nom et une date : jamais analysée |

**Correctifs, tous testés** :
- **Suivi des modifications** → aplati avant toute analyse (suppressions retirées entièrement, insertions dépaquetées) sur le corps, chaque en-tête/pied de page, et chaque note.
- **Métadonnées** → vidées à la finalisation (auteur, dernier modificateur, commentaire, sujet, mots-clés, catégorie) — champs structurés dont la nature est connue par convention, inutile de les faire passer par le NER.
- **Hyperliens** → texte affiché "déplié" (redevient un run normal, analysé comme le reste du corps) ; cible et relation associée supprimées, pas seulement débranchées du corps visible.
- **Commentaires** → retirés entièrement (marqueurs + parties associées), pas caviardés : même un commentaire partiellement masqué révélerait qu'une discussion interne visait une personne précise.
- **Notes de bas de page / de fin** → contrairement aux zones ci-dessus, ce sont analysées et caviardées comme le corps (contenu réellement destiné au lecteur, pas un artefact de travail interne) — nécessite une lecture/réécriture manuelle de leur partie XML, aucune API python-docx dédiée.

**Test de validation combiné** : document construit avec les 6 zones simultanément, passé dans le pipeline complet réel (pas seulement les fonctions internes) — les 6 fuites ne se reproduisent plus, texte normal du corps toujours présent, fichier reste ouvrable sans erreur. Toutes les régressions DOCX de la session ont été revérifiées après ce refactor — aucune casse.

**Bug trouvé pendant la vérification elle-même** : le premier passage de correction avait oublié un point d'appel (encore l'ancienne API dans le point d'entrée réel de détection) — les tests initiaux ne l'avaient pas détecté car ils appelaient les fonctions internes directement. Corrigé, puis revérifié en appelant cette fois les vraies fonctions d'entrée de bout en bout.

### 9.2 Score de confiance Presidio inefficace pour filtrer le bruit administratif — ✅ traité par un patch du moteur

**Le constat** : sur des documents administratifs (formulaires, listes de personnel), des mots courants du français ("Compétences", "Exploiter", "Cliquez"...) étaient détectés comme `PERSON`/`ORGANIZATION` au même titre que de vrais noms. Piste naturelle testée en premier : remonter le seuil de confiance minimal. **Écartée après investigation** — le recognizer spaCy de Presidio attribue un score **fixe** (0.85 par défaut, `ner_strength`) à toute détection, indépendamment de sa justesse réelle ; un seuil ne peut donc jamais distinguer un bon d'un mauvais résultat de ce recognizer précis. Confirmé par le code source du recognizer lui-même (`spacy_recognizer.py`, obtenu depuis le conteneur), pas seulement supposé.

**Risque de régression identifié et évité** : remonter quand même le seuil au-dessus de 0.85 pour forcer le filtrage a été testé — et a effectivement supprimé le bruit, mais a **aussi supprimé la détection de vrais noms** dans un PDF médical réel (même mécanisme de score fixe), en mode "aucun thème" sélectionné. Revert immédiat dès confirmation du problème sur données réelles, avant tout déploiement plus large. Rappel du principe qui a guidé la décision : un faux positif se corrige en un clic en révision, un faux négatif part inaperçu — ne jamais arbitrer en faveur de moins de faux positifs si ça coûte des faux négatifs.

**Solution retenue** : patch du recognizer spaCy de Presidio lui-même (`spacy_recognizer.py`, surchargé au build via le `Dockerfile` de `presidio-analyzer`) — exige qu'au moins un token de l'empan détecté soit étiqueté nom propre (`PROPN`) par le tagger spaCy, déjà actif dans le pipeline chargé (zéro coût d'inférence supplémentaire, confirmé par inspection des composants actifs du modèle `fr_core_news_md`). Testé avec des objets spaCy simulés reproduisant les cas réels observés : mots administratifs (VERB/NOUN) rejetés, vrais noms (PROPN) conservés.

**Limite assumée, découverte en aval** : ce filtre par POS a laissé passer un format de nom compact ("Initiale(s). Nom", ex. `L. Verdurme`, `J-M. Costa`) présent dans un annuaire professionnel réel — le tagger spaCy hésite à étiqueter un nom de famille rare comme nom propre quand il suit directement une initiale isolée, sans les indices habituels. **Confirmé ne pas être une régression du patch** (testé avec et sans le patch actif, même résultat) — limite préexistante du NER sur ce format précis, indépendante du filtre POS.

### 9.3 Nouveaux reconnaisseurs regex ajoutés

- **`FrenchInitialSurnameRecognizer`** (`common.json`, actif quel que soit le thème) — motif "Initiale(s). Nom" pour couvrir la limite du 9.2. **Compromis assumé et documenté** : le même motif de surface correspond aussi à un titre de section numéroté par lettre ("A. Introduction", "B. Méthode") — accepté délibérément, cohérent avec le principe de la section 9.2 (mieux vaut trop caviarder que rater un vrai nom), à surveiller si ce type de faux positif s'avère fréquent en usage réel.
- **Numéro de dossier patient** (`patch_recognizers.py`, intégré au build de `presidio-analyzer`, donc actif indépendamment du thème sélectionné) — étendu avec le motif "chiffres + lettre + chiffres" (ex. `22D0755084`, jusque-là seulement dans `medical.json` donc actif uniquement avec ce thème) et le libellé abrégé "n°" (ex. "Dossier n° : ..."), qui couvre aussi les numéros purement numériques. **Redondance résiduelle non nettoyée** : `medical.json` contient encore des motifs similaires, désormais dupliqués avec ceux intégrés en dur — sans risque fonctionnel (fusion automatique des détections qui se chevauchent en révision), mais source de dérive si l'un est modifié sans l'autre.

### 9.4 Bugs de détection structurelle par tableau — trouvés et corrigés en cours de session

Trois bugs réels dans la nouvelle détection structurelle (repérage de colonnes/lignes de tableau par mot-clé d'étiquette, indépendante du NER) ont été trouvés au fil des tests sur documents réels, pas anticipés à la conception :

1. **Faux positif par sous-chaîne** : le test de correspondance de mot-clé utilisait `"nom" in texte` plutôt qu'une limite de mot — un long paragraphe de description contenant "dénomination" ou "installation" (contenant la sous-chaîne "nom") faisait caviarder à tort toute une ligne de tableau. Corrigé par une correspondance sur mot entier + une longueur maximale de candidat-étiquette (un vrai libellé de champ est court, jamais un paragraphe).
2. **Absorption d'une deuxième étiquette dans la valeur de la première** : une ligne de tableau avec plusieurs paires étiquette:valeur côte à côte ("Nom: X Date: Y") faisait absorber la 2ᵉ étiquette et sa valeur dans la détection de la 1ʳᵉ. Corrigé pour gérer plusieurs étiquettes par ligne, avec changement de type dès qu'une nouvelle étiquette est rencontrée.
3. **Tableau à une seule ligne mal classé** : une ligne unique où 2 colonnes correspondent par coïncidence à des mots-clés se faisait classer à tort comme "tableau à en-tête" (qui suppose des lignes de données en dessous) — 0 détection utile produite. Corrigé en exigeant au moins une ligne de données sous l'en-tête.

Chacun trouvé via un journal de diagnostic temporaire (voir 9.5) plutôt que par relecture de code — sans lui, ces trois bugs seraient probablement restés invisibles, aucun des scénarios de test construits à l'avance ne les ayant révélés.

### 9.5 Journal de diagnostic temporaire (`ENABLE_DEBUG_LOG`) — deux failles trouvées et corrigées

Fonctionnalité ajoutée en cours de session pour accélérer le diagnostic des faux positifs/négatifs sur données réelles (une ligne JSON par détection : type, score, source, texte concerné) — explicitement documentée comme lab-only, à retirer avant toute mise en production. Deux failles identifiées et corrigées avant la fin de la session, pas laissées comme risque accepté :

- **Pas de cloisonnement entre utilisateurs** — `/api/debug/log` n'était protégé que par l'authentification standard : n'importe quel utilisateur authentifié pouvait télécharger les détections (texte en clair) de n'importe quel autre utilisateur. **Corrigé** : chaque entrée est taguée avec l'utilisateur, l'endpoint filtre par l'identité de la requête avant de renvoyer quoi que ce soit. Testé avec plusieurs utilisateurs simulés.
- **Pas d'expiration temporelle** — seulement une rotation par taille, contrairement aux fichiers de sortie (purgés par TTL). **Corrigé** : purge périodique (`DEBUG_LOG_TTL_SECONDS`, 1h par défaut) intégrée à la boucle de nettoyage existante, coordonnée avec le verrou du handler de logging pour éviter toute corruption pendant une écriture concurrente — vérifié explicitement que le journal reste écrivable après une purge.

**Il reste qu'une fonctionnalité de debug loggant du texte en clair est une exception délibérée au principe qui gouverne le reste de l'application** (le journal d'audit, lui, ne stocke que des hashs) — désactivée par défaut, mais à retirer du code avant toute mise en production, pas seulement désactivée par variable d'environnement.

### 9.6 Nouveaux points ouverts, non traités cette session

| # | Statut | Risque | Constat |
|---|---|--------|---------|
| 9.6.1 | ⏳ | **Zip-bomb DOCX : pas de limite sur le nombre d'entrées** | La validation borne la taille décompressée totale et le ratio de compression par fichier, mais jamais le nombre d'entrées dans l'archive — un zip avec un nombre extrême de fichiers minuscules pourrait rester sous les deux seuils tout en coûtant cher rien qu'à parcourir la table des fichiers. |
| 9.6.2 | ⏳ | **Risque XXE non vérifié** | Pas de confirmation que la configuration lxml utilisée par python-docx désactive bien la résolution d'entités externes par défaut pour la version épinglée (1.2.0/6.1.1). Généralement le cas par défaut sur les versions récentes, mais non confirmé spécifiquement. |
| 9.6.3 | ⏳ | **Validation CSV intrinsèquement faible** | Pas de signature binaire possible pour un CSV (contrairement à `%PDF-` ou la structure ZIP d'un docx) — connu et accepté dès la conception, re-signalé pour mémoire. |
| 9.6.4 | ⏳ | **Pas de budget de temps global pour la détection sur un gros document** | Chaque appel à Presidio a un timeout de 30s, mais rien ne borne le temps total d'un document proche des limites de taille (`MAX_DOCX_PARAGRAPHS`/`MAX_CSV_CELLS`), qui peut nécessiter des dizaines d'appels séquentiels. |
| 9.6.5 | ⏳ | **Page de révision CSV sans pagination** | Un CSV proche de la limite de cellules autorisée génère une page HTML avec potentiellement des centaines de milliers de `<td>` — coûteux en mémoire serveur, bande passante, et rendu navigateur. Angle "amplification de ressources" non anticipé au moment de fixer le seuil de taille. |
| 9.6.6 | ⏳ | **Nouveaux regex non testés contre le ReDoS** | `FrenchInitialSurnameRecognizer`, les motifs de numéro de dossier, et la correspondance de mot-clé de colonne n'ont pas fait l'objet d'un test de performance dédié contre des entrées adverses. Lecture rapide ne révélant pas de quantificateurs imbriqués ambigus, mais pas une preuve formelle. |
| 9.6.7 | ⏳ | **Seuils numériques DOCX/CSV non stress-testés** | Taille décompressée max, ratio de compression, nombre de cellules CSV : valeurs raisonnables choisies par jugement, jamais éprouvées contre une charge adverse réellement construite. |
| 9.6.8 | ⏳ | **`app/Dockerfile` jamais examiné cette session** | Les hypothèses sur l'environnement d'exécution du service `app` (utilisateur non-root, nombre de workers Uvicorn) reposent sur des déductions indirectes (l'erreur de permission rencontrée sur `/data/debug`), jamais sur une lecture directe du fichier. |
| 9.6.9 | 🟡 | **`themes/formation.json` potentiellement orphelin** | Créé en cours de session comme piste de correction, explicitement écarté ensuite au profit d'autres approches — à confirmer s'il a été supprimé côté VM ou s'il traîne, inutilisé mais inoffensif. |
| 9.6.10 | — | **MD5 utilisé pour le regroupement visuel côté révision** | Non un enjeu de sécurité : sert uniquement à générer un identifiant HTML stable pour le regroupement visuel côté client, aucun usage cryptographique. Mentionné uniquement pour éviter qu'un futur audit ne le signale à tort. |
| 9.6.11 | ⏳ | **Fraîcheur des dépendances DOCX à reconfirmer** | `python-docx==1.2.0` et `lxml==6.1.1` étaient les dernières versions stables au moment de l'épinglage — à revérifier avant mise en production si du temps s'est écoulé, cohérent avec le principe déjà appliqué aux autres dépendances (voir section 5). |

---

## 10. Fuite structurelle PDF — métadonnées et objets orphelins (nouveau, session 8)

### 10.1 Contexte et déclencheur

Test repris sur `caviar_test.pdf`, un fichier "anonymisé" produit par une session précédente (même patient fictif que `test_med.pdf`) et laissé de côté sans avoir été audité pour lui-même. Demande explicite de vérifier la présence de métadonnées et d'éventuels "trous" comme ceux trouvés sur DOCX en 9.1, et de corriger sur le même principe.

### 10.2 Constat — 🔴 CRITIQUE, ✅ corrigé et testé

**Métadonnées** : `_finalize_pdf_job` sauvegardait le PDF final via `doc.save(output_path)` sans jamais toucher au dictionnaire `/Info` ni au paquet XMP. Sur `caviar_test.pdf`, `producer` ("cairo 1.18.0") et `creationDate` (date réelle de génération du document source) survivaient intégralement — exactement le même angle que les propriétés `core.xml` en DOCX (9.1), jamais traité côté PDF.

**Objets orphelins — le constat le plus sérieux** : `page.apply_redactions()` (PyMuPDF) ne supprime pas les anciens objets de contenu de page pré-caviardage du fichier ; il les déréférence seulement en pointant la page vers un nouvel objet. Sans collecte des objets inutilisés à la sauvegarde (`garbage=0`, le défaut, utilisé sans le savoir par le code existant), ces objets restent physiquement présents dans le fichier de sortie. **[VÉRIFIÉ]** sur `caviar_test.pdf` : sur 414 objets dans le fichier, seuls 83 étaient réellement atteignables depuis l'arbre de pages courant — les 331 autres incluaient plusieurs flux de contenu complets de l'état pré-caviardage, contenant le nom complet du patient, sa date de naissance et son numéro de dossier en clair, extractibles avec une dizaine de lignes de code PyMuPDF (aucun outil spécialisé de forensic nécessaire — `mutool clean`, `qpdf`, ou l'inspecteur d'objets d'Acrobat auraient tout autant suffi). La page rendue affichait pourtant bien le caviardage — **contournement total de la protection au niveau du fichier, indépendant de la qualité de la détection**, même famille de bug que 9.1 (DOCX, suivi des modifications) mais plus grave : ici c'est le comportement par défaut de tout appel `doc.save()`, pas un cas d'usage particulier (mode suivi activé) — donc probablement présent dans **tous** les PDF jamais produits par ce pipeline jusqu'ici.

### 10.3 Correctif, testé

- **`_wipe_pdf_metadata(doc)`** (nouvelle fonction, miroir de `_wipe_core_properties` côté DOCX) : `doc.set_metadata({})` puis `doc.del_xml_metadata()` si un paquet XMP est présent.
- **`doc.save(output_path, garbage=4, clean=True, deflate=True)`** au lieu de `doc.save(output_path)` — `garbage=4` purge les objets non référencés (le cœur du correctif), `clean=True` réécrit les flux de contenu, `deflate=True` recompresse (bénéfice de taille, pas un enjeu de sécurité en soi).

**Test de validation** : rejoué via les **vrais points d'entrée** (`_handle_detect_pdf` puis `_finalize_pdf_job`, pas des fonctions internes isolées — leçon de la session 7 appliquée directement) sur `test_med.pdf` (mêmes données patient que `caviar_test.pdf`) : 124 détections, 106 zones après regroupement, toutes caviardées. Sur le fichier de sortie : métadonnées vides, objets atteignables ramenés à 82 (contre 414 dans `caviar_test.pdf`), et un balayage de **tous** les objets du fichier (pas seulement l'arbre de pages, la même méthode que celle qui avait révélé la fuite) ne retrouve trace nulle part des 77 chaînes de PII réellement extraites du document d'origine — nom, date de naissance et numéro de dossier inclus. Suite de tests unitaires existante (13 tests) toujours au vert après reconstruction de l'image `anonymiseur-app`.

### 10.4 Constat incident, non corrigé cette session — ⏳ ouvert, diagnostiqué précisément

En construisant le jeu de chaînes PII pour la vérification 10.3, deux mots génériques (`BIOLOGIE`, `Laboratoire`) sont d'abord apparus suspects lors d'un premier passage de test — **ce premier diagnostic s'est révélé partiellement faux et a été corrigé avant d'écrire quoi que ce soit dans le code** : le script de vérification initial chargeait `main.py` depuis une copie isolée sans son dossier `themes/` à côté (`THEMES_DIR = Path(__file__).parent / "themes"` résolu vers un chemin inexistant), donc **aucun thème n'était réellement appliqué** — `excluded_entity_types` ne s'exécutait jamais. Une fois le script corrigé (copie de `main.py` + `themes/` ensemble, thème médical réellement chargé et vérifié `THEMES["medical"]` non vide) :

- **`BIOLOGIE` (`ORGANIZATION`) : déjà résolu, aucune action nécessaire.** Avec le vrai thème médical, `excluded_entity_types: ["ORGANIZATION"]` (déjà en place depuis la session intermédiaire non documentée, voir note de traçabilité en tête de document) filtre entièrement ce type — 0 détection sur les 3 occurrences de la page, de façon cohérente, pas de fuite.
- **`Laboratoire` (`LOCATION`) : trou réel, confirmé, plus profond qu'un simple mot manquant à l'`allow_list`.** Ajouté à `medical.json.allow_list` sur suggestion initiale — **testé, insuffisant** : l'unique occurrence effectivement caviardée sur la page 2 ne provient pas d'un empan NER propre `"Laboratoire"`, mais d'un empan **fusionné à travers un saut de ligne de mise en page** : `"Laboratoire\n \nExemplaire"` (LOCATION, score 0.85) — l'extraction PyMuPDF (`page.get_text()`) concatène en une seule chaîne l'étiquette de formulaire "(s) au(x) : Laboratoire" et le début de la ligne suivante, sans rapport, "Exemplaire patient". Un mot isolé dans l'`allow_list` (correspondance exacte) ne peut pas matcher cette chaîne à saut de ligne. Deux conséquences en cascade, toutes deux **[VÉRIFIÉES]** par test direct sur `test_med.pdf` :
  1. `page.search_for(entity_text)` sur cet empan à saut de ligne renvoie **3 rects disjoints** au lieu d'un seul — dont un qui couvre bien "Laboratoire", mais aussi un second qui caviarde à tort une zone contenant "Exemplaire" (texte sans rapport, effet de bord du bug plutôt qu'un choix délibéré de sur-caviardage comme en 9.3).
  2. La chaîne utilisée comme candidat de propagation inter-pages (passe 2, `propagate_candidates`) est cette même chaîne corrompue par le saut de ligne — elle ne correspond littéralement à rien sur les pages 0/1, donc la propagation vers les 4 autres occurrences propres du mot échoue silencieusement. C'est la vraie cause du symptôme initial ("une seule occurrence sur 5 détectée"), pas un problème de couverture de `PROPAGATED_ENTITY_TYPES` comme supposé au premier passage.

Décision prise avec l'utilisateur (question posée explicitement, compromis non trivial) : **ne pas corriger ce bug cette session**. Une correction sûre nécessiterait de modifier la façon dont les empans multi-lignes sont traités dans `_detect_pdf` (ex. ne garder que le premier segment de ligne pour la recherche/propagation) — risque de régression sur tout document PDF, et un compromis réel à trancher consciemment : tronquer un empan multi-ligne pour éviter le caviardage parasite pourrait, dans un autre document, faire manquer la 2ᵉ ligne d'un vrai nom réparti sur deux lignes — à l'opposé du principe déjà acté en 9.2/9.3 ("ne jamais arbitrer en faveur de moins de faux positifs si ça coûte des faux négatifs"). L'entrée `"Laboratoire"` reste dans `medical.json.allow_list` (inoffensive, ne matche rien actuellement, mais deviendrait utile si un futur document contient un empan NER propre `"Laboratoire"` isolé). Mots génériques de en-tête concernés ici, pas des identifiants (aucun nom de patient, date ou numéro touché) — sévérité bien moindre que 10.2, mais signal de plus, et de nature différente (empans NER corrompus par la mise en page, pas juste vocabulaire mal classé), pour l'item déjà ouvert le plus impactant du projet (mesure de la qualité de détection PII sur corpus varié, section 6 session 5 / synthèse ci-dessous).

### 10.5 Note de traçabilité

`caviar_test` et `caviar_test.pdf` (les deux fichiers ayant servi de déclencheur, identiques) ont été supprimés par erreur pendant le nettoyage des artefacts de test de cette session — non trackés par git, non récupérables. Cas reproductible via `test_med.pdf` (même patient), donc sans perte d'information pour l'audit, mais à signaler pour traçabilité.

---

## 8. Synthèse et priorisation mise à jour

**✅ Fait cette session (7) :**
- 6 fuites de données structurelles DOCX trouvées et corrigées, chacune vérifiée par test réel (9.1) — probablement le constat le plus sérieux du projet à ce jour, dans la continuité de ce que la section "qualité de détection PII" de la session 5 avait commencé à révéler, mais d'une nature différente (contournement total de la détection, pas imprécision de détection)
- Diagnostic et traitement d'un biais de détection Presidio par un patch du moteur lui-même plutôt qu'un réglage applicatif, après qu'une première piste (seuil de score) se soit révélée dangereuse et ait été écartée à temps (9.2)
- Deux failles de sécurité propres trouvées et corrigées sur une fonctionnalité de diagnostic ajoutée pendant la session elle-même, avant la fin de cette même session (9.5)
- Trois bugs de détection structurelle trouvés et corrigés grâce à un outillage de diagnostic construit en cours de route, invisibles aux tests anticipés (9.4)

**🟡 Décisions de risque documentées cette session :**
- Faux positif assumé sur les titres de section numérotés par lettre, pour ne pas rater de vrais noms au format compact (9.3)
- `medical.json`/`patch_recognizers.py` : redondance non nettoyée, sans risque fonctionnel immédiat (9.3)

**✅ Fait cette session (8) :**
- Fuite structurelle PDF critique trouvée et corrigée : objets de contenu pré-caviardage (nom, date de naissance, numéro de dossier patient en clair) et métadonnées non nettoyées survivaient dans tout fichier "anonymisé" produit par le pipeline — comportement par défaut, pas un cas limite (10.2)
- Correctif (`_wipe_pdf_metadata` + `garbage=4, clean=True` à la sauvegarde) revérifié de bout en bout via les vrais points d'entrée, sur les 77 valeurs de PII réellement extraites du document d'origine (10.3)

**🟡 Décisions de risque documentées cette session :**
- `Laboratoire`/`LOCATION` : trou de caviardage diagnostiqué précisément (empan NER fusionné à travers un saut de ligne PDF, avec effet de bord de sur-caviardage parasite) — correction délibérément reportée après arbitrage explicite avec l'utilisateur, le compromis de correction n'étant pas sans risque de régression (10.4)
- `BIOLOGIE`/`ORGANIZATION` : suspecté à tort en premier passage (bug du script de vérification, pas du produit) — confirmé sans action nécessaire, déjà couvert par `excluded_entity_types` (10.4)

**Reste le plus impactant, toutes sessions confondues :**
1. Un vrai travail de mesure de la qualité de détection PII sur corpus varié (section 6 de la session 5) — toujours en attente ; la session 7 (DOCX) puis le constat 10.4 (PDF) en illustrent encore l'importance
2. Risque XXE non vérifié sur le nouveau chemin DOCX (9.6.2) — le seul des nouveaux points ouverts qui touche potentiellement à une vraie exécution/fuite plutôt qu'à un coût de ressources
3. Certificat TLS de confiance avant toute préproduction (1.19)
4. Le pentest final (section 7)
5. Processus récurrent de veille CVE (1.13)

**Leçon opérationnelle de la session 7, toujours valable** : une vérification qui appelle des fonctions internes plutôt que les vrais points d'entrée peut donner une fausse confiance — un bug d'intégration réel (9.1) est passé inaperçu à travers plusieurs tests unitaires qui passaient tous, découvert seulement en testant le point d'entrée réel de bout en bout. Rejoint la leçon de la session 6 sur la vérification de l'état réel plutôt que supposé, appliquée cette fois au code plutôt qu'à l'infrastructure. **Appliquée directement en session 8** : le correctif PDF a été revérifié via `_handle_detect_pdf`/`_finalize_pdf_job` réels plutôt que par une manipulation isolée de `fitz.Document`.

---

*Document de travail — à mettre à jour au fur et à mesure des corrections apportées et des points levés lors de la revue avec le RSSI/DPO.*
