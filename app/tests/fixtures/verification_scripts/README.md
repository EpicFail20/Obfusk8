# Scripts de vérification manuelle (non-régression fuite PII)

Ces scripts appellent les vrais points d'entrée (`_handle_detect_*` /
`_finalize_*_job`) de `main.py` pour vérifier qu'un document "anonymisé" ne
contient plus AUCUNE trace des données caviardées, nulle part dans le
fichier (pas seulement dans le rendu visible) — voir `plan_audit.md`
sections 10 et 11.

## Prérequis : app_copy

`main.py` charge ses thèmes via `Path(__file__).parent / "themes"` — pour
que les scripts voient les vrais thèmes (`excluded_entity_types`,
`allow_list`, recognizers), il faut copier `main.py` ET `themes/` ensemble
dans le volume partagé du conteneur `app` (`/var/lib/anonymiseur/workdir`
côté hôte = `/data/tmp` côté conteneur), pas seulement `main.py` seul.

```bash
rm -rf /var/lib/anonymiseur/workdir/app_copy
mkdir -p /var/lib/anonymiseur/workdir/app_copy
cp /home/debian/anonymiseur/app/main.py /var/lib/anonymiseur/workdir/app_copy/main.py
cp -r /home/debian/anonymiseur/app/themes /var/lib/anonymiseur/workdir/app_copy/themes
```

## Construire les fixtures (déjà présentes dans ce dossier, à régénérer
## seulement si le format de test doit changer)

```bash
cp app/tests/fixtures/verification_scripts/build_docx_fixture.py /var/lib/anonymiseur/workdir/
cp app/tests/fixtures/verification_scripts/build_csv_fixture.py /var/lib/anonymiseur/workdir/
docker exec anonymiseur-app-1 python3 /data/tmp/build_docx_fixture.py /data/tmp/docx_fixture.docx
docker exec anonymiseur-app-1 python3 /data/tmp/build_csv_fixture.py /data/tmp/csv_fixture.csv
cp /var/lib/anonymiseur/workdir/docx_fixture.docx app/tests/fixtures/
cp /var/lib/anonymiseur/workdir/csv_fixture.csv app/tests/fixtures/
```

## Lancer les vérifications

```bash
cp app/tests/fixtures/docx_fixture.docx /var/lib/anonymiseur/workdir/
cp app/tests/fixtures/csv_fixture.csv /var/lib/anonymiseur/workdir/
cp /home/debian/anonymiseur/test_med.pdf /var/lib/anonymiseur/workdir/   # PDF réel, riche en PII fictives
cp app/tests/fixtures/verification_scripts/verify_docx.py /var/lib/anonymiseur/workdir/
cp app/tests/fixtures/verification_scripts/verify_csv.py /var/lib/anonymiseur/workdir/
cp app/tests/fixtures/verification_scripts/verify_pdf_full.py /var/lib/anonymiseur/workdir/

docker exec anonymiseur-app-1 python3 /data/tmp/verify_docx.py
docker exec anonymiseur-app-1 python3 /data/tmp/verify_csv.py
docker exec anonymiseur-app-1 python3 /data/tmp/verify_pdf_full.py
```

