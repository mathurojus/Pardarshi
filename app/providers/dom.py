"""In-page JavaScript + helpers that turn a live page into Step data (text, segments, signals)."""
from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from app.capture.schema import PriceItem, Segment, Signal, Step
from app.ml.price_extract import extract_items

COLLECT_JS = r"""
() => {
  const BLOCKS = 'p,div,ul,ol,li,form,h1,h2,h3,h4,table,section,header,main,button,a,label,select';
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const segs = []; const seen = new Set();
  for (const el of document.querySelectorAll('h1,h2,h3,h4,button,a,label,p,li,span,div,td,th,option')) {
    if (!vis(el)) continue;
    const tag = el.tagName.toLowerCase();
    const interactive = ['button', 'a', 'label'].includes(tag);
    if (!interactive && el.querySelector(BLOCKS)) continue;          // only leaf blocks carry their own copy
    const t = clean(el.innerText);
    if (t.length < 3 || t.length > 300) continue;
    const key = t.toLowerCase(); if (seen.has(key)) continue; seen.add(key);
    let role = 'text';
    if (tag === 'button' || el.classList.contains('btn') || el.getAttribute('role') === 'button') role = 'button';
    else if (tag === 'a') role = 'link';
    else if (tag === 'label') role = 'label';
    else if (/^h[1-4]$/.test(tag)) role = 'heading';
    const lab = el.closest('label'); const cb = lab ? lab.querySelector('input[type=checkbox]') : null;
    segs.push({text: t, tag, role, near_checked: !!(cb && cb.checked)});
    if (segs.length >= 200) break;
  }
  const checked = [...document.querySelectorAll('input[type=checkbox]:checked')].filter(vis).map(cb => {
    const lab = cb.closest('label'); return clean(lab ? lab.innerText : (cb.parentElement ? cb.parentElement.innerText : ''));
  });
  const strikes = [...document.querySelectorAll('del,s,strike,[style*="line-through"]')].filter(vis).map(e => clean(e.innerText));
  // Visible card fields only: many shops keep a hidden payment modal in the page from the start.
  // Includes hosted card iframes (Stripe, Razorpay, Adyen...), which name or title themselves after the card.
  const hasCard = [...document.querySelectorAll(
    'input[autocomplete^="cc-" i],input[name*="card" i],input[id*="card" i],input[name*="cvv" i],input[name*="cvc" i],' +
    'input[placeholder*="card number" i],input[aria-label*="card number" i],iframe[name*="card" i],iframe[title*="card" i]')].some(vis);
  const anyVis = sel => [...document.querySelectorAll(sel)].some(vis);
  const body = ((document.body && document.body.innerText) || '');
  const head = document.title + ' ' + body.slice(0, 1500);
  // Bot walls: we stop and report them. Pardarshi never tries to get past a CAPTCHA.
  const blocked = /captcha|are you (a )?(human|robot)|verify (that )?you are (a )?human|unusual traffic|access denied|request blocked|pardon our interruption|checking your browser|just a moment\.\.\.|bot protection|403 forbidden/i.test(head)
    || anyVis('iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="challenges.cloudflare"],#px-captcha,#challenge-form');
  // Login / OTP / personal-details walls: the agent never types these, so the run stops here.
  const login = anyVis('input[type=password],input[autocomplete="one-time-code"],input[name*="otp" i],input[id*="otp" i]')
    || (/request otp|send otp|verify (your )?(mobile|phone)|enter (your )?(mobile|phone)( number)?|email\s*\/\s*mobile|mobile\s*\/\s*email|(log ?in|sign ?in) to (continue|proceed|checkout)/i.test(body.slice(0, 4000))
        && anyVis('input[type=tel],input[type=text],input[type=email]'));
  // A payment-method screen (common on Indian checkouts) counts as reaching payment, card form or not.
  const methods = [/\bUPI\b/, /net ?banking/i, /(credit|debit)\s*(\/\s*(credit|debit)\s*)?card/i, /\bwallets?\b/i, /cash on delivery/i, /\bEMI\b/]
    .filter(rx => rx.test(body)).length;
  const payPage = methods >= 2 && /(select|choose)\s+(a\s+)?payment\s+(method|option|mode)|payment\s+(options|methods|mode)|pay\s+(using|with|via)/i.test(body);
  const h1 = [...document.querySelectorAll('h1')].find(vis);
  const og = document.querySelector('meta[property="og:title"]');
  const heading = clean(h1 ? h1.innerText : (og ? og.content : '')).slice(0, 200);
  return {title: document.title, url: location.href, heading, text: body.slice(0, 8000), wall: blocked ? 'bot' : login ? 'login' : '', payPage,
          segments: segs, checked, strikes, hasCard};
}
"""

