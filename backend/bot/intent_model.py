"""Inference for the APF3 TF-IDF + logistic-regression intent model.

The model is trained offline with scikit-learn and exported as JSON. Runtime
inference uses only the standard library, so the Windows service needs no ML
packages and never deserializes an executable pickle.
"""

import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

INTENTS = frozenset({
    "consulta", "pedido", "reclamo", "fuera_de_tema", "ofensivo", "spam",
})
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "model" / "intent_apf3.json"
TOKEN_PATTERN = re.compile(r"(?u)\b\w\w+\b")


@dataclass(frozen=True)
class IntentPrediction:
    intent: str
    confidence: float
    margin: float
    in_vocabulary: bool


class IntentModel:
    def __init__(self, artifact):
        if artifact.get("format") != "dadtv-apf3-tfidf-logreg-v1":
            raise ValueError("Formato de clasificador APF3 no reconocido")
        self.classes = tuple(artifact["classes"])
        if len(self.classes) != 6 or set(self.classes) != INTENTS:
            raise ValueError("El clasificador debe tener las seis clases APF3")
        self.vocabulary = artifact["vocabulary"]
        self.idf = artifact["idf"]
        self.coef = artifact["coef"]
        self.intercept = artifact["intercept"]
        size = len(self.vocabulary)
        if (
            len(self.idf) != size or len(self.coef) != 6
            or len(self.intercept) != 6
            or any(len(row) != size for row in self.coef)
        ):
            raise ValueError("Dimensiones del clasificador APF3 invalidas")

    @classmethod
    def load(cls, path=DEFAULT_MODEL_PATH):
        with Path(path).open(encoding="utf-8") as handle:
            return cls(json.load(handle))

    @staticmethod
    def terms(text):
        # Equivale a strip_accents='unicode', lowercase y token_pattern de
        # TfidfVectorizer. Solo se entrenan unigramas y bigramas de palabras.
        normalized = unicodedata.normalize("NFKD", text.lower())
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        words = TOKEN_PATTERN.findall(normalized)
        return words + [f"{left} {right}" for left, right in zip(words, words[1:])]

    def predict(self, text):
        counts = Counter(self.terms(text or ""))
        weights = {}
        for term, count in counts.items():
            index = self.vocabulary.get(term)
            if index is not None:
                weights[index] = (1.0 + math.log(count)) * self.idf[index]
        length = math.sqrt(sum(weight * weight for weight in weights.values()))
        if length:
            weights = {index: weight / length for index, weight in weights.items()}
        logits = [
            bias + sum(row[index] * value for index, value in weights.items())
            for bias, row in zip(self.intercept, self.coef)
        ]
        offset = max(logits)
        scores = [math.exp(value - offset) for value in logits]
        total = sum(scores)
        probabilities = [value / total for value in scores]
        ranking = sorted(range(len(probabilities)), key=probabilities.__getitem__, reverse=True)
        winner, second = ranking[:2]
        return IntentPrediction(
            self.classes[winner], probabilities[winner],
            probabilities[winner] - probabilities[second], bool(weights),
        )
