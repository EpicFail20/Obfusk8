import sys, importlib.util, time, csv, io

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# 300000 cellules PRESQUE VIDES (une seule non-vide par ligne) -> la
# detection saute la quasi-totalite des cellules (rien a analyser), donc
# ne devrait PAS declencher le budget de temps -- isole le cout pur du
# rendu de la page HTML, independant de la detection.
n_cols = 15
n_rows = min(m.MAX_CSV_ROWS - 1, m.MAX_CSV_CELLS // n_cols - 1)
buf = io.StringIO()
w = csv.writer(buf)
w.writerow(["Nom"] + [f"Col{j}" for j in range(n_cols-1)])
for i in range(n_rows):
    w.writerow([f"x{i}"] + [""] * (n_cols - 1))
raw = buf.getvalue().encode("utf-8")
print(f"CSV: {(n_rows+1)*n_cols} cellules (quasi vides), {len(raw)/1024:.0f} Ko", flush=True)

t0 = time.time()
try:
    resp = m._handle_detect_csv(raw, "medical", m.THEMES.get("medical"), "test-empty", "hashempty", "test@example.com", len(raw)/1e6)
    elapsed = time.time() - t0
    body = resp.body if hasattr(resp, "body") else b""
    print(f"Genere en {elapsed:.2f}s", flush=True)
    print(f"Taille de la page HTML: {len(body)/1024/1024:.2f} Mo", flush=True)
    print(f"<td>: {body.count(b'<td')}  <tr>: {body.count(b'<tr')}", flush=True)
except Exception as exc:
    print(f"Exception apres {time.time()-t0:.2f}s: {exc}", flush=True)
