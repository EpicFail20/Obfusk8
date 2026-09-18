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

# Variante classique "external DTD" : le sous-ensemble EXTERNE (pas interne)
# n'a pas la restriction PEReferences -> teste si un DTD externe distant
# est meme charge (load_dtd doit etre False par defaut, donc non), et la
# variante "tout dans le DTD externe" avec no_network
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
