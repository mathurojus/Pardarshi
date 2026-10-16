"""End-to-end evaluation against the demo shop's KNOWN ground truth.

Runs full audits (real browser personas) on the dirty and clean shop, then scores:
  * pattern classes   : did we find exactly the injected dark-pattern classes?
  * drip pricing      : flagged on dirty, not on clean
  * personalization   : flagged exactly the factors the shop biases (iphone, DE, returning)
  * cancellation friction : flagged on dirty, not on clean

Usage (shop must be running):  python -m eval.evaluate [--repeats 3]
"""
from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from app.capture.schema import AuditReport
from app.config import settings
from app.pipeline import run_audit

PERSONA_CASES = [("device", "iphone"), ("device", "android"), ("user_state", "returning"), ("country", "DE"), ("country", "JP")]


def predicted_classes(rep: AuditReport) -> set[str]:
    out = set()
    for f in rep.findings:
        if f.type.startswith("pattern:"):
            out.add(f.type.split(":", 1)[1])
        elif f.type == "basket_sneaking":
            out.add("preselected_addon")
        elif f.type == "friction":
            out.add("subscription_trap")
    return out


def predicted_personalization(rep: AuditReport) -> set[tuple[str, str]]:
    f = next((x for x in rep.findings if x.type == "personalization"), None)
    return {(e["factor"], e["level"]) for e in (f.metrics["effects"] if f else [])}


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 3), "recall": round(r, 3),
            "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


async def main(repeats: int) -> dict:
    tallies = {k: [0, 0, 0] for k in ("pattern_classes", "drip_pricing", "personalization", "friction")}
    sites = {}
    async with httpx.AsyncClient() as http:
        for site in ("dirty", "clean"):
            truth = (await http.get(f"{settings.demo_shop_url}/__truth/{site}")).json()
            rep = await run_audit(site, repeats=repeats)
            sites[site] = {"audit_id": rep.audit_id, "trust_score": rep.trust_score, "runs_ok": f"{rep.runs_ok}/{rep.runs_total}"}

            t_cls = {p["category"] for p in truth["patterns"]}
            p_cls = predicted_classes(rep)
            tallies["pattern_classes"][0] += len(t_cls & p_cls)
            tallies["pattern_classes"][1] += len(p_cls - t_cls)
            tallies["pattern_classes"][2] += len(t_cls - p_cls)
            sites[site]["classes_missed"] = sorted(t_cls - p_cls)
            sites[site]["classes_extra"] = sorted(p_cls - t_cls)

            for key, fid in (("drip_pricing", "drip_pricing"), ("friction", "friction")):
                want, got = key in truth["computed"], any(f.type == fid for f in rep.findings)
                tallies[key][0] += want and got
                tallies[key][1] += (not want) and got
                tallies[key][2] += want and (not got)

            bias = truth["bias"]
            t_p = set()
            if bias["device"].get("iphone"):
                t_p.add(("device", "iphone"))
            if bias["returning"]:
                t_p.add(("user_state", "returning"))
            if bias["country"].get("DE"):
                t_p.add(("country", "DE"))
            p_p = predicted_personalization(rep)
            tallies["personalization"][0] += len(t_p & p_p)
            tallies["personalization"][1] += len(p_p - t_p)
            tallies["personalization"][2] += len(t_p - p_p)
            sites[site]["personalization_flagged"] = sorted(map(list, p_p))

    results = {k: prf(*v) for k, v in tallies.items()}
    allv = [sum(v[i] for v in tallies.values()) for i in range(3)]
    results["overall"] = prf(*allv)
    out = {"repeats": repeats, "provider": settings.provider, "sites": sites, "scores": results}
    path = settings.data_dir / "eval_results.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    asyncio.run(main(ap.parse_args().repeats))
