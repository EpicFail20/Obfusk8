import sys, importlib.util, time

spec = importlib.util.spec_from_file_location("m", "/data/tmp/app_copy/main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("MAX_DETECTION_SECONDS:", m.MAX_DETECTION_SECONDS, flush=True)
theme = m.THEMES.get("medical")

# Assez de blocs pour depasser largement le budget si le garde-fou ne
# fonctionnait pas (a ~0.3s/lot de 200 blocs, il en faut ~300+ pour
# depasser 90s -> 60000+ blocs).
n_blocks = 70000
block_texts = [(i, f"NOM{i} Prenom{i} user{i}@example.test 06{i:08d} Paris") for i in range(n_blocks)]

t0 = time.time()
try:
    dets = m._detect_text_blocks(block_texts, theme=theme)
    elapsed = time.time() - t0
    print(f"Termine sans erreur en {elapsed:.1f}s ({len(dets)} detections) -- INATTENDU, le budget aurait du interrompre avant", flush=True)
except Exception as exc:
    elapsed = time.time() - t0
    print(f"Interrompu apres {elapsed:.1f}s: {type(exc).__name__}: {exc}", flush=True)
