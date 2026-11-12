"""Train and evaluate the six-class APF3 model from the APF2 labeled CSV.

Training requires scikit-learn (requirements-legacy.txt). The production bot
loads only the exported JSON and does not need scikit-learn or joblib.
"""

import csv
import hashlib
import json
from pathlib import Path

from bot.intent_model import INTENTS, IntentModel
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import make_pipeline

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "training_data.csv"
MODEL = ROOT / "bot" / "model" / "intent_apf3.json"
REPORT = ROOT / "bot" / "model" / "intent_apf3_report.json"


def pipeline():
    return make_pipeline(
        TfidfVectorizer(
            ngram_range=(1, 2), strip_accents="unicode", sublinear_tf=True,
            token_pattern=r"(?u)\b\w\w+\b",
        ),
        LogisticRegression(max_iter=2000, C=4, class_weight="balanced"),
    )


def main():
    with DATA.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    texts = [row["text"].strip() for row in rows]
    labels = [row["label"].strip() for row in rows]
    if not texts or any(not text for text in texts) or set(labels) != INTENTS:
        raise ValueError("El CSV debe contener textos y las seis clases APF3")

    train_indices, test_indices = train_test_split(
        list(range(len(texts))), test_size=0.2, random_state=42, stratify=labels,
    )
    train_texts = [texts[index] for index in train_indices]
    train_labels = [labels[index] for index in train_indices]
    test_texts = [texts[index] for index in test_indices]
    test_labels = [labels[index] for index in test_indices]
    model = pipeline()
    folds = StratifiedKFold(n_splits=4, shuffle=True, random_state=7)
    cv_scores = cross_val_score(model, train_texts, train_labels, cv=folds, scoring="accuracy")
    model.fit(train_texts, train_labels)
    predicted = model.predict(test_texts)
    classes = list(model[-1].classes_)
    report = {
        "dataset": "APF2 training_data.csv; etiquetas manuales, 180 ejemplos",
        "dataset_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
        "train_count": len(train_indices),
        "holdout_count": len(test_indices),
        "holdout_accuracy": float(accuracy_score(test_labels, predicted)),
        "cv_accuracy_mean_training_only": float(cv_scores.mean()),
        "cv_accuracy_std_training_only": float(cv_scores.std()),
        "classes": classes,
        "classification_report": classification_report(
            test_labels, predicted, labels=classes, output_dict=True, zero_division=0,
        ),
        "confusion_matrix": confusion_matrix(test_labels, predicted, labels=classes).tolist(),
        "limitations": "Conjunto pequeno y manual; las probabilidades no estan calibradas. "
        "Se compararon configuraciones durante el desarrollo con una particion de prueba, "
        "por lo que el 72.2% es descriptivo y puede ser optimista. "
        "El umbral y margen de confianza obligan a pedir aclaracion ante dudas.",
    }

    # Reentrenar con todos los ejemplos tras la evaluacion. Hubo comparaciones
    # exploratorias con la particion de prueba: no llamarla estimacion imparcial.
    model.fit(texts, labels)
    vectorizer, classifier = model.steps[0][1], model.steps[1][1]
    artifact = {
        "format": "dadtv-apf3-tfidf-logreg-v1",
        "dataset_sha256": report["dataset_sha256"],
        "classes": classes,
        "vocabulary": vectorizer.vocabulary_,
        "idf": vectorizer.idf_.tolist(),
        "coef": classifier.coef_.tolist(),
        "intercept": classifier.intercept_.tolist(),
    }
    exported = IntentModel(artifact)
    for text in texts + ["ya pague", "quiero hbo", "mi cuenta no funciona", "???"]:
        expected = model.predict_proba([text])[0]
        actual = exported.predict(text)
        winner = expected.argmax()
        if actual.intent != classes[winner] or abs(actual.confidence - expected[winner]) > 1e-10:
            raise RuntimeError("La inferencia JSON no coincide con el modelo entrenado")

    MODEL.parent.mkdir(parents=True, exist_ok=True)
    MODEL.write_text(json.dumps(artifact, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Entrenado: {len(texts)} ejemplos; holdout: {report['holdout_accuracy']:.3f}; "
          f"CV entrenamiento: {report['cv_accuracy_mean_training_only']:.3f}")
    print(f"Modelo: {MODEL}; informe: {REPORT}")


if __name__ == "__main__":
    main()
