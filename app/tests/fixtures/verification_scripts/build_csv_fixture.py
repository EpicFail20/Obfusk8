import csv, sys

path = sys.argv[1] if len(sys.argv) > 1 else "/data/tmp/csv_fixture.csv"

rows = [
    ["Nom", "Prenom", "Email", "Telephone", "DateNaissance", "NumeroDossier", "Notes"],
    ["DUPUIS", "Marc", "marc.dupuis@example-fictif.test", "06 22 33 44 55", "14/03/1972", "M772233114", "RAS"],
    ["LEFEBVRE", "Anne", "anne.lefebvre@example-fictif.test", "07 88 99 00 11", "29/07/1965", "A650729987", "Suivi standard"],
    # cellule avec valeur contenant une virgule + guillemets (doit être
    # correctement ré-échappée en sortie)
    ["NOEL", "Chantal", "chantal.noel@example-fictif.test", "06 00 11 22 33", "01/01/1990", "N900101555", "Adresse: 5, rue de la Paix, 75002 Paris"],
    # cellule avec un saut de ligne interne (quotée)
    ["ROY", "Bernard", "bernard.roy@example-fictif.test", "06 44 55 66 77", "12/12/1955", "R551212333", "Ligne 1 du commentaire\nLigne 2 mentionnant Bernard ROY à nouveau"],
    # ligne à une seule donnée utile (bug 9.4.3 : tableau à une seule ligne mal classé)
    ["Denomination", "installation", "", "", "", "", ""],
]

with open(path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=",")
    for row in rows:
        w.writerow(row)

print("Fixture CSV construit:", path)
