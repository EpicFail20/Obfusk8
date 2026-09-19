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
import zipfile, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "/data/tmp/xxe_fixture.docx"
BASE = "/data/tmp/docx_fixture.docx"  # already-built valid DOCX fixture

with zipfile.ZipFile(BASE, "r") as zin:
    contents = {n: zin.read(n) for n in zin.namelist()}

# --- document.xml with a DOCTYPE + external entity pointing to a
# canary file on the shared volume, and to /etc/passwd ---
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

# --- footnotes.xml with the same kind of payload, to test the custom
# parsing path (_get_note_part -> docx.oxml.parse_xml) ---
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
