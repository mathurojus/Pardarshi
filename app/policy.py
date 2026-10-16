"""Policy vs reality: what the fine print promises vs what the audit observed.

Fetch API (TinyFish) pulls terms / refund / cancellation pages as clean text; Gemini lists the
concrete, checkable promises; we compare each promise with measured facts from the audit.
Only promises the measurements directly contradict become findings.
"""
from __future__ import annotations

import asyncio

from google.genai import types

from app import llm
from app.capture.schema import AuditReport, Evidence, Finding
from app.providers.base import Providers

CANDIDATE_PATHS = ["/terms", "/terms-and-conditions", "/refund-policy", "/cancellation-policy", "/policies", "/faq"]

SCHEMA = types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.OBJECT, properties={
    "promise": types.Schema(type=types.Type.STRING),
    "contradicted": types.Schema(type=types.Type.BOOLEAN),
    "observed": types.Schema(type=types.Type.STRING),
    "explanation": types.Schema(type=types.Type.STRING),
}, required=["promise", "contradicted", "observed", "explanation"]))


def observed_facts(report: AuditReport) -> list[str]:
    facts = []
    for w in report.waterfalls[:1]:
        if w.get("advertised") and w.get("final"):
            facts.append(f"Advertised price {w['advertised']:.0f}, payable price {w['final']:.0f} "
                         f"({w['drip_pct']:+.0f}%). Fees only shown at checkout." if w["drip_pct"] > 3 else
                         f"Advertised price {w['advertised']:.0f} equals payable price {w['final']:.0f} (no hidden fees).")
    p = report.personalization.get("advertised", {})
    sig = [e for e in p.get("effects", []) if e["significant"]]
    facts.append("Prices differ by persona: " + ", ".join(f"{e['factor']}={e['level']} {e['effect_pct']:+.0f}%" for e in sig)
                 if sig else "Every persona was shown the same price (differences within noise).")
    if report.friction:
        fr = report.friction
        facts.append(f"Cancelling took {fr['cancel_clicks']:.0f} clicks vs {fr['signup_clicks']:.0f} to sign up "
                     f"({fr['interruption_screens']} interruption screens).")
    return facts


async def policy_check(providers: Providers, base_url: str, report: AuditReport) -> list[Finding]:
    if not llm.available():
        return []
    root = base_url.rstrip("/")
    pages = [p for p in await providers.fetch.fetch([root + path for path in CANDIDATE_PATHS]) if p.text and not p.error]
    pages = [p for p in pages if len(p.text) > 80]
    if not pages:
        report.notes.append("Policy check: no terms/policy page found at the usual paths.")
        return []
    policy_text = "\n\n".join(f"[{p.url}]\n{p.text[:3500]}" for p in pages[:3])
    facts = observed_facts(report)
    prompt = (
        "You compare a website's written policy with measured facts from an automated audit.\n"
        f"POLICY TEXT:\n{policy_text}\n\nMEASURED FACTS:\n- " + "\n- ".join(facts) + "\n\n"
        "List each concrete, checkable promise in the policy about prices, fees, personalization or "
        "cancellation. For each, say whether the measured facts CONTRADICT it (contradicted=true only if a "
        "fact clearly shows the opposite; if the facts are silent, contradicted=false). Quote the promise "
        "verbatim. Do not invent facts."
    )
    try:
        rows = await asyncio.to_thread(llm.generate_json, prompt, json_schema=SCHEMA, temperature=0.0)
    except Exception as e:
        busy = "429" in str(e) or "503" in str(e)
        report.notes.append("Policy check skipped (Gemini is busy or out of quota)." if busy else f"Policy check skipped: {str(e)[:80]}")
        return []
    out = []
    for i, r in enumerate(x for x in rows if x["contradicted"]):
        out.append(Finding(
            id=f"policy:{i}", type="policy", severity="high", ccpa="Misleading policy / unfair trade practice",
            title=f"Policy contradicts behaviour: “{r['promise'][:90]}”", detail=f"{r['explanation']} Observed: {r['observed']}",
            penalty=8.0, evidence=[Evidence(text=r["promise"], step_name="policy page")], metrics={"policy_url": pages[0].url},
        ))
    return out
