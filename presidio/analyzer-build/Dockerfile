# Image Presidio Analyzer avec support du français ajouté
# (l'image publique n'embarque que le modèle anglais par défaut)

FROM ghcr.io/data-privacy-stack/presidio-analyzer:2.2.364

# Télécharge le modèle spaCy français en plus de l'anglais déjà présent
RUN pip install --no-cache-dir spacy && \
    python -m spacy download fr_core_news_md

# Nos fichiers de config (déclarent les langues + le mapping des modèles)
# Chemin confirmé via les variables d'env NLP_CONF_FILE / ANALYZER_CONF_FILE
# du conteneur en cours d'exécution (/app/presidio_analyzer/conf/)
COPY conf/default.yaml /app/presidio_analyzer/conf/default.yaml
COPY conf/default_analyzer.yaml /app/presidio_analyzer/conf/default_analyzer.yaml

# Patch default_recognizers.yaml (déclare fr + ajoute notre recognizer
# d'identifiant patient) via un vrai parseur YAML plutôt que par insertion
# de texte brut — l'indentation "séquence zéro" de ce fichier s'est révélée
# trop fragile pour une édition par sed avec des blocs imbriqués.
COPY patch_recognizers.py /tmp/patch_recognizers.py
RUN pip install --no-cache-dir pyyaml && \
    python3 /tmp/patch_recognizers.py
