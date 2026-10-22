"""Turn captured runs into an AuditReport: findings, waterfalls, personalization, friction, score."""
from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict

from app.capture.schema import AuditReport, Evidence, Finding, RunRecord, Step
from app.ml import personalization, taxonomy
from app.ml.pattern_clf import PatternClassifier
from app.ml.price_extract import drip_ratio

PATTERN_MIN_PROB = 0.6


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _norm(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.lower())).strip()


def _step(run: RunRecord, name: str) -> Step | None:
    return next((s for s in run.steps if s.name == name), None)


def _first_price_step(run: RunRecord) -> Step | None:
    """Where the advertised price was read: the scripted 'event' step, or the step the agent recorded."""
    named = _step(run, "event")
    if named:
        return named
    idx = run.meta.get("advertised_step")
    hit = next((s for s in run.steps if s.index == idx), None) if idx is not None else None
    return hit or (run.steps[0] if run.steps else None)


def product_of(run: RunRecord) -> dict | None:
    """The product a purchase run priced: its name and page, read where the advertised price was read."""
    s = _first_price_step(run)
    # Not the page <title>: on many sites that is the shop's name. No heading means: identify it by its page.
    name = (run.meta.get("product_name") or (s.heading if s else "")).strip()
    url = ((s.url if s else "") or "").split("#", 1)[0]
    if not name and not url:
        return None
    key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip() or url
    return {"name": name[:160], "url": url, "key": key}


