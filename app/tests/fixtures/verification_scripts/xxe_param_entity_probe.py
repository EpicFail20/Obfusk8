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
from lxml import etree
import time

canary_path = "/data/tmp/xxe_canary_secret.txt"
with open(canary_path, "w") as f:
    f.write("SECRET_CANARY_VALUE_1234567890")

oxml_parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False)

payload = f'''<?xml version="1.0" standalone="no"?>
<!DOCTYPE r [
  <!ENTITY % contents SYSTEM "file://{canary_path}">
  <!ENTITY % payload "<!ENTITY output '%contents;'>">
  %payload;
]>
<r><w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:r><w:t>&output;</w:t></w:r></w:p></r>'''

print("=== Test avec standalone=\"no\" ===")
t0 = time.time()
try:
    root = etree.fromstring(payload.encode("utf-8"), oxml_parser)
    text = etree.tostring(root).decode("utf-8", errors="replace")
    print(f"Parsed OK en {time.time()-t0:.3f}s")
    print("Contient le canari:", "SECRET_CANARY_VALUE" in text)
    print("Sortie:", text[:400])
except Exception as exc:
    print(f"EXCEPTION après {time.time()-t0:.3f}s: {type(exc).__name__}: {exc}")

# Classic "external DTD" variant: the EXTERNAL subset (not internal)
# does not have the PEReferences restriction -> tests whether a remote
# external DTD is even loaded (load_dtd must be False by default, so
# no), and the "everything in the external DTD" variant with no_network
payload2 = f'''<?xml version="1.0"?>
<!DOCTYPE r SYSTEM "file:///data/tmp/xxe_external.dtd">
<r><w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:r><w:t>&output;</w:t></w:r></w:p></r>'''

with open("/data/tmp/xxe_external.dtd", "w") as f:
    f.write(f'<!ENTITY % contents SYSTEM "file://{canary_path}">\n<!ENTITY % payload "<!ENTITY output \'%contents;\'>">\n%payload;\n')

print("\n=== Test DTD externe local (SYSTEM file://) ===")
t0 = time.time()
try:
    root = etree.fromstring(payload2.encode("utf-8"), oxml_parser)
    text = etree.tostring(root).decode("utf-8", errors="replace")
    print(f"Parsed OK en {time.time()-t0:.3f}s")
    print("Contient le canari:", "SECRET_CANARY_VALUE" in text)
    print("Sortie:", text[:400])
except Exception as exc:
    print(f"EXCEPTION après {time.time()-t0:.3f}s: {type(exc).__name__}: {exc}")
