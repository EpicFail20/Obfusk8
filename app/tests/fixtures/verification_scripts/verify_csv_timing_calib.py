import sys, importlib.util, time

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("TEXT_CHUNK_MAX_CHARS:", m.TEXT_CHUNK_MAX_CHARS, "TEXT_CHUNK_MAX_BLOCKS:", m.TEXT_CHUNK_MAX_BLOCKS, flush=True)
print("MAX_CSV_CELLS:", m.MAX_CSV_CELLS, "MAX_CSV_ROWS:", m.MAX_CSV_ROWS, flush=True)

theme = m.THEMES.get("medical")

# Mesure directe du cout d'un lot (chunk) tel que _detect_text_blocks les
# construit : jusqu'a TEXT_CHUNK_MAX_BLOCKS blocs par appel a Presidio.
n_blocks = m.TEXT_CHUNK_MAX_BLOCKS
block_texts = [(i, f"NOM{i} Prenom{i} user{i}@example.test 06{i:08d} Paris") for i in range(n_blocks)]

n_calib_chunks = 5
times = []
for c in range(n_calib_chunks):
    indexed = [(f"{c}:{i}", t) for i, t in block_texts]
    t0 = time.time()
    dets = m._detect_text_blocks(indexed, theme=theme)
    elapsed = time.time() - t0
    times.append(elapsed)
    print(f"chunk {c}: {n_blocks} blocs -> {elapsed:.3f}s ({len(dets)} detections)", flush=True)

avg = sum(times) / len(times)
n_total_cells = m.MAX_CSV_CELLS
n_cols = 15
n_total_blocks_estimate = n_total_cells  # une cellule = un bloc pour _detect_text_blocks
n_chunks_estimate = n_total_blocks_estimate / n_blocks
print(f"\nTemps moyen par lot de {n_blocks} blocs: {avg:.3f}s", flush=True)
print(f"Pour {n_total_cells} cellules (limite MAX_CSV_CELLS) -> ~{n_chunks_estimate:.0f} lots -> ~{avg*n_chunks_estimate:.1f}s estimees", flush=True)
