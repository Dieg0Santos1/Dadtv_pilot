"""Compare APF3 models on one validation split; then export the winner.

The old APF2 holdout was inspected during development. It is quarantined,
not used for selection or called an independent final test.
"""

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from bot.intent_model import INTENTS, IntentModel
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.svm import LinearSVC

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "training_data.csv"
FINAL_TEST = ROOT / "data" / "apf3_final_test.csv"
MODEL = ROOT / "bot" / "model" / "intent_apf3.json"
REPORT = ROOT / "bot" / "model" / "intent_apf3_report.json"
CLASSES = sorted(INTENTS)


def read_dataset(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or any(not row["text"].strip() or row["label"] not in INTENTS for row in rows):
        raise ValueError(f"Dataset invalido: {path}")
    if set(row["label"] for row in rows) != INTENTS:
        raise ValueError(f"Faltan clases APF3 en {path}")
    return [row["text"].strip() for row in rows], [row["label"] for row in rows]


def candidates():
    def vectorizer(ngram_range):
        return TfidfVectorizer(ngram_range=ngram_range, strip_accents="unicode",
                               sublinear_tf=True, token_pattern=r"(?u)\b\w\w+\b")
    return {
        "baseline_most_frequent": make_pipeline(vectorizer((1, 1)), DummyClassifier(strategy="most_frequent")),
        "logreg_unigrams_c4": make_pipeline(vectorizer((1, 1)), LogisticRegression(max_iter=2000, C=4, class_weight="balanced")),
        "linear_svm_unigrams_c1": make_pipeline(vectorizer((1, 1)), LinearSVC(C=1, class_weight="balanced", max_iter=5000, random_state=42)),
    }


def metrics(expected, predicted):
    return {
        "accuracy": float(accuracy_score(expected, predicted)),
        "macro_f1": float(f1_score(expected, predicted, labels=CLASSES, average="macro", zero_division=0)),
        "classification_report": classification_report(expected, predicted, labels=CLASSES, output_dict=True, zero_division=0),
        "confusion_matrix": confusion_matrix(expected, predicted, labels=CLASSES).tolist(),
    }


def main():
    texts, labels = read_dataset(DATA)
    final_texts, final_labels = read_dataset(FINAL_TEST)
    if set(texts) & set(final_texts):
        raise ValueError("La prueba final contiene frases identicas a las de entrenamiento")
    development_idx, prior_holdout_idx = train_test_split(
        list(range(len(texts))), test_size=0.2, random_state=42, stratify=labels)
    train_idx, validation_idx = train_test_split(
        development_idx, test_size=0.25, random_state=23,
        stratify=[labels[i] for i in development_idx])
    x_train, y_train = [texts[i] for i in train_idx], [labels[i] for i in train_idx]
    x_val, y_val = [texts[i] for i in validation_idx], [labels[i] for i in validation_idx]
    validation = {}
    for name, model in candidates().items():
        model.fit(x_train, y_train)
        validation[name] = metrics(y_val, model.predict(x_val))
    selected = max(validation, key=lambda name: (validation[name]["macro_f1"], validation[name]["accuracy"], name))
    if not selected.startswith("logreg_"):
        raise RuntimeError("El ganador no es compatible con el runtime logistico; implementar su inferencia antes de exportar")

    # The final-test CSV is read above for integrity checks, but is scored
    # only here, after the fixed selection rule has chosen a model.
    model = candidates()[selected]
    # Fit on development data only. The inspected APF2 holdout stays out of
    # both model selection and the exported runtime artifact.
    model.fit([texts[i] for i in development_idx], [labels[i] for i in development_idx])
    final_result = metrics(final_labels, model.predict(final_texts))
    vectorizer, classifier = model.steps[0][1], model.steps[1][1]
    artifact = {
        "format": "dadtv-apf3-tfidf-logreg-v1",
        "dataset_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
        "classes": list(classifier.classes_), "vocabulary": vectorizer.vocabulary_,
        "idf": vectorizer.idf_.tolist(), "coef": classifier.coef_.tolist(),
        "intercept": classifier.intercept_.tolist(),
    }
    exported = IntentModel(artifact)
    for phrase in texts + final_texts:
        expected = model.predict_proba([phrase])[0]
        actual = exported.predict(phrase)
        winner = expected.argmax()
        if actual.intent != classifier.classes_[winner] or abs(actual.confidence - expected[winner]) > 1e-10:
            raise RuntimeError("La inferencia JSON no coincide con scikit-learn")

    report = {
        "protocol": "v3: baseline, regresion logistica y SVM lineal en la misma validacion; prueba controlada reutilizada descriptivamente",
        "training_dataset": {"path": str(DATA.relative_to(ROOT)), "count": len(texts),
                             "sha256": artifact["dataset_sha256"], "class_counts": dict(Counter(labels)),
                             "origin": "Frases manuales; fuente primaria no documentada. No hay evidencia de conversaciones reales."},
        "selection": {"train_count": len(train_idx), "validation_count": len(validation_idx),
                      "prior_holdout_count": len(prior_holdout_idx),
                      "prior_holdout_status": "Inspeccionado antes; no se usa para seleccionar ni como prueba final.",
                      "split": "outer: stratified 20%, random_state=42; inner: stratified 25% de desarrollo, random_state=23",
                      "criterion": "max macro_f1, luego accuracy, luego nombre", "classes_order": CLASSES,
                      "candidates": validation, "selected": selected},
        "final_test": {"path": str(FINAL_TEST.relative_to(ROOT)), "count": len(final_texts),
                       "sha256": hashlib.sha256(FINAL_TEST.read_bytes()).hexdigest(),
                       "class_counts": dict(Counter(final_labels)),
                       "origin": "Frases de prueba controladas redactadas para APF3; no conversaciones reales.",
                       "independence_status": "Ya inspeccionado antes de agregar SVM; resultado descriptivo, no nueva prueba independiente.",
                       "metrics": final_result},
        "export": {"path": str(MODEL.relative_to(ROOT)), "format": artifact["format"],
                   "fit_count": len(development_idx), "configuration": {
                       "vectorizer": {"ngram_range": list(vectorizer.ngram_range), "strip_accents": "unicode",
                                      "sublinear_tf": True, "token_pattern": vectorizer.token_pattern},
                       "classifier": {"type": "LogisticRegression", "C": classifier.C,
                                      "class_weight": "balanced", "max_iter": classifier.max_iter}}},
        "limitations": "La prueba controlada es simulada, pequena y ya fue consultada antes de esta comparacion con SVM; su resultado es descriptivo, no una evaluacion independiente nueva. "
                       "La particion antigua fue inspeccionada previamente y tampoco es independiente. "
                       "Las confianzas no estan calibradas como probabilidades de acierto.",
    }
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    MODEL.write_text(json.dumps(artifact, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Seleccionado: {selected}; validacion macro-F1: {validation[selected]['macro_f1']:.3f}; "
          f"prueba controlada macro-F1: {final_result['macro_f1']:.3f}; accuracy: {final_result['accuracy']:.3f}")


if __name__ == "__main__":
    main()
