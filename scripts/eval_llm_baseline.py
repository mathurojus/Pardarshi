"""Compare the trained classifier with a Gemini zero-shot baseline on the same held-out set.

Uses ONE Gemini request (all held-out strings in a single prompt), so it is cheap on a free-tier key.
Writes the result into the metrics file the dashboard reads.

  python -m scripts.eval_llm_baseline
"""
from __future__ import annotations

import json

from google.genai import types

from app import llm
from app.ml.pattern_clf import METRICS_PATH, evaluate, heldout_rows
from app.ml.taxonomy import CLASSES

DEF = {
    "none": "ordinary UI copy",
    "false_urgency": "countdown / deadline pressure",
    "scarcity": "'only N left' style scarcity",
    "fake_social_proof": "'N people viewing / just bought' claims",
    "confirmshaming": "decline wording that guilt-trips the user",
    "preselected_addon": "label of a pre-ticked paid extra",
    "forced_action": "must register / install / subscribe to continue",
    "subscription_trap": "auto-renewal or hard-to-cancel wording",
    "nagging": "repeated interruptions / retention pop-ups",
}
SCHEMA = types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.OBJECT, properties={
    "i": types.Schema(type=types.Type.INTEGER), "label": types.Schema(type=types.Type.STRING, enum=CLASSES)},
    required=["i", "label"]))


def main() -> None:
    rows = heldout_rows()
    listing = "\n".join(f"{i}. [{r}{', pre-ticked checkbox' if c else ''}] {t}" for i, (t, r, c, _) in enumerate(rows))
    prompt = ("Classify each website UI string into exactly one class.\nClasses:\n" +
              "\n".join(f"- {k}: {v}" for k, v in DEF.items()) + f"\n\nSTRINGS:\n{listing}")
    out = llm.generate_json(prompt, json_schema=SCHEMA, temperature=0.0)
    pred = {d["i"]: d["label"] for d in out}
    labels = [pred.get(i, "none") for i in range(len(rows))]
    res = evaluate(labels, rows)
    p = METRICS_PATH()
    metrics = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    metrics["llm_zero_shot"] = res
    p.write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    print(f"Gemini zero-shot: macro-F1 {res['macro_f1']}, dark-vs-none F1 {res['dark_vs_none']['f1']}")
    if "tfidf_logreg" in metrics:
        print(f"Trained model   : macro-F1 {metrics['tfidf_logreg']['macro_f1']}")


if __name__ == "__main__":
    main()
