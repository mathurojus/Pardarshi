"""Deterministic, scripted flows for the demo shop (no LLM needed, so tests are repeatable).

Real sites use the goal-driven agent instead (app/providers/llm_agent.py or TinyFish).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

from app import progress
from app.capture.schema import Persona, RunRecord, Step, utcnow
from app.ml.price_extract import final_total, headline_price
from app.providers.dom import capture_step


@dataclass
class Recorder:
    page: object
    run: RunRecord
    shots_dir: Path
    rel_root: Path
    clicks: int = 0
    steps: list[Step] = field(default_factory=list)
    hit_payment_form: bool = False

    async def goto(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded")

    async def click(self, locator) -> None:
        try:
            await locator.first.click(timeout=8000)
        except PlaywrightTimeout:  # one slower retry: a busy machine can miss the first window
            await locator.first.click(timeout=20000)
        self.clicks += 1
        await self.page.wait_for_load_state("domcontentloaded")

    async def snap(self, name: str, check_timer_reload: bool = False) -> Step:
        kw = dict(name=name, index=len(self.steps), clicks=self.clicks, shots_dir=self.shots_dir,
                  rel_root=self.rel_root, check_timer_reload=check_timer_reload)
        try:
            step, has_card = await capture_step(self.page, **kw)
        except PlaywrightError:  # the page navigated mid-capture (a redirect): let it settle, capture once more
            try:
                await self.page.wait_for_load_state("load", timeout=15000)
            except PlaywrightError:
                pass
            step, has_card = await capture_step(self.page, **kw)
        self.steps.append(step)
        progress.step(name)
        self.hit_payment_form = self.hit_payment_form or has_card
        return step


async def ticketshop_purchase(rec: Recorder, base_url: str, persona: Persona) -> None:
    page = rec.page
    await rec.goto(base_url + "/")
    if persona.user_state == "returning":
        await rec.goto(base_url + "/")  # a second visit: the shop now sees a returning visitor
    await rec.click(page.locator("a[href*='/event/']").nth(int(rec.run.meta.get("product_slot") or 0)))
    event = await rec.snap("event", check_timer_reload=True)
    adv, cur = headline_price(event.text)
    rec.run.advertised_price, rec.run.currency = adv, cur
    await rec.click(page.get_by_role("button", name="Book now").or_(page.get_by_role("button", name="Select tickets")))
    await rec.snap("cart")
    await rec.click(page.get_by_role("button", name="Continue to payment"))
    payment = await rec.snap("payment")
    # Payment guard: we only ever LOOK at the payment page. Nothing is typed into it.
    rec.run.final_price = final_total(payment.prices)
    rec.run.reached_end = rec.hit_payment_form


async def ticketshop_cancel(rec: Recorder, base_url: str, persona: Persona) -> None:
    page = rec.page
    await rec.goto(base_url + "/plus")
    await rec.click(page.get_by_role("button", name="Start free trial"))
    rec.run.meta["signup_clicks"] = rec.clicks
    rec.clicks = 0
    await rec.snap("signed_up")

    async def link(name: str):
        await rec.click(page.get_by_role("link", name=name, exact=True))
        await rec.snap(name.lower().replace(" ", "_").replace("'", ""))

    if await page.get_by_role("link", name="Cancel membership", exact=True).count():
        await link("Cancel membership")
        await rec.click(page.get_by_role("button", name="Confirm cancellation"))
        await rec.snap("done")
    else:
        for name in ("Settings", "Membership", "Manage billing", "End membership", "No thanks, continue"):
            await link(name)
        await page.select_option("select[name=why]", "Not using it")
        rec.clicks += 1  # choosing a reason is one more required interaction
        await rec.click(page.get_by_role("button", name="Next"))
        await rec.snap("confirm")
        await rec.click(page.get_by_role("link", name="Yes, I don't care about saving money", exact=True))
        await rec.snap("done")
    rec.run.reached_end = "cancelled" in rec.steps[-1].text.lower()


SCRIPTS = {"ticketshop": {"purchase": ticketshop_purchase, "cancel": ticketshop_cancel}}


async def execute(page, spec, persona: Persona, run: RunRecord, store) -> None:
    """Run one flow on an already-configured page (local Chromium or a TinyFish cloud browser).

    A scripted flow is used when one exists for the site; otherwise a Gemini-driven agent loop
    works toward the intent. Failures are recorded on the run, never raised.
    """
    rec = Recorder(page=page, run=run, shots_dir=store.shots_dir(run.audit_id), rel_root=store.root)
    try:
        script = SCRIPTS.get(spec.script or "", {}).get(spec.flow)
        if script:
            await script(rec, spec.base_url, persona)
        else:
            from app.providers.llm_agent import run_goal_agent  # lazy: needs a Gemini key

            await run_goal_agent(rec, spec, persona)
    except Exception as e:  # a failed run is data, not a crash
        run.error = f"{type(e).__name__}: {e}"[:300]
    finally:
        run.steps = rec.steps
        run.clicks_total = rec.clicks
        run.finished_at = utcnow()