def same_product(purchase_runs: list[RunRecord]) -> tuple[list[RunRecord], list[dict], str | None]:
    """Keep price comparisons to ONE product. Returns (runs to compare, product summary, note or None).

    Runs that priced a different product than most runs did are left out of drip and personalization maths,
    because a price difference between two different products says nothing about the shopper.
    """
    groups: dict[str, list[RunRecord]] = defaultdict(list)
    info: dict[str, dict] = {}
    for r in purchase_runs:
        if not r.ok:
            continue
        p = product_of(r)
        k = p["key"] if p else "?"
        groups[k].append(r)
        info.setdefault(k, p or {"name": "Unknown product", "url": "", "key": "?"})
    if not groups:
        return purchase_runs, [], None
    main = max(groups, key=lambda k: (len(groups[k]), k != "?"))
    summary = [{"name": info[k]["name"], "url": info[k]["url"], "runs": len(rs),
                "personas": sorted({r.persona.label for r in rs}), "used": k == main}
               for k, rs in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    if len(groups) == 1:
        return purchase_runs, summary, None
    dropped = sum(len(rs) for k, rs in groups.items() if k != main)
    keep = [r for r in purchase_runs if not r.ok or (product_of(r) or {"key": "?"})["key"] == main]
    note = (f"{dropped} run(s) priced a different product than '{info[main]['name']}' and were left out of the "
            "price comparisons (drip pricing, personalised pricing, waterfalls).")
    return keep, summary, note


MIN_RUNS_PER_PRODUCT = 3


def product_groups(purchase_runs: list[RunRecord]) -> dict[str, tuple[dict, list[RunRecord]]]:
    """Measured runs grouped by the product they priced, only products with enough runs to compare shoppers."""
    groups: dict[str, list[RunRecord]] = defaultdict(list)
    info: dict[str, dict] = {}
    for r in purchase_runs:
        p = product_of(r) if r.ok else None
        if p:
            groups[p["key"]].append(r)
            info.setdefault(p["key"], p)
    return {k: (info[k], rs) for k, rs in sorted(groups.items(), key=lambda kv: -len(kv[1]))
            if len(rs) >= MIN_RUNS_PER_PRODUCT}


def _merge(pairs: list[tuple[str, Finding]], total: int) -> Finding | None:
    """One finding per kind across products: the worst product leads, the others are named, the score counts it once."""
    if not pairs:
        return None
    pairs = sorted(pairs, key=lambda t: -t[1].penalty)
    name, f = pairs[0]
    f.title += f" (on {len(pairs)} of {total} products)"
    f.detail += f" Product: {name}." + (f" Also on: {', '.join(n for n, _ in pairs[1:])}." if len(pairs) > 1 else "")
    f.evidence = f.evidence + [e for _, g in pairs[1:3] for e in g.evidence[:1]]
    f.metrics = {**f.metrics, "products": [{"name": n, **{k: v for k, v in g.metrics.items() if k != "effects"}} for n, g in pairs]}
    return f


def _ev(run: RunRecord, step: Step | None, text: str = "") -> Evidence:
    return Evidence(run_id=run.run_id, step_name=step.name if step else "", text=text,
                    screenshot=step.screenshot if step else None)


# ---------------------------------------------------------------------------------------------
def waterfalls(purchase_runs: list[RunRecord]) -> list[dict]:
    """Per persona: the run with the median final price, shown as a price waterfall."""
    by_persona: dict[str, list[RunRecord]] = defaultdict(list)
    for r in purchase_runs:
        if r.ok and r.final_price:
            by_persona[r.persona.id].append(r)
    out = []
    for pid, rs in by_persona.items():
        rs = sorted(rs, key=lambda r: r.final_price)
        rep = rs[len(rs) // 2]
        pay = rep.price_step()
        first = _first_price_step(rep)
        items = [i for i in pay.prices if i.kind != "total" and i.kind != "other"]
        out.append({
            "persona_id": pid, "persona": rep.persona.label, "n": len(rs), "currency": rep.currency,
            "advertised": _median([r.advertised_price for r in rs]), "final": _median([r.final_price for r in rs]),
            "drip_pct": round((drip_ratio(_median([r.advertised_price for r in rs]), _median([r.final_price for r in rs])) or 0) * 100, 1),
            "items": [{"label": i.label, "amount": i.amount, "kind": i.kind, "preselected": i.preselected} for i in items],
            "screenshot": pay.screenshot,
            # the one real run behind the screenshots and line items, so an exhibit never mixes runs
            "run": {"run_id": rep.run_id, "advertised": rep.advertised_price, "final": rep.final_price,
                    "first_screenshot": first.screenshot, "stopped_at": rep.stopped_at or "payment"},
        })
    return sorted(out, key=lambda w: w["persona_id"] != "base")


# ---------------------------------------------------------------------------------------------
def drip_finding(purchase_runs: list[RunRecord]) -> Finding | None:
    ratios = [(drip_ratio(r.advertised_price, r.final_price), r) for r in purchase_runs if r.ok]
    ratios = [(x, r) for x, r in ratios if x is not None]
    if not ratios:
        return None
    med = statistics.median(x for x, _ in ratios)
    if med < 0.05:
        return None
    rep = min(ratios, key=lambda t: abs(t[0] - med))[1]
    pay = rep.price_step()
    fees = [f"{i.label} {int(i.amount)}" for i in pay.prices if i.kind in ("fee", "addon", "tax")]
    sev = "high" if med >= 0.25 else "medium" if med >= 0.10 else "low"
    return Finding(
        id="drip_pricing", type="drip_pricing", severity=sev, ccpa=taxonomy.COMPUTED_INFO["drip_pricing"][1],
        title=f"Drip pricing: the price grows {med * 100:.0f}% between the first screen and "
              + ("payment" if all(r.reached_end for _, r in ratios) else "the checkout summary"),
        detail=(f"Advertised {rep.advertised_price:.0f} {rep.currency}; payable {rep.final_price:.0f} {rep.currency}. "
                f"Added during checkout: {', '.join(fees) or 'unitemised charges'}. "
                f"Median over {len(ratios)} runs."),
        penalty=min(25.0, med * 100 * 0.9),
        evidence=[_ev(rep, _first_price_step(rep), f"Advertised {rep.advertised_price:.0f}"),
                  _ev(rep, pay, f"Payable {rep.final_price:.0f}")],
        metrics={"median_drip_pct": round(med * 100, 1), "runs": len(ratios)},
    )


def basket_finding(purchase_runs: list[RunRecord]) -> Finding | None:
    hits = []
    for r in purchase_runs:
        if not r.ok:
            continue
        for s in r.steps:
            for i in s.prices:
                if i.preselected and i.kind == "addon" and i.amount > 0:
                    hits.append((r, s, i))
                    break
    if not hits:
        return None
    r, s, i = hits[0]
    return Finding(
        id="basket_sneaking", type="basket_sneaking", severity="high",
        ccpa=taxonomy.COMPUTED_INFO["basket_sneaking"][1],
        title=f"Basket sneaking: '{i.label}' ({i.amount:.0f}) is added to the order by default",
        detail=(f"A pre-ticked checkbox adds a paid extra to the total in {len(hits)} of "
                f"{sum(1 for x in purchase_runs if x.ok)} runs. The shopper never asked for it."),
        penalty=10.0, evidence=[_ev(r, s, i.label)], metrics={"amount": i.amount, "runs": len(hits)},
    )


# ---------------------------------------------------------------------------------------------
def pattern_findings(runs: list[RunRecord], clf: PatternClassifier, skip: set[str]) -> list[Finding]:
    ok = [r for r in runs if r.ok]
    if not ok:
        return []
    flows: dict[str, int] = defaultdict(int)
    for r in ok:
        flows[r.flow] += 1
    # class -> norm text -> {runs:set, best_p, ev}
    agg: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in ok:
        for s in r.steps:
            if not s.segments:
                continue
            for seg, (label, p) in zip(s.segments, clf.predict(s.segments, PATTERN_MIN_PROB)):
                if label == "none":
                    continue
                d = agg[label].setdefault(_norm(seg.text), {"runs": set(), "p": 0.0, "ev": None, "text": seg.text, "flow": r.flow})
                d["runs"].add(r.run_id)
                if p > d["p"]:
                    d["p"], d["ev"] = p, _ev(r, s, seg.text)
    out = []
    for cls, texts in agg.items():
        if cls in skip:
            continue
        kept = []
        for d in texts.values():
            n_flow = flows[d["flow"]]
            need = 1 if n_flow <= 2 else max(2, math.ceil(0.3 * n_flow))
            if len(d["runs"]) >= need:
                kept.append(d)
        if not kept:
            continue
        kept.sort(key=lambda d: (-len(d["runs"]), -d["p"]))
        title, ccpa, penalty = taxonomy.INFO[cls]
        out.append(Finding(
            id=f"pattern:{cls}", type=f"pattern:{cls}", title=title, ccpa=ccpa, penalty=float(penalty),
            severity="high" if penalty >= 8 else "medium",
            detail="Seen: " + "; ".join(f"“{d['text']}”" for d in kept[:3]),
            evidence=[d["ev"] for d in kept[:3] if d["ev"]],
            metrics={"distinct_texts": len(kept), "avg_confidence": round(statistics.mean(d["p"] for d in kept), 3)},
        ))
    return out


def urgency_signal_finding(runs: list[RunRecord]) -> Finding | None:
    """A countdown that restarts on reload is an invented deadline: strongest evidence of false urgency."""
    for r in runs:
        for s in r.steps:
            reset = next((g for g in s.signals if g.kind == "timer_resets"), None)
            if reset:
                title, ccpa, penalty = taxonomy.INFO["false_urgency"]
                n = sum(1 for x in runs for st in x.steps for g in st.signals if g.kind == "timer_resets")
                return Finding(
                    id="pattern:false_urgency", type="pattern:false_urgency", title=title, ccpa=ccpa, severity="high",
                    penalty=float(penalty) + 2,
                    detail=(f"A countdown ran down ({reset.detail.get('before_reload')}) and then jumped back to "
                            f"{reset.detail.get('after_reload')} after a page reload: the deadline is not real."),
                    evidence=[_ev(r, s, f"Timer {reset.detail.get('before_reload')} -> {reset.detail.get('after_reload')} on reload")],
                    metrics={"reset_observations": n},
                )
    return None


# ---------------------------------------------------------------------------------------------
def personalization_finding(purchase_runs: list[RunRecord]) -> tuple[Finding | None, dict]:
    ok = [r for r in purchase_runs if r.ok]
    adv = personalization.analyse(ok, "advertised_price")
    fin = personalization.analyse(ok, "final_price")
    summary = {"advertised": adv, "final": fin}
    sig = [(e, "advertised price") for e in adv["effects"] if e["significant"]]
    if not sig:
        sig = [(e, "final price") for e in fin["effects"] if e["significant"]]
    if not sig:
        return None, summary
    parts = [f"{e['factor']} = {e['level']} (vs {e['vs']}): {e['effect_pct']:+.1f}% "
             f"[95% CI {e['ci_low_pct']:+.1f}, {e['ci_high_pct']:+.1f}], p_adj={e['p_adj']:.3g}" for e, _ in sig]
    worst = max(abs(e["effect_pct"]) for e, _ in sig)
    return Finding(
        id="personalization", type="personalization", severity="high" if worst >= 8 else "medium",
        ccpa=taxonomy.COMPUTED_INFO["personalization"][1],
        title="Prices differ by persona beyond random price noise",
        detail=(" | ".join(parts) + f". Site noise (1 sd) is about {adv['noise_pct']}%, so these gaps are not chance."),
        penalty=15.0 + min(10.0, worst / 2), metrics={"effects": [e for e, _ in sig], "noise_pct": adv["noise_pct"]},
    ), summary


# ---------------------------------------------------------------------------------------------
def friction_finding(cancel_runs: list[RunRecord], clf: PatternClassifier) -> tuple[Finding | None, dict]:
    ok = [r for r in cancel_runs if r.ok]
    if not ok:
        return None, {}
    cancel_clicks = _median([r.clicks_total for r in ok])
    signup = _median([r.meta.get("signup_clicks") for r in ok]) or 1
    ratio = cancel_clicks / max(1, signup)
    rep = ok[0]
    interrupts = 0
    for s in rep.steps:
        labels = {lab for lab, _ in clf.predict(s.segments, PATTERN_MIN_PROB)}
        interrupts += bool(labels & {"nagging", "confirmshaming"})
    info = {"cancel_clicks": cancel_clicks, "signup_clicks": signup, "ratio": round(ratio, 1),
            "screens": len(rep.steps), "interruption_screens": interrupts}
    if ratio < 3 and cancel_clicks < 5:
        return None, info
    title, ccpa, _ = taxonomy.INFO["subscription_trap"]
    return Finding(
        id="friction", type="friction", severity="high" if ratio >= 5 else "medium",
        ccpa=taxonomy.COMPUTED_INFO["friction"][1],
        title=f"Cancelling takes {cancel_clicks:.0f} clicks; signing up took {signup:.0f}",
        detail=(f"{ratio:.0f}x more effort to leave than to join, across {len(rep.steps)} screens, "
                f"{interrupts} of them retention or guilt-trip interruptions."),
        penalty=min(15.0, (ratio - 1) * 2.5),
        evidence=[_ev(rep, s) for s in rep.steps[1:4]], metrics=info,
    ), info


# ---------------------------------------------------------------------------------------------
def analyze(audit_id: str, site: str, provider: str, runs: list[RunRecord], clf: PatternClassifier) -> AuditReport:
    purchase = [r for r in runs if r.flow == "purchase"]
    cancel = [r for r in runs if r.flow == "cancel"]
    report = AuditReport(audit_id=audit_id, site=site, provider=provider, runs_total=len(runs),
                         runs_ok=sum(1 for r in runs if r.ok))
    failed = [r for r in runs if not r.ok]
    if failed:
        report.notes.append(f"{len(failed)} of {len(runs)} runs did not complete (e.g. {failed[0].error or 'did not reach the end'}).")
    early = [r for r in purchase if r.ok and r.stopped_at == "price_screen"]
    if early:
        report.notes.append(
            f"{len(early)} of {len(purchase)} purchase runs met a login or personal-details step before payment. "
            "Their payable price is the furthest total the site showed (for example the cart summary); "
            "charges added after that step are not covered.")

    multi = [r for r in purchase if r.ok and (r.meta.get("quantity") or 1) > 1]
    if multi:  # a cart with 2 units is not a price change: compare single-unit runs only
        purchase = [r for r in purchase if r not in multi]
        report.notes.append(f"{len(multi)} run(s) ended with more than one unit in the cart and were left out of the "
                            "price comparisons.")
    groups = product_groups(purchase)
    multi = len(groups) >= 2  # several products audited side by side: analyse each, then merge per finding kind
    if multi:
        priced = [r for _, rs in groups.values() for r in rs]
        report.products = [
            {"name": info["name"], "url": info["url"], "runs": len(rs), "personas": sorted({r.persona.label for r in rs}),
             "used": True, "advertised": round(_median([r.advertised_price for r in rs]), 2),
             "final": round(_median([r.final_price for r in rs]), 2),
             "drip_pct": round((drip_ratio(_median([r.advertised_price for r in rs]), _median([r.final_price for r in rs])) or 0) * 100, 1)}
            for info, rs in groups.values()]
        left = sum(1 for r in purchase if r.ok) - len(priced)
        report.notes.append(f"{len(groups)} products audited side by side; each is compared only with itself."
                            + (f" {left} run(s) on products with fewer than {MIN_RUNS_PER_PRODUCT} runs were left out." if left else ""))
    else:
        priced, report.products, mixed = same_product(purchase)  # compare prices of one product only
        if mixed:
            report.notes.append(mixed)

    findings: list[Finding] = []
    if multi:
        names = [(info["name"], rs) for info, rs in groups.values()]
        for fn in (drip_finding, basket_finding):
            f = _merge([(n, x) for n, rs in names if (x := fn(rs))], len(names))
            if f:
                findings.append(f)
    else:
        for f in (drip_finding(priced), basket_finding(purchase)):
            if f:
                findings.append(f)
    skip = {"preselected_addon"} if any(f.id == "basket_sneaking" for f in findings) else set()
    urgency = urgency_signal_finding(purchase)
    pats = pattern_findings(runs, clf, skip)
    if urgency:
        pats = [p for p in pats if p.id != urgency.id]
        pats.append(urgency)
    findings += pats
    if multi:
        per = [(n, *personalization_finding(rs)) for n, rs in names]
        lead = max((t for t in per if t[1]), key=lambda t: t[1].penalty, default=per[0])
        report.personalization = lead[2]
        pf = _merge([(n, f) for n, f, _ in per if f], len(names))
    else:
        pf, report.personalization = personalization_finding(priced)
    if pf:
        findings.append(pf)
    ff, report.friction = friction_finding(cancel, clf)
    if ff:
        findings.append(ff)

    report.findings = findings
    if multi:
        report.waterfalls = [{**w, "persona": f"{w['persona']} · {n}"} for n, rs in names for w in waterfalls(rs)]
    else:
        report.waterfalls = waterfalls(priced)
    rescore(report)
    return report


def rescore(report: AuditReport) -> None:
    """Sort findings and (re)compute the trust score.

    Every finding carries a penalty in points (shown in the breakdown). Score = 100 * exp(-total / 70):
    0 points -> 100, 20 -> 75, 50 -> 49, 100 -> 24. It falls smoothly, never clips at 0, so two bad sites
    still rank against each other."""
    report.findings = sorted(report.findings, key=lambda f: -f.penalty)
    report.score_breakdown = [{"finding": f.id, "title": f.title, "penalty": round(f.penalty, 1)} for f in report.findings]
    total = sum(f.penalty for f in report.findings)
    report.trust_score = round(100.0 * math.exp(-total / 70.0), 1)
