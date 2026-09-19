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
"""
Builds a fictitious DOCX fixture exercising all the structural areas
already fixed by the obfusk8 pipeline (tracked changes, metadata,
hyperlinks, comments, footnotes/endnotes, header/footer) + a text box
(known limitation, not covered).
All the data is fictitious, generated for this test.
"""
import zipfile, shutil, sys
import docx
from docx.oxml import parse_xml
from docx.oxml.ns import qn, nsdecls
from docx.opc.constants import RELATIONSHIP_TYPE as RT

OUT = "/data/tmp/fixture_build.docx"
FINAL = sys.argv[1] if len(sys.argv) > 1 else "/data/tmp/docx_fixture.docx"

d = docx.Document()

# --- normal body ---
d.add_paragraph("Ceci est un document de test fictif pour vérifier le pipeline d'anonymisation.")
d.add_paragraph("Le patient se nomme Isabelle FONTAINE, né le 03/11/1980, joignable au 06 11 22 33 44.")

# --- header / footer ---
section = d.sections[0]
section.header.paragraphs[0].text = "Confidentiel - suivi par Gregoire VASSEUR"
section.footer.paragraphs[0].text = "Contact urgence : Sylvie MERCIER"

# --- hyperlink (displayed text + separate target) ---
part = d.part
r_id = part.relate_to("mailto:olivier.rousseau@example-fictif.test", RT.HYPERLINK, is_external=True)
hyperlink_xml = (
    f'<w:p {nsdecls("w", "r")}>'
    f'<w:hyperlink r:id="{r_id}">'
    f'<w:r><w:t>Contacter Olivier ROUSSEAU</w:t></w:r>'
    f'</w:hyperlink>'
    f'</w:p>'
)
hyperlink_p = parse_xml(hyperlink_xml)
d.element.body.append(hyperlink_p)

# --- tracked changes (deleted text, remains in the XML) ---
del_xml = (
    f'<w:p {nsdecls("w")}>'
    f'<w:del w:id="900" w:author="testeur" w:date="2024-01-01T00:00:00Z">'
    f'<w:r><w:delText>Nom supprimé en mode suivi : Frederic LAMBERT, né le 22/02/1990</w:delText></w:r>'
    f'</w:del>'
    f'</w:p>'
)
del_p = parse_xml(del_xml)
d.element.body.append(del_p)

# --- final normal paragraph (marker for end of body) ---
last_p = d.add_paragraph("Fin du corps du document de test.")

# --- comment ---
run = last_p.add_run(" [ancre commentaire]")
d.add_comment(run, text="Voir dossier de Camille GIRARD pour comparaison", author="Relecteur Test")

# --- identifying metadata (core properties), via the python-docx API
# itself rather than manual XML injection -- avoids producing an
# invalid core.xml (duplicated element) that would skew the test ---
d.core_properties.author = "Nicolas PETIT"
d.core_properties.last_modified_by = "Nicolas PETIT"
d.core_properties.subject = "Dossier confidentiel Julie MOREL"
d.core_properties.comments = "Revu par Nicolas PETIT le 2024-01-01"

d.save(OUT)

# --- footnotes.xml / endnotes.xml: python-docx 1.2.0 has no API for
# this, manual injection at the zip level (same reltypes as
# NOTE_RELTYPES in main.py) ---
FOOTNOTES_XML = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes {nsdecls("w")}>
<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>
<w:footnote w:id="1"><w:p><w:r><w:t>Note de bas de page : Antoine BOUCHER, dossier F778899123</w:t></w:r></w:p></w:footnote>
</w:footnotes>'''

ENDNOTES_XML = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:endnotes {nsdecls("w")}>
<w:endnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>
<w:endnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:endnote>
<w:endnote w:id="1"><w:p><w:r><w:t>Note de fin : Valerie ANDRE, tel 07 55 44 33 22</w:t></w:r></w:p></w:endnote>
</w:endnotes>'''

REL_FOOTNOTES = '<Relationship Id="rIdFootnotesFixture" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>'
REL_ENDNOTES = '<Relationship Id="rIdEndnotesFixture" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes" Target="endnotes.xml"/>'
CT_FOOTNOTES = '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
CT_ENDNOTES = '<Override PartName="/word/endnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"/>'

with zipfile.ZipFile(OUT, "r") as zin:
    names = zin.namelist()
    contents = {n: zin.read(n) for n in names}

doc_rels = contents["word/_rels/document.xml.rels"].decode("utf-8")
doc_rels = doc_rels.replace("</Relationships>", REL_FOOTNOTES + REL_ENDNOTES + "</Relationships>")
contents["word/_rels/document.xml.rels"] = doc_rels.encode("utf-8")

ct = contents["[Content_Types].xml"].decode("utf-8")
ct = ct.replace("</Types>", CT_FOOTNOTES + CT_ENDNOTES + "</Types>")
contents["[Content_Types].xml"] = ct.encode("utf-8")

contents["word/footnotes.xml"] = FOOTNOTES_XML.encode("utf-8")
contents["word/endnotes.xml"] = ENDNOTES_XML.encode("utf-8")

# note reference in the body (structural realism, not required by the
# detection pipeline but avoids a docx that looks corrupted if opened
# manually in Word)
doc_xml = contents["word/document.xml"].decode("utf-8")
ref_xml = (
    '<w:p><w:r><w:t xml:space="preserve">Renvoi vers note : </w:t></w:r>'
    '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteReference w:id="1"/></w:r>'
    '<w:r><w:rPr><w:rStyle w:val="EndnoteReference"/></w:rPr><w:endnoteReference w:id="1"/></w:r>'
    '</w:p>'
)
doc_xml = doc_xml.replace("<w:sectPr", ref_xml + "<w:sectPr", 1)
contents["word/document.xml"] = doc_xml.encode("utf-8")

with zipfile.ZipFile(FINAL, "w", zipfile.ZIP_DEFLATED) as zout:
    for name, data in contents.items():
        zout.writestr(name, data)

print("Fixture DOCX construit:", FINAL)
