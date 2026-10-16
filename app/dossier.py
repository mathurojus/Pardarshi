"""The paperwork around a report: a stable reference number, how the audit was run, and SHA-256
fingerprints of the report and every screenshot it cites, so a printed copy can be checked later."""
from __future__ import annotations

import hashlib
from collections import Counter

from app.capture.schema import AuditReport
from app.capture.store import AuditStore


def reference(r: AuditReport) -> str:
    return f"PDR-{r.created_at:%Y}-{hashlib.sha256(r.audit_id.encode()).hexdigest()[:6].upper()}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cited_files(r: AuditReport) -> list[str]:
    seen: dict[str, None] = {}
    for f in r.findings:
        for e in f.evidence:
            if e.screenshot:
                seen.setdefault(e.screenshot)
    for w in r.waterfalls:
        for shot in ((w.get("run") or {}).get("first_screenshot"), w.get("screenshot")):
            if shot:
                seen.setdefault(shot)
    return list(seen)


def build(r: AuditReport, store: AuditStore) -> dict:
    root = store.root / r.audit_id
    report_file = root / "report.json"
    files = [{"path": "report.json", "sha256": _sha(report_file.read_bytes()) if report_file.exists() else None}]
    for rel in cited_files(r):  # screenshot paths are relative to the data root and start with the audit id
        p = (store.root / rel).resolve()
        ok = p.is_file() and str(p).startswith(str(root.resolve()))
        files.append({"path": rel.split("/", 1)[-1], "sha256": _sha(p.read_bytes()) if ok else None})
    digest = _sha("\n".join(f"{f['path']}:{f['sha256']}" for f in files).encode())

    runs = store.load_runs(r.audit_id)
    personas = sorted({run.persona.label for run in runs if run.flow == "purchase"})
    started = [run.started_at for run in runs]
    finished = [run.finished_at for run in runs if run.finished_at]
    flows = Counter(run.flow for run in runs)
    repeats = max((run.repeat for run in runs if run.flow == "purchase"), default=-1) + 1
    return {
        "reference": reference(r),
        "fingerprint": digest,
        "files": files,
        "method": {
            "provider": r.provider,
            "personas": personas,
            "repeats": repeats,
            "runs_by_flow": dict(flows),
            "steps_recorded": sum(len(run.steps) for run in runs),
            "screenshots_recorded": sum(1 for run in runs for s in run.steps if s.screenshot),
            "started_at": min(started).isoformat() if started else None,
            "finished_at": max(finished).isoformat() if finished else None,
            "noise_sd_pct": ((r.personalization or {}).get("advertised") or {}).get("noise_pct"),
        },
    }
