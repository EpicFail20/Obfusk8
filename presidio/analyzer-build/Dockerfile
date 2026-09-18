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

# Patch spacy_recognizer.py : ajoute un filtre par étiquette grammaticale
# (POS) pour PERSON/ORGANIZATION/LOCATION avant d'accepter une détection.
# Contexte : Presidio attribue un score FIXE (0.85) à toutes les détections
# de ce recognizer, quelle que soit leur justesse — un mot administratif
# isolé sans contexte de phrase ("Compétences", "Exploiter"...) reçoit
# exactement le même score qu'un vrai nom propre, impossible à distinguer
# par seuil. Le patch exige qu'au moins un token de l'empan soit un nom
# propre (PROPN) selon le tagger spaCy déjà actif dans le pipeline chargé —
# zéro coût d'inférence supplémentaire. Voir la docstring du fichier patché
# pour le détail complet. Fichier vérifié fonctionnellement (voir tests
# unitaires du dépôt) mais PAS encore validé sur le vrai modèle fr_core_news_md
# en conditions réelles — à confirmer après déploiement.
COPY patches/spacy_recognizer.py /app/presidio_analyzer/predefined_recognizers/nlp_engine_recognizers/spacy_recognizer.py
