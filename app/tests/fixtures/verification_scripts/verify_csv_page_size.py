import sys, importlib.util, time, csv, io

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

n_cols = 15
n_rows = 1333  # ~20000 cellules, reste large sous le budget de temps (90s)
buf = io.StringIO()
w = csv.writer(buf)
w.writerow(["Nom", "Prenom", "Email", "Telephone", "Ville"] + [f"Col{j}" for j in range(n_cols-5)])
for i in range(n_rows):
    w.writerow([f"NOM{i}", f"Prenom{i}", f"user{i}@example.test", f"06{i:08d}", "Paris"] + [f"val{i}_{j}" for j in range(n_cols-5)])
raw = buf.getvalue().encode("utf-8")
print(f"CSV: {(n_rows+1)*n_cols} cellules, {len(raw)/1024:.0f} Ko", flush=True)

t0 = time.time()
resp = m._handle_detect_csv(raw, "medical", m.THEMES.get("medical"), "test-page-size", "hashpage", "test@example.com", len(raw)/1e6)
elapsed = time.time() - t0
body = resp.body if hasattr(resp, "body") else b""
print(f"Genere en {elapsed:.2f}s", flush=True)
print(f"Taille de la page HTML: {len(body)/1024/1024:.2f} Mo", flush=True)
print(f"<td>: {body.count(b'<td')}  <tr>: {body.count(b'<tr')}", flush=True)
