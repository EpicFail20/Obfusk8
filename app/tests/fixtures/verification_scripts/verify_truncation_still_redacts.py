import sys, importlib.util, csv, io

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# 2500 lignes, une seule colonne avec un nom different par ligne -> verifie
# qu'une ligne AU-DELA du seuil d'affichage (2000) est quand meme caviardee
# a la finalisation, meme si jamais visible dans l'apercu.
buf = io.StringIO()
w = csv.writer(buf)
w.writerow(["Nom"])
for i in range(2500):
    w.writerow([f"Dupont{i:04d} Test"])
raw = buf.getvalue().encode("utf-8")

resp = m._handle_detect_csv(raw, "medical", m.THEMES.get("medical"), "test-trunc", "hashtrunc", "test@example.com", len(raw)/1e6)
job = m.PENDING_JOBS["test-trunc"]
print(f"{len(job['detections'])} detections au total (sur 2500 lignes)")

# La ligne 2400 (index 2399, bien au-dela du cap d'affichage de 2000) a-t-elle une detection ?
row_2400_detected = any(d["block_id"] == "2399:0" for d in job["detections"])
print("Ligne 2400 (au-dela de l'apercu) a une detection:", row_2400_detected)

summary, output_path, _ = m._finalize_csv_job(job, "test-trunc", set())
out_text = open(output_path, encoding="utf-8-sig").read()
lines = out_text.splitlines()
print("Ligne 2400 dans le fichier final:", repr(lines[2400]))
print("Contient encore 'Dupont2399' en clair:", "Dupont2399" in out_text)
