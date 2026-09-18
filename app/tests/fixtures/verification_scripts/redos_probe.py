import re, time

PATTERNS = {
    "FrenchInitialSurnameRecognizer": r"\b[A-ZÀ-Ý](?:-[A-ZÀ-Ý])?\.\s?[A-ZÀ-Ý][a-zà-ÿ]+(?:[- ][A-ZÀ-Ý][a-zà-ÿ]+)*\b",
    "PatientIEP": r"\b(?:IEP|iep|Iep)\s{0,2}[:.\-]?\s{0,2}0+\d{7,}\b",
    "PatientDossier1": r"\b[A-Z]\d{8,12}\b",
    "PatientDossier2": r"\b\d{1,4}[A-Z]\d{6,10}\b",
    "StreetAddress": r"\b\d{1,4}\s?(?:bis|ter)?,?\s+(?:rue|avenue|av\.?|boulevard|bd\.?|impasse|all[ée]e|chemin|place|route|quai|cours|square)\s+[A-ZÀ-Ýa-zà-ÿ][A-ZÀ-Ýa-zà-ÿ'\- ]{1,60}",
    "NumeroPatientGeneric": r"\bnum[eé]ro\s{0,2}patient\s{0,2}[:.\-]?\s{0,2}[A-Za-z0-9]{5,}\b",
}

ADVERSARIAL = {
    # Chaine "-Xxxxxxx" repetee N fois, sans point final ni frontiere \b
    # valide a la fin (termine par un caractere qui casse le mot) pour
    # forcer le moteur a explorer un maximum de decoupages avant d'echouer.
    "FrenchInitialSurnameRecognizer": lambda n: "A." + ("-Bbbbbbbbbb" * n) + "9",
    # Longue suite de zeros suivie d'un caractere qui casse la frontiere finale
    "PatientIEP": lambda n: "IEP" + ("0" * n) + "9x",
    "PatientDossier1": lambda n: "A" + ("9" * n) + "x",
    "PatientDossier2": lambda n: ("9" * n) + "Ax",
    "StreetAddress": lambda n: "12 rue " + ("a" * n),
    "NumeroPatientGeneric": lambda n: "numero patient " + ("9" * n) + "x",
}

for name, pattern in PATTERNS.items():
    compiled = re.compile(pattern)
    gen = ADVERSARIAL[name]
    print(f"\n=== {name} ===")
    for n in (100, 1000, 5000, 20000, 50000):
        s = gen(n)
        t0 = time.time()
        compiled.search(s)
        elapsed = time.time() - t0
        print(f"  n={n:>6} len={len(s):>6} -> {elapsed*1000:.2f} ms")
        if elapsed > 2.0:
            print("  !!! DEPASSE 2s, arret de la montee en charge pour ce motif")
            break
