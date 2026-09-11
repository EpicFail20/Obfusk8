import fitz

# Construit une image de test : moitié gauche rouge (zone à "caviarder"),
# moitié droite bleue (zone à laisser intacte) - permet de vérifier après
# coup si le rouge survit ailleurs dans le fichier.
W, H = 200, 100
pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, W, H), False)
for y in range(H):
    for x in range(W):
        if x < W // 2:
            pix.set_pixel(x, y, (255, 0, 0))   # rouge = "PII" à caviarder
        else:
            pix.set_pixel(x, y, (0, 0, 255))   # bleu = contenu à conserver
img_bytes = pix.tobytes("png")

doc = fitz.open()
page = doc.new_page(width=300, height=200)
img_rect = fitz.Rect(50, 50, 250, 150)
page.insert_image(img_rect, stream=img_bytes)

# Même mécanisme que _apply_manual_redactions() : une zone tracée par
# l'utilisateur ne couvrant QUE la moitié gauche (rouge) de l'image.
redact_rect = fitz.Rect(50, 50, 150, 150)
page.add_redact_annot(redact_rect, fill=(0, 0, 0))
page.apply_redactions()  # défauts réels du code : images=2, graphics=1, text=0

out_path = "/tmp/probe_out.pdf"
doc.save(out_path, garbage=4, clean=True, deflate=True)
doc.close()

print("=== Après caviardage (garbage=4, clean=True) ===")
doc2 = fitz.open(out_path)
page2 = doc2[0]

# 1) Rendu visuel : la zone rouge redactée doit apparaître noire.
pm = page2.get_pixmap()
left_px = pm.pixel(75, 100)   # dans redact_rect (gauche, rouge d'origine)
right_px = pm.pixel(200, 100)  # hors redact_rect (droite, bleu conservé)
print("Pixel rendu dans la zone caviardée (attendu noir):", left_px)
print("Pixel rendu hors zone (attendu bleu conservé):", right_px)

# 2) Inspection de TOUS les objets image du fichier de sortie (pas
# seulement ceux référencés par la page) - le rouge d'origine survit-il
# ailleurs, comme un objet orphelin de contenu texte pré-caviardage ?
print("\n=== Balayage de tous les objets image du fichier ===")
found_red_anywhere = False
n = doc2.xref_length()
for xref in range(1, n):
    if not doc2.xref_is_image(xref):
        continue
    try:
        info = doc2.extract_image(xref)
    except Exception as exc:
        print(f"  xref {xref}: extraction échouée ({exc})")
        continue
    raw = info["image"]
    subpix = fitz.Pixmap(raw)
    if subpix.n >= 3:
        # cherche un pixel rouge pur quelque part dans cette image
        has_red = False
        for yy in range(0, subpix.height, max(1, subpix.height // 10)):
            for xx in range(0, subpix.width, max(1, subpix.width // 10)):
                px = subpix.pixel(xx, yy)
                if px[0] > 200 and px[1] < 50 and px[2] < 50:
                    has_red = True
                    break
            if has_red:
                break
        print(f"  xref {xref}: taille={subpix.width}x{subpix.height}, contient du rouge={has_red}")
        if has_red:
            found_red_anywhere = True

print(f"\nRÉSULTAT : rouge original retrouvé quelque part dans le fichier = {found_red_anywhere}")
print("objets atteignables depuis l'arbre de pages vs total xref:", len(list(doc2.pages())), "page(s),", n, "objets xref")
doc2.close()
