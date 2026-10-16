"""Draft a consumer complaint from an audit. Gemini writes it; a template is the offline fallback."""
from __future__ import annotations

import asyncio
from datetime import datetime

from app import llm
from app.capture.schema import AuditReport

HELPLINE = "National Consumer Helpline (consumerhelpline.gov.in, 1915)"
DISCLAIMER = ("This complaint was generated from an automated audit. The categories named are indicative, "
              "to help you describe what you saw; they are not a legal finding.")


def _facts(report: AuditReport) -> str:
    lines = [f"- [{f.severity.upper()}] {f.title} (category: {f.ccpa or 'n/a'}). {f.detail}" for f in report.findings]
    return "\n".join(lines) or "- No violations were found."


def template_draft(report: AuditReport, name: str = "[Your name]") -> str:
    return (
        f"To: {HELPLINE}\nDate: {datetime.now():%d %B %Y}\nSubject: Complaint about misleading practices on {report.site}\n\n"
        f"I am {name}. I am raising a concern about {report.site}. An automated audit completed {report.runs_ok} "
        f"test bookings on the site as different types of customers, stopping before payment, and recorded "
        f"screenshots at every step. It found:\n\n{_facts(report)}\n\n"
        f"Overall transparency score: {report.trust_score:.0f}/100. Screenshots and the price breakdown are "
        f"attached as evidence. I request that the matter be examined under the Guidelines for Prevention and "
        f"Regulation of Dark Patterns, 2023, and that the seller be asked to correct these practices.\n\n"
        f"[Your contact details]\n\n{DISCLAIMER}"
    )


async def draft_complaint(report: AuditReport, name: str = "[Your name]") -> dict:
    """Returns {'text': ..., 'method': 'gemini'|'template'}."""
    if not report.findings:
        return {"text": "The audit found no violations, so there is nothing to complain about.", "method": "none"}
    if llm.available():
        prompt = (
            f"Write a clear, polite, factual consumer complaint to India's {HELPLINE} about the website "
            f"'{report.site}', from a customer named {name}. Use ONLY the findings below; do not add facts or "
            f"legal conclusions. Use short paragraphs and a bullet list of findings with the category name for "
            f"each. Mention that screenshots are attached, that the test stopped before payment, and ask for the "
            f"matter to be examined under the 2023 dark-patterns guidelines. End with the disclaimer: "
            f"'{DISCLAIMER}'\n\nFINDINGS:\n{_facts(report)}\nTransparency score: {report.trust_score:.0f}/100"
        )
        try:
            return {"text": await asyncio.to_thread(llm.generate, prompt, temperature=0.3), "method": "gemini"}
        except Exception:
            pass
    return {"text": template_draft(report, name), "method": "template"}