TIMER_JS = r"""
() => { const m = ((document.body && document.body.innerText) || '').match(/\b\d{1,2}:\d{2}(?::\d{2})?\b/g); return m || []; }
"""


def _secs(t: str) -> int:
    parts = [int(x) for x in t.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def link_preselected(items: list[PriceItem], checked_labels: list[str]) -> None:
    """Mark add-on price items whose label matches the text of a pre-ticked checkbox."""
    low = [c.lower() for c in checked_labels]
    for it in items:
        if it.kind not in ("addon", "other"):
            continue
        words = [w for w in re.findall(r"[a-z]{5,}", it.label.lower())]
        if words and any(any(w in c for w in words) for c in low):
            it.preselected = True


async def capture_step(
    page,
    *,
    name: str,
    index: int,
    clicks: int,
    shots_dir: Path,
    rel_root: Path,
    check_timer_reload: bool = False,
) -> tuple[Step, bool]:
    """Snapshot the current page. Returns (step, looks_like_payment_form)."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await page.wait_for_load_state("networkidle", timeout=4000)
    except Exception:
        pass
    data = await page.evaluate(COLLECT_JS)
    signals: list[Signal] = []

    timers1 = await page.evaluate(TIMER_JS)
    if timers1:
        await asyncio.sleep(1.3)
        timers2 = await page.evaluate(TIMER_JS)
        if timers2 and _secs(timers2[0]) < _secs(timers1[0]):
            signals.append(Signal(kind="countdown", text=timers2[0], detail={"first": timers1[0], "second": timers2[0]}))
            if check_timer_reload:
                await page.reload()
                await page.wait_for_load_state("domcontentloaded")
                timers3 = await page.evaluate(TIMER_JS)
                if timers3 and _secs(timers3[0]) > _secs(timers2[0]):
                    signals.append(Signal(kind="timer_resets", text=timers3[0],
                                          detail={"before_reload": timers2[0], "after_reload": timers3[0]}))
                # Re-read the reloaded page: the recorded text and prices must match the screenshot below
                # and the page the agent carries on from (a reload can change what is shown).
                data = await page.evaluate(COLLECT_JS)

    for c in data["checked"]:
        signals.append(Signal(kind="preselected", text=c))
    for s in data["strikes"]:
        signals.append(Signal(kind="strikethrough", text=s))

    shot_path = shots_dir / f"{uuid.uuid4().hex[:10]}_{index}_{name}.jpg"
    try:
        await page.screenshot(path=str(shot_path), full_page=True, type="jpeg", quality=55)
        shot_rel = shot_path.relative_to(rel_root).as_posix()
    except Exception:
        shot_rel = None

    items = extract_items(data["text"])
    link_preselected(items, data["checked"])
    step = Step(
        index=index, name=name, url=data["url"], title=data["title"], heading=data.get("heading", ""),
        text=data["text"], screenshot=shot_rel,
        prices=items, signals=signals, segments=[Segment(**s) for s in data["segments"]], clicks_so_far=clicks,
        note={"bot": "bot_wall", "login": "login_wall"}.get(data.get("wall"), ""),
    )
    return step, bool(data["hasCard"] or data.get("payPage"))


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()