Chaque script fait tourner detect+finalize réels, puis balaie de façon
exhaustive TOUTES les parties du fichier de sortie (toutes les entrées du
zip pour DOCX, tous les objets PDF y compris ceux non référencés par
l'arbre de pages courant, les octets bruts pour CSV) à la recherche des
chaînes PII injectées dans le fixture. "AUCUNE fuite" = seul résultat
attendu ; toute autre sortie doit être investiguée avant de considérer le
pipeline sûr.

**Ne pas supprimer les fixtures ni les fichiers de sortie générés dans
`/var/lib/anonymiseur/workdir` — conservés pour pouvoir rejouer les tests.**

## Test XXE (section 12 du plan d'audit)

`xxe_lxml_probe.py` teste le parseur lxml exact de python-docx isolément
(lecture fichier local, bombe d'entités, SSRF via DTD externe), sans
dépendre d'un fixture DOCX. `build_xxe_docx.py` construit `xxe_fixture.docx`
(conservé, `word/document.xml` et `word/footnotes.xml` contiennent des
payloads XXE) à partir de `docx_fixture.docx` ; `verify_xxe.py` le fait
passer par le vrai pipeline et vérifie qu'aucun contenu de fichier local
(canari créé à la volée + `/etc/passwd`) n'apparaît dans la sortie.

```bash
cp app/tests/fixtures/verification_scripts/xxe_lxml_probe.py /var/lib/anonymiseur/workdir/
docker exec anonymiseur-app-1 python3 /data/tmp/xxe_lxml_probe.py

cp app/tests/fixtures/docx_fixture.docx /var/lib/anonymiseur/workdir/
cp app/tests/fixtures/verification_scripts/build_xxe_docx.py /var/lib/anonymiseur/workdir/
docker exec anonymiseur-app-1 python3 /data/tmp/build_xxe_docx.py /data/tmp/xxe_fixture.docx

cp app/tests/fixtures/verification_scripts/verify_xxe.py /var/lib/anonymiseur/workdir/
docker exec anonymiseur-app-1 python3 /data/tmp/verify_xxe.py
```

## Test zip-bomb par nombre d'entrées (section 13 du plan d'audit, 9.6.1)

`build_zipbomb_entries.py` construit à la volée une archive proche de
`MAX_UPLOAD_MB` (25 Mo) contenant ~260 000 entrées minimales (0 octet
chacune) déguisée en `.docx` — **pas conservée en fixture** (≈24 Mo,
régénérée en quelques secondes, pas d'intérêt à la garder en git).
`verify_zipbomb.py` mesure le temps CPU et la mémoire consommés par
`_validate_docx_zip`/`_detect_file_kind` (doivent rejeter en ~0s grâce à la
lecture directe de l'EOCD, `_peek_zip_entry_count`, avant tout appel à
`zipfile.ZipFile()`) :

```bash
cp app/tests/fixtures/verification_scripts/build_zipbomb_entries.py /var/lib/anonymiseur/workdir/
docker exec anonymiseur-app-1 python3 /data/tmp/build_zipbomb_entries.py 10 /data/tmp/zipbomb_entries.docx

cp app/tests/fixtures/verification_scripts/verify_zipbomb.py /var/lib/anonymiseur/workdir/
docker exec anonymiseur-app-1 python3 /data/tmp/verify_zipbomb.py
```

Résultat attendu : `_validate_docx_zip` et `_detect_file_kind` rejettent en
~0.000s (pas ~1,1s) — c'est le seul indicateur qui compte, le "Pipeline
complet `_handle_detect_docx`" du script reste volontairement lent (~1s)
car il appelle ce handler directement, en sautant `_detect_file_kind` —
un raccourci de test qui n'existe pas sur le vrai chemin HTTP
(`detect_document` appelle toujours `_detect_file_kind` en premier).

## Miniature de document DOCX (`docProps/thumbnail.jpeg`)

`verify_docx_thumbnail.py` vérifie que `_wipe_docx_thumbnail()` retire bien
la miniature du zip de sortie (relation package-level `_rels/.rels`,
reltype `.../metadata/thumbnail`, jamais dans `word/_rels/document.xml.rels`
comme les relations habituelles du corps) et que le fichier reste ouvrable
et correctement caviardé par ailleurs. `docx_fixture.docx` contient déjà
une miniature (ajoutée par python-docx par défaut à la sauvegarde), pas
besoin d'un fixture séparé.

```bash
cp app/tests/fixtures/verification_scripts/verify_docx_thumbnail.py /var/lib/anonymiseur/workdir/
docker exec anonymiseur-app-1 python3 /data/tmp/verify_docx_thumbnail.py
```

## Efficacité du caviardage manuel d'image (PDF)

`probe_image_redaction.py` ne dépend d'aucun fixture ni du pipeline HTTP —
il construit une image de test (moitié rouge = "PII", moitié bleue =
contenu à conserver), applique le même mécanisme que
`_apply_manual_redactions()` (zone tracée par l'utilisateur, uniquement sur
la moitié rouge) suivi du même `doc.save(garbage=4, clean=True,
deflate=True)` que `_finalize_pdf_job`, puis balaie TOUS les objets image
du fichier de sortie (pas seulement l'arbre de pages) à la recherche du
rouge d'origine. Répond à la question posée explicitement par
l'utilisateur : le caviardage manuel d'une zone image est-il réellement
irrécupérable (pixels réécrits) ou seulement un cache visuel par-dessus
(pixels d'origine toujours présents ailleurs dans le fichier) ?

**Résultat [VÉRIFIÉ]** : `page.apply_redactions()` (défauts du code,
`images=2` = "blank out overlapping image parts") réécrit réellement les
pixels de la zone caviardée dans un nouvel objet image, purgé de tout
rouge résiduel ; combiné à `garbage=4`, aucune copie non caviardée de
l'image d'origine ne survit ailleurs dans le fichier. Autrement dit : le
mécanisme de zones manuelles déjà en place pour le PDF satisfait
l'exigence "aucune possibilité de récupération post-caviardage" pour les
images — **sur PDF uniquement** ; le DOCX n'a aujourd'hui aucun mécanisme
de zone manuelle équivalent (pas d'UI de sélection de zone sur une image
incrustée), donc la stratégie "l'utilisateur caviarde lui-même les images"
n'est déployable que côté PDF pour l'instant.

```bash
cp app/tests/fixtures/verification_scripts/probe_image_redaction.py /var/lib/anonymiseur/workdir/
docker exec anonymiseur-app-1 python3 /data/tmp/probe_image_redaction.py
```
