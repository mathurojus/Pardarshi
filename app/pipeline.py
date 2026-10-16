"""Audit pipeline: choose personas -> run flows in parallel -> analyse -> save report."""
from __future__ import annotations

import asyncio
import random
import uuid
from typing import Callable
from urllib.parse import urlparse

from app.analysis import product_of
from app.progress import STEP_HOOK
from app.capture.schema import AuditReport, FlowSpec, Persona, RunRecord
from app.capture.store import AuditStore
from app.config import settings
from app.ml.pattern_clf import get_classifier
from app.personas import BASELINE, default_matrix, localise
from app.providers import get_providers
from app.providers.base import Providers
from app.providers.opensource import ensure_allowed

DEMO_SITES = {"dirty", "clean"}


class AuditInconclusive(RuntimeError):
    """Every purchase run failed before the payment screen; no report is written."""
TINYFISH_COUNTRIES = {"US", "GB", "CA", "DE", "FR", "JP", "AU"}
Emit = Callable[[dict], None]


def specs_for(site: str, flows: tuple[str, ...] = ("purchase", "cancel"), session_file: str | None = None) -> list[FlowSpec]:
    if site in DEMO_SITES:
        base = f"{settings.demo_shop_url}/{site}"
        return [FlowSpec(site=site, base_url=base, flow=f, script="ticketshop",
                         intent="Buy one ticket for the first event listed" if f == "purchase"
                         else "Start the free trial membership, then cancel it") for f in flows if f in ("purchase", "cancel")]
    host = urlparse(site if "//" in site else f"https://{site}").hostname or site
    url = site if "//" in site else f"https://{site}"
    return [FlowSpec(site=host, base_url=url, flow="purchase", session_file=session_file, polite=True,
                     intent="Pick the first product, add it to the cart and go as far towards payment as possible")]


def audit_label(site: str) -> str:
    return {"dirty": "TicketNest (demo)", "clean": "FairTix (demo)"}.get(site, site)


def _supports_country(providers: Providers, spec: FlowSpec) -> bool:
    fn = getattr(providers.agent, "supports_country", None)
    return bool(fn(spec)) if fn else False


def _supports_device(providers: Providers, spec: FlowSpec) -> bool:
    fn = getattr(providers.agent, "supports_device", None)
    return bool(fn(spec)) if fn else False


