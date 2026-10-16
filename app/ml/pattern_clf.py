"""Dark-pattern text classifier: TF-IDF (word + char n-grams) + logistic regression.

Chosen on purpose: it trains in seconds on a laptop CPU (no GPU here), is explainable
(top n-grams per class) and is fast enough to score every segment of every captured page.
Context from the DOM is injected as tokens: the element role and whether the text sits
next to a pre-ticked checkbox.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.pipeline import FeatureUnion, Pipeline

from app.capture.schema import Segment
from app.config import settings
from app.ml import augment
from app.ml.seed_data import HELDOUT, SEED
from app.ml.taxonomy import CLASSES

MIN_WORDS = 3
MODEL_PATH = lambda: settings.models_dir / "pattern_clf.joblib"  # noqa: E731
METRICS_PATH = lambda: settings.models_dir / "metrics.json"  # noqa: E731


def featurize(text: str, role: str = "text", checked: bool = False) -> str:
    # The element role (link/heading/label) is deliberately NOT a feature: in an earlier version it became a
    # shortcut ("every link is confirm-shaming", "every heading is nagging") and flagged site navigation.
    return f"{'zchecked ' if checked else ''}{text.lower()}"


def _rows_to_xy(rows: list[tuple[str, str, bool, str]]):
    return [featurize(t, r, c) for t, r, c, _ in rows], [lab for *_, lab in rows]


def load_training_rows(include_generated: bool = True, include_templates: bool = True) -> list[tuple[str, str, bool, str]]:
    rows = [(t, r, c, lab) for lab, items in SEED.items() for t, r, c in items]
    if include_templates:
        rows += augment.generate()
    gen = settings.training_dir / "generated.jsonl"
    if include_generated and gen.exists():
        for line in gen.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                if d["label"] in CLASSES:
                    rows.append((d["text"], d["role"], bool(d["checked"]), d["label"]))
    return rows


def heldout_rows() -> list[tuple[str, str, bool, str]]:
    return [(t, r, c, lab) for lab, items in HELDOUT.items() for t, r, c in items]


def build_pipeline(C: float = 8.0) -> Pipeline:
    feats = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)),
    ])
    return Pipeline([("f", feats), ("lr", LogisticRegression(C=C, max_iter=3000, class_weight="balanced"))])


class PatternClassifier:
    def __init__(self, pipe: Pipeline):
        self.pipe = pipe
        self.classes = list(pipe.named_steps["lr"].classes_)

    @classmethod
    def train(cls, rows=None, C: float = 8.0) -> "PatternClassifier":
        X, y = _rows_to_xy(rows or load_training_rows())
        return cls(build_pipeline(C).fit(X, y))

    def predict_proba(self, segs: list[Segment]) -> np.ndarray:
        if not segs:
            return np.zeros((0, len(self.classes)))
        return self.pipe.predict_proba([featurize(s.text, s.role, s.near_checked) for s in segs])

    def predict(self, segs: list[Segment], threshold: float = 0.5) -> list[tuple[str, float]]:
        """Label per segment; anything below the threshold falls back to 'none'."""
        out = []
        for seg, row in zip(segs, self.predict_proba(segs)):
            i = int(row.argmax())
            label, p = str(self.classes[i]), float(row[i])
            # A manipulation needs a phrase: 1-2 word strings ("Events", "CVV", "Account") are site chrome.
            if len(seg.text.split()) < MIN_WORDS:
                label, p = "none", 1.0
            out.append((label, p) if p >= threshold else ("none", 1.0 - p))
        return out

    def top_terms(self, cls: str, k: int = 8) -> list[str]:
        feats = self.pipe.named_steps["f"].get_feature_names_out()
        coef = self.pipe.named_steps["lr"].coef_[self.classes.index(cls)]
        return [feats[i].split("__", 1)[-1] for i in np.argsort(coef)[::-1][:k]]

    def save(self, path: Path | None = None) -> Path:
        p = path or MODEL_PATH()
        p.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.pipe, p)
        return p

    @classmethod
    def load(cls, path: Path | None = None) -> "PatternClassifier":
        return cls(joblib.load(path or MODEL_PATH()))


def get_classifier() -> PatternClassifier:
    """Load the saved model, or train one on the spot (seconds) if none exists yet."""
    if MODEL_PATH().exists():
        return PatternClassifier.load()
    clf = PatternClassifier.train()
    clf.save()
    return clf


def evaluate(pred_labels: list[str], rows=None) -> dict:
    rows = rows or heldout_rows()
    y = [lab for *_, lab in rows]
    labels = [c for c in CLASSES]
    rep = classification_report(y, pred_labels, labels=labels, output_dict=True, zero_division=0)
    yb = ["none" if v == "none" else "dark" for v in y]
    pb = ["none" if v == "none" else "dark" for v in pred_labels]
    rep_b = classification_report(yb, pb, labels=["none", "dark"], output_dict=True, zero_division=0)
    return {
        "n": len(y),
        "macro_f1": round(f1_score(y, pred_labels, labels=labels, average="macro", zero_division=0), 4),
        "accuracy": round(float(np.mean(np.array(y) == np.array(pred_labels))), 4),
        "dark_vs_none": {"precision": round(rep_b["dark"]["precision"], 4), "recall": round(rep_b["dark"]["recall"], 4),
                         "f1": round(rep_b["dark"]["f1-score"], 4)},
        "per_class": {c: {k: round(rep[c][k], 3) for k in ("precision", "recall", "f1-score", "support")} for c in labels},
        "confusion": {"labels": labels, "matrix": confusion_matrix(y, pred_labels, labels=labels).tolist()},
    }


def main() -> None:
    train_rows = load_training_rows()
    clf = PatternClassifier.train(train_rows)
    rows = heldout_rows()
    pred = [lab for lab, _ in clf.predict([Segment(text=t, role=r, near_checked=c) for t, r, c, _ in rows])]
    metrics = {"train_rows": len(train_rows), "tfidf_logreg": evaluate(pred, rows)}
    # Stricter: drop held-out strings that (ignoring digits) also appear in the training data.
    norm = lambda t: re.sub(r"\d+", "#", t.lower().strip())  # noqa: E731
    seen = {norm(t) for t, *_ in train_rows}
    clean = [r for r in rows if norm(r[0]) not in seen]
    pred_c = [lab for lab, _ in clf.predict([Segment(text=t, role=r, near_checked=c) for t, r, c, _ in clean])]
    metrics["tfidf_logreg_dedup"] = evaluate(pred_c, clean)
    clf.save()
    METRICS_PATH().write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    m = metrics["tfidf_logreg"]
    print(f"trained on {len(train_rows)} rows; held-out n={m['n']}")
    print(f"macro-F1 {m['macro_f1']}  accuracy {m['accuracy']}  dark-vs-none F1 {m['dark_vs_none']['f1']}")
    d = metrics["tfidf_logreg_dedup"]
    print(f"de-duplicated held-out n={d['n']}: macro-F1 {d['macro_f1']}  dark-vs-none P={d['dark_vs_none']['precision']} R={d['dark_vs_none']['recall']}")
    for c, v in m["per_class"].items():
        print(f"  {c:18s} P={v['precision']:.2f} R={v['recall']:.2f} F1={v['f1-score']:.2f} (n={int(v['support'])})")


if __name__ == "__main__":
    main()
