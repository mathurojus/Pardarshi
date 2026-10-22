"""Goal-driven browser agent for the open-source lane (any site without a script).

Loop: snapshot the page -> list clickable elements -> ask Gemini for ONE next action -> do it.
Hard guards (not left to the model): never type into card/credential/personal fields, and stop the
moment a card form is visible. This is the open-source counterpart of TinyFish's Agent API.
"""
from __future__ import annotations

import asyncio
import random
import re

from google.genai import types
from playwright.async_api import Error as PlaywrightError

from app import llm
from app.capture.schema import FlowSpec, Persona
from app.config import settings
from app.ml.price_extract import cart_quantity, final_total, headline_price
from app.providers.scripts import Recorder

LIST_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 90);
  document.querySelectorAll('[data-pd-id]').forEach(e => e.removeAttribute('data-pd-id'));
  const out = []; let i = 0;
  for (const el of document.querySelectorAll('a[href],button,input[type=submit],input[type=button],[role=button],input[type=text],input[type=search],select')) {
    if (!vis(el) || el.disabled) continue;
    const tag = el.tagName.toLowerCase();
    el.setAttribute('data-pd-id', String(i));
    out.push({id: i, tag, type: el.type || '', text: clean(el.innerText || el.value || el.placeholder || el.name || el.getAttribute('aria-label')),
              name: clean((el.name || '') + ' ' + (el.placeholder || '')), href: tag === 'a' ? clean(el.getAttribute('href')) : ''});
    i++; if (i >= 60) break;
  }
  return out;
}
"""

SCHEMA = types.Schema(type=types.Type.OBJECT, properties={
    "action": types.Schema(type=types.Type.STRING, enum=["click", "type", "select", "back", "done"]),
    "target": types.Schema(type=types.Type.INTEGER, nullable=True),
    "text": types.Schema(type=types.Type.STRING, nullable=True),
    "reason": types.Schema(type=types.Type.STRING),
}, required=["action", "reason"])

SAFE_FIELD = re.compile(r"search|from|to\b|destination|origin|city|location|where|query|keyword", re.I)
BLOCKED_FIELD = re.compile(r"card|cvv|cvc|expir|pass|otp|email|phone|mobile|name|address|pin|aadhaar|pan\b", re.I)

# Buttons that pay, place or confirm an order or start a subscription. Refused in code, whatever the model says:
# with a signed-in account (saved cards, cash on delivery) one of these could complete a real purchase.
PAY_BUTTON = re.compile(
    r"^\s*(pay\b|pay\s*(now|securely|₹|rs\.?|inr|\$)|make\s+payment|proceed\s+to\s+pay|continue\s+to\s+pay|"
    r"confirm\s+(and\s+|&\s+)?pay|confirm\s+(order|booking|purchase|payment)|complete\s+(purchase|payment|order|booking)|"
    r"place\s+order\s+(and|&)\s+pay|buy\s+and\s+pay|subscribe(\s+now)?$|start\s+(my\s+)?(subscription|membership))",
    re.I)

SYSTEM = (
    "You drive a web browser to audit a website like an ordinary shopper. Choose exactly ONE next action. "
    "Rules: never enter card, bank, password, OTP, email, phone, name or address. You may type only into search "
    "or route/city/destination boxes. Never press a button that pays, places or confirms an order. "
    "Prefer the path that shows the full price breakdown (product page, add to cart, view cart or checkout summary). "
    "Add exactly ONE unit of ONE product: once it is in the cart or bag, never press add-to-cart or buy-now again; "
    "go to the cart or checkout instead. If the cart already shows the price breakdown and nothing new can be reached, answer done. "
    "When the page asks for card or payment details, or to log in or give personal details, answer action=done."
)


def finish_run(run, steps, hit_payment_form: bool, stop_reason: str = "", first_index: int = 1) -> None:
    """Decide what a goal-agent run measured. Pure (no browser, no model), so it is unit-tested.

    payment      -> reached a card or payment-method screen.
    price_screen -> stopped at a login/details wall (or a refused pay button, or ran out of steps) after the
                    site had already shown a checkout total that differs from the first price's page.
    login        -> a login/details wall came before any checkout total: nothing to compare.
    blocked      -> a CAPTCHA / access-denied page: reported as an error, never worked around.
    """
    # The first page is usually a listing full of other products' prices, unless the run started on a pinned
    # product page (first_index=0), where the first price IS the product's advertised price.
    priced = [s for s in steps if s.index >= first_index and headline_price(s.text)[0] is not None]
    if priced:
        run.advertised_price, run.currency = headline_price(priced[0].text)
        run.meta["advertised_step"] = priced[0].index
    totals = [s for s in steps if final_total(s.prices) is not None]
    last_total = totals[-1] if totals else None
    run.final_price = final_total(last_total.prices) if last_total else None
    if last_total:
        run.meta["price_step"] = last_total.index
        qty = cart_quantity(last_total.text)
        if qty:
            run.meta["quantity"] = qty
    if stop_reason:
        run.meta["stop_reason"] = stop_reason

    if any(s.note == "bot_wall" for s in steps):
        run.stopped_at = "blocked"
        run.error = run.error or "Blocked by the site's bot protection (CAPTCHA or access-denied page). Not bypassed."
    elif hit_payment_form:
        run.stopped_at, run.reached_end = "payment", True
    elif last_total and priced and last_total.index > priced[0].index:
        run.stopped_at = "price_screen"
    else:
        run.stopped_at = "login" if any(s.note == "login_wall" for s in steps) else ""


async def click_through(rec: Recorder, loc) -> bool:
    """Click like a shopper would, recovering from what real sites throw in the way. False if it never landed.

    Links that open a new tab stay in this tab (the run follows one page). A popup or banner covering the
    target is dismissed with Escape, as a person would close it; then the element's own click() is used.
    """
    page = rec.page
    try:
        await loc.first.evaluate("e => { const a = e.closest('a'); if (a) a.removeAttribute('target'); }")
    except PlaywrightError:
        pass
    try:
        await loc.first.click(timeout=8000)
    except PlaywrightError:
        try:
            await page.keyboard.press("Escape")
            await loc.first.click(timeout=5000)
        except PlaywrightError:
            try:
                await loc.first.evaluate("e => e.click()")
            except PlaywrightError:
                return False
    rec.clicks += 1
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PlaywrightError:
        pass
    return True


async def run_goal_agent(rec: Recorder, spec: FlowSpec, persona: Persona) -> None:
    page, run = rec.page, rec.run
    await rec.goto(spec.product_url or spec.base_url)  # a pinned product: every shopper checks the same item
    history: list[str] = []
    acts: list[tuple] = []
    stop = "max_steps"
    for i in range(settings.max_steps):
        step = await rec.snap(f"step{i}", check_timer_reload=(i == 1))
        if rec.hit_payment_form:  # payment guard: a card or payment-method screen ends the run before anything is typed
            stop = "payment"
            break
        if step.note in ("bot_wall", "login_wall"):  # a site check or login ends the run: reported, never bypassed
            stop = step.note
            break
        if spec.polite:  # one shopper at a time, at a human pace
            await asyncio.sleep(random.uniform(*settings.polite_delay))
        try:
            elements = await page.evaluate(LIST_JS)
        except PlaywrightError:  # the page navigated while being read (redirects, client-side routing): read it again
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightError:
                pass
            continue
        if not elements:
            break
        listing = "\n".join(f"[{e['id']}] <{e['tag']}{' ' + e['type'] if e['type'] else ''}> {e['text']}"
                            + (f"  -> {e['href']}" if e['href'] else "") for e in elements)
        prompt = (f"INTENT: {spec.intent}\nPAGE TITLE: {step.title}\nSTEP {i + 1}/{settings.max_steps}\n"
                  f"PREVIOUS ACTIONS: {'; '.join(history[-6:]) or 'none'}\n\nVISIBLE TEXT (truncated):\n{step.text[:1500]}\n\n"
                  f"CLICKABLE ELEMENTS:\n{listing}\n\nWhat is the single best next action to reach the end of the intent?")
        try:
            d = await asyncio.to_thread(llm.generate_json, prompt, json_schema=SCHEMA, system=SYSTEM, temperature=0.1)
        except RuntimeError:  # every model busy (503/429): wait once, then keep what was measured so far
            await asyncio.sleep(15)
            try:
                d = await asyncio.to_thread(llm.generate_json, prompt, json_schema=SCHEMA, system=SYSTEM, temperature=0.1)
            except RuntimeError:
                stop = "llm_unavailable"
                break
        action, target = d.get("action"), d.get("target")
        history.append(f"{action} {target if target is not None else ''} ({d.get('reason', '')[:50]})")
        acts.append((action, target, step.url))
        if len(acts) >= 3 and len(set(acts[-3:])) == 1:  # the same click on the same page three times: stuck
            stop = "stuck"
            break
        if action == "done":
            stop = "agent_done"
            break
        if action == "back":
            try:
                await page.go_back(timeout=15000)
            except PlaywrightError:
                history.append("back failed")
            continue
        el = next((e for e in elements if e["id"] == target), None)
        if el is None:
            continue
        loc = page.locator(f"[data-pd-id='{target}']")
        if action == "type":
            if BLOCKED_FIELD.search(el["name"]) or not SAFE_FIELD.search(el["name"] + " " + el["text"]):
                history.append("refused to type: not a safe search field")
                continue
            try:
                await loc.first.fill((d.get("text") or "")[:60], timeout=8000)
                await loc.first.press("Enter")
                rec.clicks += 1
            except PlaywrightError:
                history.append(f"typing into [{target}] failed; pick another element")
        elif action == "select":
            try:
                await loc.first.select_option(index=1, timeout=8000)
                rec.clicks += 1
            except PlaywrightError:
                history.append(f"select [{target}] failed; pick another element")
        else:
            if PAY_BUTTON.search(el["text"]):  # hard guard: this click could pay or place an order
                history.append(f"refused to click '{el['text'][:40]}': it would pay or place an order")
                stop = "pay_button"
                break
            if not await click_through(rec, loc):
                history.append(f"click on [{target}] '{el['text'][:30]}' did not work (covered or hidden); pick another element")

    run.steps = rec.steps
    finish_run(run, rec.steps, rec.hit_payment_form, stop, first_index=0 if spec.product_url else 1)
    run.meta["agent_steps"] = history
