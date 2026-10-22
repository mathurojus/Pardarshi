"""Demo ticket shop with a KNOWN set of injected dark patterns and persona-based pricing.

Two profiles share one codebase:
  /dirty  "TicketNest"  drip fees, fake urgency, pre-ticked add-ons, confirm-shaming,
                        a buried cancellation maze, and price bias by device/country/returning user
  /clean  "FairTix"     all-in upfront price, no pressure copy, two-click cancellation, no bias

Because the truth is known (see /__truth/<profile>), the audit pipeline can be scored
for precision and recall. Payments are never processed: /pay always refuses.

Persona signals the shop reads (all server-side, nothing is spoofable-by-design):
  device   : User-Agent (iPhone / Android / desktop)
  country  : X-Demo-Country (local tests) > CF-IPCountry (Cloudflare tunnel) > US
  returning: presence of the 'seen' cookie when the request arrives
"""
from __future__ import annotations

import os
import random

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, DictLoader, select_autoescape

BASE_PRICE = 999
MEMBERSHIP_PRICE = 199
INSURANCE_PRICE = 149
_rng = random.Random(int(os.getenv("DEMO_SEED", "0")) or None)

PROFILES: dict[str, dict] = {
    "dirty": {
        "name": "TicketNest",
        "honest": False,
        "bias": {"device": {"iphone": 0.10, "android": 0.0}, "country": {"DE": 0.06}, "returning": 0.04},
        "truth_patterns": [
            {"category": "false_urgency", "text": "Hurry! Offer ends in"},
            {"category": "scarcity", "text": "Only 2 tickets left at this price!"},
            {"category": "fake_social_proof", "text": "37 people are viewing this event right now"},
            {"category": "fake_social_proof", "text": "128 tickets sold in the last hour"},
            {"category": "preselected_addon", "text": "Add Ticket Protect"},
            {"category": "confirmshaming", "text": "No thanks, I'd rather risk losing my tickets"},
            {"category": "confirmshaming", "text": "Yes, I don't care about saving money"},
            {"category": "nagging", "text": "Wait! Don't miss out."},
            {"category": "subscription_trap", "text": "End membership"},
        ],
        "truth_computed": ["drip_pricing", "basket_sneaking", "personalization", "friction"],
    },
    "clean": {
        "name": "FairTix",
        "honest": True,
        "bias": {"device": {}, "country": {}, "returning": 0.0},
        "truth_patterns": [],
        "truth_computed": [],
    },
}

CSS = """
*{box-sizing:border-box}body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#f6f7f9;color:#1b1f24}
header{background:#14213d;color:#fff;padding:14px 20px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
header a{color:#fff;text-decoration:none;opacity:.9}header .brand{font-weight:700;font-size:20px;margin-right:auto}
main{max-width:760px;margin:24px auto;padding:0 16px}
.card{background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.price{font-size:34px;font-weight:700}.price del{font-size:18px;color:#888;margin-left:10px}
.row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #eee}
.row.total{font-weight:700;border-bottom:0;font-size:18px}
.btn{display:inline-block;background:#1f7a3d;color:#fff;border:0;border-radius:8px;padding:12px 22px;font-size:16px;cursor:pointer;text-decoration:none}
.btn.big{padding:16px 34px;font-size:19px}
a.tiny{font-size:11px;color:#9a9a9a}
.warn{color:#b3261e;font-weight:600}.muted{color:#6a737d;font-size:13px}
input[type=text],select{padding:9px;border:1px solid #ccc;border-radius:6px;width:100%;margin:4px 0 10px}
"""

