#Copyright (C) 2026 CARROLAGGI Xavier
#This program is free software: you can redistribute it and/or modify
#it under the terms of the GNU Affero General Public License as published
#by the Free Software Foundation, either version 3 of the License, or
#(at your option) any later version.

#This program is distributed in the hope that it will be useful,
#but WITHOUT ANY WARRANTY; without even the implied warranty of
#MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#GNU Affero General Public License for more details.

#You should have received a copy of the GNU Affero General Public License
#along with this program. If not, see <https://www.gnu.org/licenses/>.


import logging
import warnings
from typing import List, Optional, Set, Tuple

from presidio_analyzer import (
    AnalysisExplanation,
    LocalRecognizer,
    RecognizerResult,
)

logger = logging.getLogger("presidio-analyzer")


class SpacyRecognizer(LocalRecognizer):
    """
    Recognize PII entities using a spaCy NLP model.

    Since the spaCy pipeline is ran by the AnalyzerEngine/SpacyNlpEngine,
    this recognizer only extracts the entities from the NlpArtifacts
    and returns them.

    --- Patch anonymiseur (voir bloc marqué ci-dessous dans analyze()) ---
    Presidio attribue un score de confiance FIXE (ner_strength, 0.85 par
    défaut) à absolument toutes les détections de ce recognizer, quelle que
    soit leur justesse réelle : spaCy ne fournit pas nativement de
    confiance calibrée par entité. Résultat observé en production : un mot
    administratif isolé sans contexte de phrase ("Compétences", "Exploiter",
    "Analyse"...) reçoit exactement le même score qu'un vrai nom propre
    ("Xavier Carrolaggi") — impossible de les distinguer par le score.

    Le patch ajoute un filtre supplémentaire, INDÉPENDANT du score : pour
    les types PERSON/ORGANIZATION/LOCATION, on exige qu'au moins un token
    de l'empan détecté soit étiqueté comme nom propre (PROPN) par le
    tagger/morphologizer spaCy — déjà actif dans le pipeline chargé, donc
    sans coût d'inférence supplémentaire. Un verbe à l'infinitif ou un nom
    commun isolé n'a structurellement aucune raison d'être un nom propre ;
    un vrai nom (même partiellement composé de particules courantes comme
    "de"/"du") en contient toujours au moins un.
    """

    ENTITIES = ["DATE_TIME", "NRP", "LOCATION", "PERSON", "ORGANIZATION"]

    DEFAULT_EXPLANATION = "Identified as {} by Spacy's Named Entity Recognition"

    # Types pour lesquels le filtre POS (voir docstring de classe) s'applique.
    # DATE_TIME/NRP ne sont pas concernés : leurs faux positifs suivent un
    # mécanisme différent (patterns numériques mal formés, pas des mots de
    # vocabulaire ordinaires pris pour des noms propres).
    POS_FILTERED_ENTITIES = {"PERSON", "ORGANIZATION", "LOCATION"}

    # deprecated, use MODEL_TO_PRESIDIO_MAPPING in NerModelConfiguration instead
    CHECK_LABEL_GROUPS = [
        ({"LOCATION"}, {"GPE", "LOC"}),
        ({"PERSON", "PER"}, {"PERSON", "PER"}),
        ({"DATE_TIME"}, {"DATE", "TIME"}),
        ({"NRP"}, {"NORP"}),
        ({"ORGANIZATION"}, {"ORG"}),
    ]

    def __init__(
        self,
        supported_language: str = "en",
        supported_entities: Optional[List[str]] = None,
        ner_strength: float = 0.85,
        default_explanation: Optional[str] = None,
        check_label_groups: Optional[List[Tuple[Set, Set]]] = None,
        context: Optional[List[str]] = None,
        name: Optional[str] = None,
    ):
        """Initialize the SpaCy recognizer.

        :param supported_language: Language this recognizer supports
        :param supported_entities: The entities this recognizer can detect
        :param ner_strength: Default confidence for NER prediction
        :param check_label_groups: (DEPRECATED) Tuple containing Presidio entity names
        :param default_explanation: Default explanation for the results when using return_decision_process=True
        """  # noqa: E501

        self.ner_strength = ner_strength
        if check_label_groups:
            warnings.warn(
                "check_label_groups is deprecated and isn't used;"
                "entities are mapped in NerModelConfiguration",
                DeprecationWarning,
                2,
            )

        self.default_explanation = (
            default_explanation if default_explanation else self.DEFAULT_EXPLANATION
        )
        supported_entities = supported_entities if supported_entities else self.ENTITIES
        super().__init__(
            supported_entities=supported_entities,
            supported_language=supported_language,
            context=context,
            name=name,
        )

    def load(self) -> None:  # noqa: D102
        # no need to load anything as the analyze method already receives
        # preprocessed nlp artifacts
        pass

    def build_explanation(
        self, original_score: float, explanation: str
    ) -> AnalysisExplanation:
        """
        Create explanation for why this result was detected.

        :param original_score: Score given by this recognizer
        :param explanation: Explanation string
        :return:
        """
        explanation = AnalysisExplanation(
            recognizer=self.name,
            original_score=original_score,
            textual_explanation=explanation,
        )
        return explanation

    def analyze(self, text: str, entities, nlp_artifacts=None):  # noqa: D102
        results = []
        if not nlp_artifacts:
            logger.warning("Skipping SpaCy, nlp artifacts not provided...")
            return results

        ner_entities = nlp_artifacts.entities
        ner_scores = nlp_artifacts.scores

        for ner_entity, ner_score in zip(ner_entities, ner_scores):
            if (
                ner_entity.label_ not in entities
                or ner_entity.label_ not in self.supported_entities
            ):
                logger.debug(
                    f"Skipping entity {ner_entity.label_} "
                    f"as it is not in the supported entities list"
                )
                continue

            # --- Patch anonymiseur : filtre par étiquette grammaticale (POS) ---
            # Voir la docstring de la classe pour le contexte complet. Un
            # empan PERSON/ORGANIZATION/LOCATION sans aucun token PROPN
            # (nom propre) est très probablement un faux positif du type
            # "mot administratif isolé sans contexte de phrase".
            if ner_entity.label_ in self.POS_FILTERED_ENTITIES:
                has_propn = any(
                    getattr(token, "pos_", None) == "PROPN" for token in ner_entity
                )
                if not has_propn:
                    logger.debug(
                        f"Skipping {ner_entity.text!r} ({ner_entity.label_}): "
                        f"aucun token PROPN dans l'empan (filtre POS anonymiseur)"
                    )
                    continue
            # --- fin du patch ---

            textual_explanation = self.DEFAULT_EXPLANATION.format(ner_entity.label_)
            explanation = self.build_explanation(ner_score, textual_explanation)
            spacy_result = RecognizerResult(
                entity_type=ner_entity.label_,
                start=ner_entity.start_char,
                end=ner_entity.end_char,
                score=ner_score,
                analysis_explanation=explanation,
                recognition_metadata={
                    RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                    RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                },
            )
            results.append(spacy_result)

        return results

    @staticmethod
    def __check_label(
        entity: str, label: str, check_label_groups: Tuple[Set, Set]
    ) -> bool:
        raise DeprecationWarning("__check_label is deprecated")
