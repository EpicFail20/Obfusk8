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
