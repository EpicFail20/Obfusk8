from lxml import etree
import time, os

# Marqueur canari sur le volume partagé (accessible tel quel depuis le
# conteneur "app" via /data/tmp), pour voir si son contenu peut fuiter par
# une entité externe SYSTEM référencée dans un DOCX malveillant.
canary_path = "/data/tmp/xxe_canary_secret.txt"
with open(canary_path, "w") as f:
    f.write("SECRET_CANARY_VALUE_1234567890")

oxml_parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False)
print("lxml version:", etree.LXML_VERSION, etree.LIBXML_VERSION)

payloads = {
    "file_read_local": f'''<?xml version="1.0"?>
<!DOCTYPE r [ <!ENTITY xxe SYSTEM "file://{canary_path}"> ]>
<r><w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:r><w:t>&xxe;</w:t></w:r></w:p></r>''',

    "file_read_etc_passwd": '''<?xml version="1.0"?>
<!DOCTYPE r [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<r><w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:r><w:t>&xxe;</w:t></w:r></w:p></r>''',

    "billion_laughs": '''<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
]>
<r><w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:r><w:t>&lol4;</w:t></w:r></w:p></r>''',

    "external_dtd_ssrf": '''<?xml version="1.0"?>
<!DOCTYPE r SYSTEM "http://169.254.169.254/latest/meta-data/">
<r><w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:r><w:t>x</w:t></w:r></w:p></r>''',
}

for name, payload in payloads.items():
    print(f"\n=== {name} ===")
    t0 = time.time()
    try:
        root = etree.fromstring(payload.encode("utf-8"), oxml_parser)
        elapsed = time.time() - t0
        text = etree.tostring(root).decode("utf-8", errors="replace")
        print(f"Parsed OK in {elapsed:.3f}s, len={len(text)}")
        print("Contains canary secret:", "SECRET_CANARY_VALUE" in text)
        print("Contains /etc/passwd content (root: or similar):", "root:" in text)
        print("Serialized (truncated):", text[:300])
    except Exception as exc:
        elapsed = time.time() - t0
        print(f"EXCEPTION after {elapsed:.3f}s: {type(exc).__name__}: {exc}")

os.remove(canary_path)
