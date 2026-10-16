"""On-disk cache of runs and reports. Everything an audit saw is replayable offline."""
from __future__ import annotations

import json
from pathlib import Path

from app.capture.schema import AuditReport, RunRecord
from app.config import settings


class AuditStore:
    def __init__(self, root: Path | None = None):
        self.root = root or settings.runs_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def audit_dir(self, audit_id: str) -> Path:
        d = self.root / audit_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def shots_dir(self, audit_id: str) -> Path:
        d = self.audit_dir(audit_id) / "shots"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def save_run(self, run: RunRecord) -> Path:
        p = self.audit_dir(run.audit_id) / f"run_{run.run_id}.json"
        p.write_text(run.model_dump_json(indent=1), encoding="utf-8")
        return p

    def load_runs(self, audit_id: str) -> list[RunRecord]:
        d = self.root / audit_id
        runs = [RunRecord.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(d.glob("run_*.json"))]
        return sorted(runs, key=lambda r: (r.flow, r.persona.id, r.repeat))

    def save_report(self, report: AuditReport) -> Path:
        p = self.audit_dir(report.audit_id) / "report.json"
        p.write_text(report.model_dump_json(indent=1), encoding="utf-8")
        return p

    def load_report(self, audit_id: str) -> AuditReport | None:
        p = self.root / audit_id / "report.json"
        if not p.exists():
            return None
        return AuditReport.model_validate_json(p.read_text(encoding="utf-8"))

    def list_reports(self) -> list[AuditReport]:
        out = []
        for p in self.root.glob("*/report.json"):
            try:
                out.append(AuditReport.model_validate_json(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        return sorted(out, key=lambda r: r.created_at, reverse=True)
