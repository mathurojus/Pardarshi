"""Google ADK orchestration.

root agent  "pardarshi"        plans, runs audits and explains results (tools below)
 ├─ sub-agent "policy_analyst"   reads a site's written policies (Fetch API) and compares them with audit facts
 └─ sub-agent "complaint_writer" turns findings into a consumer complaint

The agents call the SAME pipeline the dashboard uses, so chat and UI never disagree.
Run from a terminal:   python -m app.agents.orchestrator "Audit the dirty shop quickly"
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app import auth
from app.capture.store import AuditStore
from app.complaint import draft_complaint
from app.config import settings
from app.pipeline import DEMO_SITES, AuditInconclusive, run_audit
from app.personas import default_matrix, quick_matrix
from app.providers import get_providers

APP_NAME = "pardarshi"
MODEL = os.getenv("ADK_MODEL", "gemini-3.5-flash-lite")
_store = AuditStore()


def _visible(audit_id: str) -> bool:
    """Chat tools only see audits the signed-in user may see (CURRENT_USER is set by the API endpoint)."""
    uid = auth.CURRENT_USER.get()
    return uid is None or auth.can_view(audit_id, uid)


def _summ(report) -> dict:
    return {
        "audit_id": report.audit_id, "site": report.site, "provider": report.provider,
        "trust_score": report.trust_score, "runs_completed": f"{report.runs_ok}/{report.runs_total}",
        "findings": [{"id": f.id, "severity": f.severity, "title": f.title, "detail": f.detail[:300],
                      "ccpa_category": f.ccpa} for f in report.findings[:10]],
        "drip_pct": next((w["drip_pct"] for w in report.waterfalls[:1]), None),
        "notes": report.notes,
    }


# ----------------------------------------------------------------------------- tools
async def run_audit_tool(site: str, quick: bool = True, products: int = 1) -> dict:
    """Audit a website by sending browser personas (different device, country, new/returning user) through its
    booking flow up to (never including) payment, and through its cancellation flow when available.
    Args:
      site: 'dirty' or 'clean' for the built-in demo shops, or a hostname that is on the allowlist.
      products: how many products (1 to 5) to audit side by side, each in its own parallel lane.
      quick: True runs 2 personas (fast, cheap); False runs the full 6-persona matrix (slower).
    Returns the audit id, the 0-100 transparency score and the findings (drip pricing, dark patterns,
    personalised pricing, cancellation friction, policy contradictions)."""
    providers = get_providers(store=_store)
    try:
        report = await run_audit(site, providers=providers, store=_store, repeats=3,
                                 personas=quick_matrix() if quick else default_matrix(),
                                 products=max(1, min(5, int(products))))
        auth.set_owner(report.audit_id, auth.CURRENT_USER.get())
        return _summ(report)
    except AuditInconclusive as e:  # let the agent explain it rather than report a score for nothing measured
        return {"error": str(e)}
    finally:
        await providers.aclose()


def list_audits_tool() -> list[dict]:
    """List audits that were already run (newest first) with their transparency scores."""
    return [{"audit_id": r.audit_id, "site": r.site, "trust_score": r.trust_score, "findings": len(r.findings)}
            for r in _store.list_reports() if _visible(r.audit_id)][:15]


def get_audit_tool(audit_id: str) -> dict:
    """Get the findings and score of one earlier audit by its id."""
    r = _store.load_report(audit_id) if _visible(audit_id) else None
    return _summ(r) if r else {"error": f"No audit named {audit_id}"}


async def find_peer_sites(service: str) -> list[dict]:
    """Search the live web for sites offering the same kind of service, to compare their transparency.
    Args:
      service: e.g. 'online event ticketing India'."""
    providers = get_providers(store=_store)
    try:
        hits = await providers.search.search(service, n=8)
        return [h.model_dump() for h in hits]
    finally:
        await providers.aclose()


async def read_policy(url: str) -> dict:
    """Fetch a terms / refund / cancellation policy page as clean text.
    Args:
      url: full URL of the policy page."""
    providers = get_providers(store=_store)
    try:
        pages = await providers.fetch.fetch([url])
        p = pages[0]
        return {"url": p.url, "title": p.title, "text": p.text[:6000], "error": p.error}
    finally:
        await providers.aclose()


async def draft_complaint_tool(audit_id: str, your_name: str = "[Your name]") -> dict:
    """Write a consumer complaint (for India's National Consumer Helpline) from an audit's findings.
    Args:
      audit_id: id of a finished audit.
      your_name: the complainant's name."""
    r = _store.load_report(audit_id) if _visible(audit_id) else None
    if not r:
        return {"error": f"No audit named {audit_id}"}
    return await draft_complaint(r, your_name)


# ----------------------------------------------------------------------------- agents
def build_root_agent() -> LlmAgent:
    os.environ.setdefault("GOOGLE_API_KEY", settings.gemini_api_key)
    policy_analyst = LlmAgent(
        name="policy_analyst", model=MODEL,
        description="Reads a website's written terms/refund/cancellation policy and compares its promises with measured audit facts.",
        instruction=("Use read_policy to fetch the policy page, then compare each concrete promise about fees, pricing "
                     "fairness and cancellation with the facts from get_audit_tool. Quote promises verbatim. Say "
                     "'contradicted' only when the audit facts clearly show the opposite."),
        tools=[read_policy, get_audit_tool])
    complaint_writer = LlmAgent(
        name="complaint_writer", model=MODEL,
        description="Drafts a consumer complaint from an audit's findings.",
        instruction="Use draft_complaint_tool for the audit the user names. Return the complaint text unchanged and remind the user to attach the screenshots.",
        tools=[draft_complaint_tool])
    return LlmAgent(
        name="pardarshi", model=MODEL,
        description="Consumer-protection auditor: finds drip pricing, dark patterns, personalised pricing and cancellation traps.",
        instruction=(
            "You are Pardarshi, an auditor that protects online shoppers. You never pay, never enter personal data and only "
            "audit allowlisted sites. To check a site call run_audit_tool (it can take a minute; the demo shops are "
            f"{', '.join(sorted(DEMO_SITES))}). Explain results plainly: lead with the transparency score, then the 3 most "
            "serious findings with concrete numbers (prices, clicks, percentages), and say what the shopper can do. "
            "Never invent numbers: use only what the tools return. For policy questions delegate to policy_analyst; for "
            "a complaint delegate to complainant_writer's tool by calling complaint_writer. To compare services call "
            "find_peer_sites first."),
        tools=[run_audit_tool, list_audits_tool, get_audit_tool, find_peer_sites],
        sub_agents=[policy_analyst, complaint_writer])


_session_service = InMemorySessionService()
_runner: Runner | None = None


def _get_runner() -> Runner:
    global _runner
    if _runner is None:
        _runner = Runner(app_name=APP_NAME, agent=build_root_agent(), session_service=_session_service)
    return _runner


async def chat(message: str, session_id: str | None = None, user_id: str = "web") -> dict:
    """One conversational turn. Returns {'reply', 'session_id', 'trace': [tool calls and agent hand-offs]}."""
    runner = _get_runner()
    session_id = session_id or uuid.uuid4().hex[:8]
    try:
        await _session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    except Exception:
        pass  # session already exists
    trace, reply = [], ""
    content = types.Content(role="user", parts=[types.Part(text=message)])
    async for ev in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
        for call in (ev.get_function_calls() or []):
            trace.append({"agent": ev.author, "tool": call.name, "args": dict(call.args or {})})
        if ev.is_final_response() and ev.content and ev.content.parts:
            reply = "".join(p.text or "" for p in ev.content.parts)
    return {"reply": reply, "session_id": session_id, "trace": trace}


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) or "Audit the dirty shop quickly and summarise the worst problems."
    out = asyncio.run(chat(msg))
    for t in out["trace"]:
        print(f"[{t['agent']}] -> {t['tool']}({t['args']})")
    print("\n" + out["reply"])
