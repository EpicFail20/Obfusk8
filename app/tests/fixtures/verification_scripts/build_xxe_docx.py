import zipfile, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "/data/tmp/xxe_fixture.docx"
BASE = "/data/tmp/docx_fixture.docx"  # fixture DOCX valide déjà construit

with zipfile.ZipFile(BASE, "r") as zin:
    contents = {n: zin.read(n) for n in zin.namelist()}

# --- document.xml avec DOCTYPE + entité externe pointant vers un fichier
# canari sur le volume partagé, et vers /etc/passwd ---
malicious_doc = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!DOCTYPE w:document [
  <!ENTITY xxe1 SYSTEM "file:///data/tmp/xxe_canary_secret.txt">
  <!ENTITY xxe2 SYSTEM "file:///etc/passwd">
]>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
<w:p><w:r><w:t>Canary: &xxe1; Passwd: &xxe2;</w:t></w:r></w:p>
<w:p><w:r><w:t>Nom fictif pour test : Robert LECLERC, ne le 05/05/1985</w:t></w:r></w:p>
<w:sectPr/>
</w:body>
</w:document>'''
contents["word/document.xml"] = malicious_doc

# --- footnotes.xml avec le meme type de payload, pour tester le chemin de
# parsing custom (_get_note_part -> docx.oxml.parse_xml) ---
malicious_footnotes = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!DOCTYPE w:footnotes [
  <!ENTITY xxe3 SYSTEM "file:///data/tmp/xxe_canary_secret.txt">
]>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>
<w:footnote w:id="1"><w:p><w:r><w:t>Note: &xxe3;</w:t></w:r></w:p></w:footnote>
</w:footnotes>'''
contents["word/footnotes.xml"] = malicious_footnotes

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zout:
    for name, data in contents.items():
        zout.writestr(name, data)

print("Fixture XXE construit:", OUT)
