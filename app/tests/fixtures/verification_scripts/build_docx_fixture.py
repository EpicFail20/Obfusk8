"""
Construit un fixture DOCX fictif exerçant toutes les zones structurelles
déjà corrigées par le pipeline anonymiseur (suivi des modifications,
métadonnées, hyperliens, commentaires, notes de bas de page/de fin,
en-tête/pied de page) + une zone de texte (limite connue, non couverte).
Toutes les données sont fictives, générées pour ce test.
"""
import zipfile, shutil, sys
import docx
from docx.oxml import parse_xml
from docx.oxml.ns import qn, nsdecls
from docx.opc.constants import RELATIONSHIP_TYPE as RT

OUT = "/data/tmp/fixture_build.docx"
FINAL = sys.argv[1] if len(sys.argv) > 1 else "/data/tmp/docx_fixture.docx"

d = docx.Document()

# --- corps normal ---
d.add_paragraph("Ceci est un document de test fictif pour vérifier le pipeline d'anonymisation.")
d.add_paragraph("Le patient se nomme Isabelle FONTAINE, né le 03/11/1980, joignable au 06 11 22 33 44.")

# --- en-tête / pied de page ---
section = d.sections[0]
section.header.paragraphs[0].text = "Confidentiel - suivi par Gregoire VASSEUR"
section.footer.paragraphs[0].text = "Contact urgence : Sylvie MERCIER"

# --- hyperlien (texte affiché + cible séparée) ---
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

# --- suivi des modifications (texte supprimé, reste dans le XML) ---
del_xml = (
    f'<w:p {nsdecls("w")}>'
    f'<w:del w:id="900" w:author="testeur" w:date="2024-01-01T00:00:00Z">'
    f'<w:r><w:delText>Nom supprimé en mode suivi : Frederic LAMBERT, né le 22/02/1990</w:delText></w:r>'
    f'</w:del>'
    f'</w:p>'
)
del_p = parse_xml(del_xml)
d.element.body.append(del_p)

# --- paragraphe normal final (pour repère de fin de corps) ---
last_p = d.add_paragraph("Fin du corps du document de test.")

# --- commentaire ---
run = last_p.add_run(" [ancre commentaire]")
d.add_comment(run, text="Voir dossier de Camille GIRARD pour comparaison", author="Relecteur Test")

# --- métadonnées identifiantes (core properties), via l'API python-docx
# elle-même plutôt qu'une injection XML manuelle -- évite de produire un
# core.xml invalide (élément dupliqué) qui fausserait le test ---
d.core_properties.author = "Nicolas PETIT"
d.core_properties.last_modified_by = "Nicolas PETIT"
d.core_properties.subject = "Dossier confidentiel Julie MOREL"
d.core_properties.comments = "Revu par Nicolas PETIT le 2024-01-01"

d.save(OUT)

# --- footnotes.xml / endnotes.xml : python-docx 1.2.0 n'a pas d'API pour ça,
# injection manuelle au niveau du zip (mêmes reltypes que NOTE_RELTYPES
# dans main.py) ---
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

# référence de note dans le corps (réalisme structurel, pas requis par le
# pipeline de détection mais évite un docx qui semble corrompu si ouvert
# manuellement dans Word)
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