async def run_audit(
    site: str,
    *,
    providers: Providers | None = None,
    store: AuditStore | None = None,
    personas: list[Persona] | None = None,
    repeats: int = 3,
    flows: tuple[str, ...] = ("purchase", "cancel"),
    on_event: Emit | None = None,
    audit_id: str | None = None,
    with_policy: bool = True,
    session_file: str | None = None,
) -> AuditReport:
    emit = on_event or (lambda e: None)
    store = store or AuditStore()
    owns = providers is None
    providers = providers or get_providers(store=store)
    audit_id = audit_id or f"{site.replace('/', '_').replace(':', '')[:24]}-{uuid.uuid4().hex[:6]}"
    personas = personas or default_matrix()
    base = BASELINE
    # A real site audited from this machine is seen from here (India): the shopper's language and timezone say
    # so too, instead of a US browser on an Indian connection. TinyFish has no India exit, so it keeps US.
    if site not in DEMO_SITES and providers.name != "tinyfish" and settings.home_country:
        personas = localise(personas, settings.home_country)
        base = localise([BASELINE], settings.home_country)[0]
    try:
        jobs: list[tuple[FlowSpec, Persona, int]] = []
        if session_file:  # one signed-in account: one shopper, one run (a shared cart would mix repeats)
            personas, repeats = [base], 1
        for spec in specs_for(site, flows, session_file):
            usable = [p for p in personas
                      if (p.country == base.country or _supports_country(providers, spec))
                      and (p.device == "desktop" or _supports_device(providers, spec))]
            if providers.name == "tinyfish":
                usable = [p for p in usable if p.country in TINYFISH_COUNTRIES]
            if spec.flow == "cancel":  # friction does not depend on persona: baseline only, fewer repeats
                usable, reps = [base], max(1, min(2, repeats))
            else:
                reps = repeats
            jobs += [(spec, p, i) for p in usable for i in range(reps)]
        key = lambda spec, p, i: f"{spec.flow}:{p.id}:{i}"  # noqa: E731
        emit({"type": "started", "audit_id": audit_id, "total": len(jobs), "provider": providers.name, "site": site,
              "jobs": [{"key": key(s, p, i), "flow": s.flow, "persona": p.label, "persona_id": p.id, "repeat": i}
                       for s, p, i in jobs]})
        # Run order: in rounds (every profile's 1st run, then every profile's 2nd...), shuffled within a round.
        # A price that drifts over time then hits all profiles alike instead of posing as personalised pricing.
        rng = random.Random(audit_id)
        rounds: list[list] = []
        for r in sorted({i for *_, i in jobs}):
            batch = [j for j in jobs if j[2] == r]
            rng.shuffle(batch)
            rounds.append(batch)
        jobs = [j for batch in rounds for j in batch]

        done = 0
        runs: list[RunRecord] = []

        async def one(spec: FlowSpec, p: Persona, i: int) -> None:
            nonlocal done
            k = key(spec, p, i)
            STEP_HOOK.set(lambda name: emit({"type": "step", "key": k, "step": name}))  # this task only
            run = await providers.agent.run_flow(spec, p, audit_id, i)
            runs.append(run)
            done += 1
            prod = product_of(run) if spec.flow == "purchase" and run.ok else None
            emit({"type": "run", "key": k, "steps": [st.name for st in run.steps], "stopped_at": run.stopped_at, "done": done, "total": len(jobs), "flow": spec.flow, "persona": p.label,
                  "repeat": i, "ok": run.ok, "error": run.error, "final_price": run.final_price,
                  "product": prod and {"name": prod["name"], "url": prod["url"]}})

        async def run_all(js: list) -> None:
            if any(s.polite for s, _, _ in js):  # real sites: one shopper at a time, never a burst of sessions
                for j in js:
                    await one(*j)
            else:
                await asyncio.gather(*(one(*j) for j in js))

        # Same product for every shopper: on a site without a script, the baseline shopper's first run picks a
        # product; every other purchase run then starts on that exact product page instead of choosing its own
        # (listings differ by device, location and visitor, so "the first product" can be a different item).
        at = next((n for n, (s, p, i) in enumerate(jobs) if s.flow == "purchase" and not s.script and not s.product_url
                   and p.id == BASELINE.id and i == 0), None)
        if at is not None and len(jobs) > 1:
            pilot = jobs[at]
            await one(*pilot)
            rest = jobs[:at] + jobs[at + 1:]
            prod = product_of(runs[-1]) if runs[-1].ok else None
            pinned = None
            if prod and prod["url"]:
                try:
                    ensure_allowed(prod["url"])
                    pinned = pilot[0].model_copy(update={
                        "product_url": prod["url"],
                        "intent": f"This page is the product '{prod['name']}'. Add exactly this product to the cart and go "
                                  "as far towards payment as possible. Do not switch to a different product."})
                except PermissionError:
                    pinned = None
            if pinned:
                emit({"type": "pinned", "product": {"name": prod["name"], "url": prod["url"]}})
                rest = [(pinned if s is pilot[0] else s, p, i) for s, p, i in rest]
            await run_all(rest)
        else:
            await run_all(jobs)
        purchase = [r for r in runs if r.flow == "purchase"]
        if not any(r.ok for r in purchase):
            # Nothing was measured, so there is nothing to score: a "100" here would falsely read as transparent.
            if purchase and all(r.stopped_at == "blocked" for r in purchase):
                reason, msg = "blocked", (
                    "The site blocked every automated shopper with a CAPTCHA or access-denied page. Pardarshi reports "
                    "this and does not get around bot protection.")
            elif any(r.stopped_at == "login" for r in purchase):
                reason, msg = "login", (
                    "The site asked for a login or personal details before showing any checkout total, and Pardarshi "
                    "never enters those. Connect your own account (python -m scripts.connect_account) to audit it "
                    "behind the login.")
            else:
                why = next((r.error for r in purchase if r.error), None)
                reason, msg = "other", ("No agent reached a checkout total, so nothing could be measured. "
                                        + (f"First error: {why}" if why else "The agent could not find a way to the checkout."))
            # The evidence of why: the last page each shopper saw (the block page, the login wall...).
            shots = [r.steps[-1].screenshot for r in purchase if r.steps and r.steps[-1].screenshot][:3]
            emit({"type": "inconclusive", "reason": reason, "message": msg, "screenshots": shots,
                  "runs": len(purchase), "site": site})
            raise AuditInconclusive(msg)
        emit({"type": "analysing"})
        from app.analysis import analyze, rescore  # local import keeps module load light
        from app.policy import policy_check

        report = await asyncio.to_thread(analyze, audit_id, audit_label(site), providers.name, runs, get_classifier())
        if session_file:
            report.notes.append("Audited with the account holder's own signed-in session: one shopper, one run. "
                                "Personalised pricing across shoppers was not tested.")
        if with_policy:
            emit({"type": "policy"})
            report.findings += await policy_check(providers, specs_for(site, flows)[0].base_url, report)
            rescore(report)
        store.save_report(report)
        emit({"type": "complete", "audit_id": audit_id, "trust_score": report.trust_score})
        return report
    finally:
        if owns:
            await providers.aclose()
