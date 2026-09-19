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
    # "-Xxxxxxx" string repeated N times, with no final period nor a
    # valid \b boundary at the end (ends with a character that breaks
    # the word) to force the engine to explore as many splits as
    # possible before failing.
    "FrenchInitialSurnameRecognizer": lambda n: "A." + ("-Bbbbbbbbbb" * n) + "9",
    # Long run of zeros followed by a character that breaks the final boundary
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
