"""Re-run the analysis for stored audits from their saved runs (no browsing, no Gemini).

Use after the analysis changes. Policy findings (which needed Gemini) are carried over from the old report.
  python -m scripts.reanalyse            # every stored audit
  python -m scripts.reanalyse dirty-ab12  # just one
"""
from __future__ import annotations

import sys

from app.analysis import analyze, rescore
from app.capture.store import AuditStore
from app.ml.pattern_clf import get_classifier


def main(ids: list[str]) -> None:
    store, clf = AuditStore(), get_classifier()
    for old in store.list_reports():
        if ids and old.audit_id not in ids:
            continue
        runs = store.load_runs(old.audit_id)
        if not runs:
            print(f"{old.audit_id}: no stored runs, skipped")
            continue
        new = analyze(old.audit_id, old.site, old.provider, runs, clf)
        new.created_at = old.created_at
        new.findings += [f for f in old.findings if f.type == "policy"]
        new.notes = old.notes
        rescore(new)
        store.save_report(new)
        print(f"{old.audit_id}: {old.trust_score} -> {new.trust_score}")


if __name__ == "__main__":
    main(sys.argv[1:])