BASE = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{% block title %}{{ shop.name }}{% endblock %}</title><style>""" + CSS + """</style></head><body>
<header><a class=brand href="/{{ p }}/">{{ shop.name }}</a><a href="/{{ p }}/">Events</a><a href="/{{ p }}/plus">{{ shop.name }} Plus</a><a href="/{{ p }}/account">Account</a></header>
<main>{% block body %}{% endblock %}</main></body></html>"""

T: dict[str, str] = {"base.html": BASE}

T["home.html"] = """{% extends "base.html" %}{% block body %}
<div class=card><h1>Upcoming events</h1>
{% for e in events %}<div class=row><span><a href="/{{ p }}/event/{{ e.id }}">{{ e.name }}</a> <span class=muted>{{ e.when }}</span></span><span>{{ e.teaser }}</span></div>{% endfor %}
</div>{% endblock %}"""

T["event.html"] = """{% extends "base.html" %}{% block body %}
<div class=card><h1>{{ event.name }}</h1><p class=muted>{{ event.when }} &middot; {{ event.venue }}</p>
<div class=price data-role=headline>{{ money(headline) }}{% if not shop.honest %}<del>{{ money(was) }}</del>{% endif %}</div>
{% if shop.honest %}<p class=muted>All-in price per ticket. Fees and taxes included.</p>
{% else %}<p class=muted>per ticket</p>
<p class=warn>Only 2 tickets left at this price!</p>
<p>37 people are viewing this event right now</p>
<p>128 tickets sold in the last hour</p>
<p class=warn>Hurry! Offer ends in <span id=t>09:59</span></p>
<script>let s=599;const el=document.getElementById('t');setInterval(()=>{s=Math.max(0,s-1);el.textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0')},1000)</script>
{% endif %}
<form method=post action="/{{ p }}/cart"><input type=hidden name=base value="{{ base }}"><input type=hidden name=event value="{{ event.id }}">
<button class="btn big" type=submit>{% if shop.honest %}Select tickets{% else %}Book now{% endif %}</button></form></div>{% endblock %}"""

T["cart.html"] = """{% extends "base.html" %}{% block body %}
<div class=card><h1>Your order</h1><p class=muted>{{ event.name }}</p>
<div class=row><span>Ticket x 1</span><span>{{ money(base) }}</span></div>
{% for label, amt in fees %}<div class=row><span>{{ label }}</span><span>{{ money(amt) }}</span></div>{% endfor %}
{% if insurance %}<div class=row><span>Ticket Protect insurance</span><span>{{ money(insurance_price) }}</span></div>{% endif %}
<div class="row total"><span>Total</span><span>{{ money(total) }}</span></div></div>
<form method=post action="/{{ p }}/checkout" class=card><input type=hidden name=base value="{{ base }}"><input type=hidden name=event value="{{ event.id }}">
<label><input type=checkbox name=insurance value=1 {% if not shop.honest %}checked{% endif %}> Add Ticket Protect for {{ money(insurance_price) }} &ndash; cover your tickets if plans change</label>
{% if not shop.honest %}<p><a class=tiny href="/{{ p }}/decline?base={{ base }}&event={{ event.id }}">No thanks, I'd rather risk losing my tickets</a></p>
<label><input type=checkbox name=news value=1 checked> Yes, send me exclusive offers and partner promotions</label>{% endif %}
<p><button class="btn big" type=submit>Continue to payment</button></p></form>{% endblock %}"""

T["payment.html"] = """{% extends "base.html" %}{% block body %}
<div class=card><h1>Payment</h1>
<div class=row><span>Ticket x 1</span><span>{{ money(base) }}</span></div>
{% for label, amt in fees %}<div class=row><span>{{ label }}</span><span>{{ money(amt) }}</span></div>{% endfor %}
{% if insurance %}<div class=row><span>Ticket Protect insurance</span><span>{{ money(insurance_price) }}</span></div>{% endif %}
<div class="row total"><span>Total payable</span><span>{{ money(total) }}</span></div></div>
<form class=card method=post action="/{{ p }}/pay"><label>Card number<input type=text name=card autocomplete=off placeholder="0000 0000 0000 0000"></label>
<label>Expiry<input type=text name=exp placeholder="MM/YY"></label><label>CVV<input type=text name=cvv placeholder="123"></label>
<button class="btn big" type=submit>Pay {{ money(total) }}</button><p class=muted>Demo shop: no payment is ever processed.</p></form>{% endblock %}"""

T["plus.html"] = """{% extends "base.html" %}{% block body %}
<div class=card><h1>{{ shop.name }} Plus</h1><p>Zero booking fees, early access and member prices.</p>
<p class=price>{{ money(mprice) }}<span class=muted> / month</span></p><p>7-day free trial.</p>
<form method=post action="/{{ p }}/plus/start"><button class="btn big" type=submit>Start free trial</button></form>
{% if not shop.honest %}<p class=muted style="font-size:10px;color:#aaa">After the trial your membership renews automatically at {{ money(mprice) }}/month until cancelled.</p>
{% else %}<p class=muted>After the trial it renews at {{ money(mprice) }}/month. Cancel anytime from your account in two clicks.</p>{% endif %}</div>{% endblock %}"""

T["account.html"] = """{% extends "base.html" %}{% block body %}
<div class=card><h1>My account</h1>
{% if member %}<p>You are a {{ shop.name }} Plus member.</p>{% else %}<p>You are not a member yet.</p>{% endif %}
{% if shop.honest %}{% if member %}<p><a href="/{{ p }}/cancel">Cancel membership</a></p>{% endif %}
{% else %}<p><a href="/{{ p }}/orders">Orders</a> &middot; <a href="/{{ p }}/settings">Settings</a> &middot; <a href="/{{ p }}/help">Help</a></p>{% endif %}</div>{% endblock %}"""

T["page.html"] = """{% extends "base.html" %}{% block body %}<div class=card><h1>{{ heading }}</h1>{{ body|safe }}</div>{% endblock %}"""

EVENTS = [
    {"id": 1, "name": "Indie Rock Night", "when": "Sat 8 Nov, 7pm", "venue": "Riverside Arena", "teaser": "Live music"},
    {"id": 2, "name": "Stand-up Special", "when": "Sun 9 Nov, 6pm", "venue": "City Hall", "teaser": "Comedy"},
    {"id": 3, "name": "Jazz in the Park", "when": "Fri 14 Nov, 5pm", "venue": "Central Park", "teaser": "Open air"},
    {"id": 4, "name": "Classical Evening", "when": "Sat 15 Nov, 7pm", "venue": "Opera House", "teaser": "Orchestra"},
    {"id": 5, "name": "Food and Music Fest", "when": "Sun 16 Nov, 12pm", "venue": "Harbour Lawn", "teaser": "Street food"},
]
EVENT_PRICE = {1: 1.0, 2: 0.8, 3: 1.2, 4: 1.5, 5: 0.9}  # each event has its own base price

env = Environment(loader=DictLoader(T), autoescape=select_autoescape(["html"]))


def money(x: float) -> str:
    return f"₹{int(round(x)):,}"


def _shop(profile: str) -> dict:
    return PROFILES[profile]


def detect(request: Request) -> dict:
    ua = request.headers.get("user-agent", "")
    device = "iphone" if "iPhone" in ua else "android" if "Android" in ua else "desktop"
    country = (
        request.headers.get("x-demo-country")
        or request.headers.get("cf-ipcountry")
        or request.headers.get("x-vercel-ip-country")
        or "US"
    ).upper()
    # 'seen' counts home-page visits: a first-time visitor has seen it once when they reach an event.
    try:
        visits = int(request.cookies.get("seen", "0"))
    except ValueError:
        visits = 0
    return {"device": device, "country": country, "returning": visits >= 2}


def personalised_base(shop: dict, who: dict) -> float:
    bias = shop["bias"]
    mult = 1.0 + bias["device"].get(who["device"], 0.0) + bias["country"].get(who["country"], 0.0)
    mult += bias["returning"] if who["returning"] else 0.0
    return BASE_PRICE * mult * _rng.uniform(0.985, 1.015)  # +/-1.5% 'dynamic pricing' noise on every site


def fee_lines(shop: dict, base: float) -> list[tuple[str, float]]:
    return [
        ("Convenience fee", round(base * 0.08)),
        ("Service fee", round(base * 0.04)),
        ("Processing fee", 49),
    ]


def render(name: str, profile: str, **ctx) -> HTMLResponse:
    shop = _shop(profile)
    return HTMLResponse(env.get_template(name).render(shop=shop, p=profile, money=money, **ctx))


app = FastAPI(title="Demo shop (ground-truth dark patterns)")


@app.get("/__truth/{profile}")
def truth(profile: str):
    shop = _shop(profile)
    return JSONResponse({"profile": profile, "patterns": shop["truth_patterns"], "computed": shop["truth_computed"], "bias": shop["bias"]})


@app.get("/")
def root():
    return RedirectResponse("/dirty/")


@app.get("/{profile}/", response_class=HTMLResponse)
def home(profile: str, request: Request):
    resp = render("home.html", profile, events=EVENTS)
    try:
        visits = int(request.cookies.get("seen", "0"))
    except ValueError:
        visits = 0
    resp.set_cookie("seen", str(visits + 1), max_age=3600)
    return resp


@app.get("/{profile}/event/{eid}", response_class=HTMLResponse)
def event(profile: str, eid: int, request: Request):
    shop = _shop(profile)
    ev = next(e for e in EVENTS if e["id"] == eid)
    base = round(personalised_base(shop, detect(request)) * EVENT_PRICE.get(eid, 1.0))
    if shop["honest"]:
        headline = base + sum(a for _, a in fee_lines(shop, base))
    else:
        headline = base
    return render("event.html", profile, event=ev, base=base, headline=headline, was=base * 1.5)


def _order(profile: str, base: float, insurance: bool, event_id: int):
    shop = _shop(profile)
    ev = next(e for e in EVENTS if e["id"] == event_id)
    fees = fee_lines(shop, base)
    total = base + sum(a for _, a in fees) + (INSURANCE_PRICE if insurance else 0)
    return dict(event=ev, base=base, fees=fees, insurance=insurance, insurance_price=INSURANCE_PRICE, total=total)


@app.post("/{profile}/cart", response_class=HTMLResponse)
def cart(profile: str, base: float = Form(...), event: int = Form(1)):
    shop = _shop(profile)
    return render("cart.html", profile, **_order(profile, base, insurance=not shop["honest"], event_id=event))


@app.get("/{profile}/decline", response_class=HTMLResponse)
def decline(profile: str, base: float, event: int = 1):
    return render("cart.html", profile, **_order(profile, base, insurance=False, event_id=event))


@app.post("/{profile}/checkout", response_class=HTMLResponse)
def checkout(profile: str, base: float = Form(...), event: int = Form(1), insurance: str | None = Form(None)):
    return render("payment.html", profile, **_order(profile, base, insurance=bool(insurance), event_id=event))


@app.post("/{profile}/pay")
def pay(profile: str):
    return JSONResponse({"error": "Demo shop: payments are disabled."}, status_code=403)


TERMS = {
    "dirty": ("<h2>Our promises</h2><ul><li>All fees are shown upfront before you choose your tickets.</li>"
              "<li>You can cancel your membership anytime, online, in a couple of clicks.</li>"
              "<li>We charge the same price to every customer.</li></ul>"),
    "clean": ("<h2>Our promises</h2><ul><li>The price you see is the price you pay: fees and taxes included.</li>"
              "<li>Cancel your membership in two clicks from your account.</li>"
              "<li>We charge the same price to every customer.</li></ul>"),
}


@app.get("/{profile}/terms", response_class=HTMLResponse)
def terms(profile: str):
    return page(profile, "Terms and policies", TERMS[profile])


@app.get("/{profile}/plus", response_class=HTMLResponse)
def plus(profile: str):
    return render("plus.html", profile, mprice=MEMBERSHIP_PRICE)


@app.post("/{profile}/plus/start")
def plus_start(profile: str):
    r = RedirectResponse(f"/{profile}/account", status_code=303)
    r.set_cookie("member", "1", max_age=3600)
    return r


@app.get("/{profile}/account", response_class=HTMLResponse)
def account(profile: str, request: Request):
    return render("account.html", profile, member="member" in request.cookies)


def page(profile: str, heading: str, body: str) -> HTMLResponse:
    return render("page.html", profile, heading=heading, body=body)


# ---- "dirty" cancellation maze (7 clicks) -------------------------------------------------
@app.get("/{profile}/settings", response_class=HTMLResponse)
def settings(profile: str):
    return page(profile, "Settings", f'<p>Profile &middot; Notifications &middot; <a href="/{profile}/membership">Membership</a></p>')


@app.get("/{profile}/orders", response_class=HTMLResponse)
def orders(profile: str):
    return page(profile, "Orders", "<p>No orders yet.</p>")


@app.get("/{profile}/help", response_class=HTMLResponse)
def help_(profile: str):
    return page(profile, "Help", "<p>Browse our help centre.</p>")


@app.get("/{profile}/membership", response_class=HTMLResponse)
def membership(profile: str):
    return page(profile, "Membership", f'<p>Plan: Plus</p><p><a class="btn big" href="/{profile}/membership">Upgrade to Annual and save 20%</a></p><p><a href="/{profile}/billing">Manage billing</a></p>')


@app.get("/{profile}/billing", response_class=HTMLResponse)
def billing(profile: str):
    return page(profile, "Billing", f'<p>Update card &middot; Billing history</p><p><a class=tiny href="/{profile}/retention">End membership</a></p>')


@app.get("/{profile}/retention", response_class=HTMLResponse)
def retention(profile: str):
    return page(profile, "Wait! Don't miss out.", f'<p>Get 3 months at 50% off.</p><p><a class="btn big" href="/{profile}/account">Keep my benefits</a></p><p><a class=tiny href="/{profile}/survey">No thanks, continue</a></p>')


@app.get("/{profile}/survey", response_class=HTMLResponse)
def survey(profile: str):
    return page(profile, "Why are you leaving?", f'<form method=get action="/{profile}/confirm"><select name=why required><option value="">Choose a reason</option><option>Too expensive</option><option>Not using it</option><option>Other</option></select><button class=btn type=submit>Next</button></form>')


@app.get("/{profile}/confirm", response_class=HTMLResponse)
def confirm_dirty(profile: str):
    return page(profile, "Are you sure?", f'<p>You will lose all your exclusive benefits.</p><p><a class="btn big" href="/{profile}/account">Keep benefits</a></p><p><a class=tiny href="/{profile}/done">Yes, I don\'t care about saving money</a></p>')


# ---- "clean" cancellation (2 clicks) ------------------------------------------------------
@app.get("/{profile}/cancel", response_class=HTMLResponse)
def cancel(profile: str):
    return page(profile, "Cancel membership", f'<p>You keep access until the end of the billing period.</p><form method=post action="/{profile}/cancel/confirm"><button class=btn type=submit>Confirm cancellation</button></form>')


@app.post("/{profile}/cancel/confirm")
def cancel_confirm(profile: str):
    r = RedirectResponse(f"/{profile}/done", status_code=303)
    r.delete_cookie("member")
    return r


@app.get("/{profile}/done", response_class=HTMLResponse)
def done(profile: str):
    r = page(profile, "Membership cancelled", "<p>Your membership has been cancelled.</p>")
    r.delete_cookie("member")
    return r


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("DEMO_SHOP_PORT", "8100")), log_level="warning")
