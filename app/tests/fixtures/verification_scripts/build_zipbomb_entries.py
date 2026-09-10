import zipfile, io, sys, time

TARGET_MB = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
OUT = sys.argv[2] if len(sys.argv) > 2 else "/data/tmp/zipbomb_entries.docx"

VALID_DOCUMENT_XML = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>Document de test (zip-bomb par nombre d'entrees).</w:t></w:r></w:p><w:sectPr/></w:body>
</w:document>'''

buf = io.BytesIO()
t0 = time.time()
n = 0
with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
    zf.writestr("word/document.xml", VALID_DOCUMENT_XML)
    zf.writestr("[Content_Types].xml", b"<Types/>")
    n = 2
    while True:
        size = buf.tell()
        if size >= TARGET_MB * 1024 * 1024:
            break
        # nom court, unique, 0 octet de contenu -> cout minimal par entree
        zf.writestr(f"junk/{n}", b"")
        n += 1

build_time = time.time() - t0
data = buf.getvalue()
with open(OUT, "wb") as f:
    f.write(data)

print(f"Construit: {OUT}")
print(f"Taille finale: {len(data)/1024/1024:.2f} Mo")
print(f"Nombre d'entrees: {n}")
print(f"Temps de construction: {build_time:.2f}s")
