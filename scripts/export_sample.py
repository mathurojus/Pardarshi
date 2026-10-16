"""Copy the latest dirty + clean demo audits (report + the evidence screenshots they cite) into ./samples.

So the repo ships real, inspectable output without committing the whole run cache.
  python -m scripts.export_sample                          # latest dirty + clean
  python -m scripts.export_sample dirty-ab12 clean-cd34    # or exactly these audits
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from app.capture.store import AuditStore
from app.config import ROOT

OUT = ROOT / "samples"


def main() -> None:
    store = AuditStore()
    reports = store.list_reports()
    for key, label in (("TicketNest", "dirty"), ("FairTix", "clean")):
        rep = next((r for r in reports if r.site.startswith(key) and r.provider == "opensource"
                    and (not sys.argv[1:] or r.audit_id in sys.argv[1:])), None)
        if not rep:
            print("no audit found for", label)
            continue
        dest = OUT / label
        shutil.rmtree(dest / "shots", ignore_errors=True)  # only the screenshots this report cites
        (dest / "shots").mkdir(parents=True, exist_ok=True)
        shots: set[str] = set()
        for f in rep.findings:
            shots.update(e.screenshot for e in f.evidence if e.screenshot)
        shots.update(w["screenshot"] for w in rep.waterfalls if w.get("screenshot"))
        shots.update(w["run"]["first_screenshot"] for w in rep.waterfalls if (w.get("run") or {}).get("first_screenshot"))
        for s in shots:
            src = store.root / s
            if src.exists():
                shutil.copy(src, dest / "shots" / Path(s).name)
        text = rep.model_dump_json(indent=1)
        for s in shots:  # rewrite paths so the sample is self-contained
            text = text.replace(s, f"shots/{Path(s).name}")
        (dest / "report.json").write_text(text, encoding="utf-8")
        print(f"{label}: {rep.audit_id} score {rep.trust_score}, {len(shots)} screenshots -> {dest}")


if __name__ == "__main__":
    main()
