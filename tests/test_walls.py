"""Real-site handling: login and bot walls, payment-method screens, the pay-button guard, and how a goal-agent
run is judged when it cannot reach payment. Page detection runs in a real browser on small synthetic pages."""
import asyncio

import pytest
from playwright.async_api import async_playwright

from app.capture.schema import Persona, PriceItem, RunRecord, Step
from app.providers.dom import capture_step
from app.providers.llm_agent import PAY_BUTTON, finish_run
from app.providers.tinyfish import parse_agent_result

PAGES = {
    "login": "<h1>Login</h1><p>Enter Email/Mobile number</p><input type=text><button>Request OTP</button>",
    "password": "<h1>Sign in</h1><input type=email><input type=password>",
    "captcha": "<title>Just a moment...</title><p>Checking your browser before accessing the site.</p>",
    "denied": "<h1>Access Denied</h1><p>You don't have permission to access this server.</p>",
    "paymethods": "<h2>Select a payment method</h2><ul><li>UPI</li><li>Credit / Debit Card</li><li>Net Banking</li>"
                  "<li>Cash on Delivery</li></ul><p>Total ₹1,299</p>",
    "hidden_card": "<p>Cart</p><p>Total: 360</p><div style='display:none'><input id=card></div><button>Place Order</button>",
    "visible_card": "<p>Total: 360</p><label>Credit card <input id=card></label>",
    "product_offers": "<h1>Phone</h1><p>₹12,999</p><p>Bank offers: UPI cashback. No cost EMI. Cash on delivery available.</p>"
                      "<p>Footer: subscribe <input type=email placeholder='Your email'></p>",
}


@pytest.fixture(scope="module")
def captured(tmp_path_factory):
    d = tmp_path_factory.mktemp("walls")

    async def go():
        out = {}
        async with async_playwright() as pw:
            b = await pw.chromium.launch()
            page = await b.new_page()
            for name, html in PAGES.items():
                await page.set_content(f"<html><head><meta charset=utf-8></head><body>{html}</body></html>")
                out[name] = await capture_step(page, name=name, index=0, clicks=0, shots_dir=d, rel_root=d)
            await b.close()
        return out

    return asyncio.run(go())  # one browser for all cases


@pytest.mark.parametrize("name,note,at_payment", [
    ("login", "login_wall", False), ("password", "login_wall", False),
    ("captcha", "bot_wall", False), ("denied", "bot_wall", False),
    ("paymethods", "", True), ("visible_card", "", True),
    ("hidden_card", "", False),       # a payment modal hidden in the page is not payment
    ("product_offers", "", False),    # offer text on a product page is not a payment screen, a footer email is not a wall
])
def test_page_detection(captured, name, note, at_payment):
    step, has_payment = captured[name]
    assert step.note == note
    assert has_payment is at_payment


@pytest.mark.parametrize("label,blocked", [
    ("Pay ₹1,305", True), ("Pay now", True), ("Proceed to Pay", True), ("Confirm order", True),
    ("Place order and pay", True), ("Subscribe", True), ("Start membership", True),
    ("Place Order", False), ("Buy now", False), ("Add to cart", False), ("Payment options", False),
    ("Subscribe to newsletter", False), ("Book now", False),
])
def test_pay_button_guard(label, blocked):
    assert bool(PAY_BUTTON.search(label)) is blocked


def _step(i, text="", total=None, note=""):
    prices = [PriceItem(label="Total", amount=total, kind="total")] if total else []
    return Step(index=i, name=f"step{i}", text=text, prices=prices, note=note)


def _run():
    return RunRecord(run_id="r", audit_id="a", site="s", persona=Persona(id="base"), provider="opensource")


def test_login_wall_after_a_cart_total_is_a_measured_price_screen():
    run, steps = _run(), [_step(0, "Home"), _step(1, "Phone ₹999"), _step(2, "Cart", total=1049), _step(3, "Login", note="login_wall")]
    run.steps = steps  # the agent attaches its steps before judging the run
    finish_run(run, steps, hit_payment_form=False, stop_reason="login_wall")
    assert (run.advertised_price, run.final_price, run.stopped_at) == (999, 1049, "price_screen")
    assert run.ok and not run.reached_end and run.price_step().index == 2


def test_login_wall_before_any_total_is_not_measured():
    run = _run()
    finish_run(run, [_step(0, "Home"), _step(1, "Phone ₹999"), _step(2, "Login", note="login_wall")], False)
    assert run.stopped_at == "login" and not run.ok


def test_bot_wall_is_an_error_never_a_result():
    run = _run()
    finish_run(run, [_step(0, "Just a moment", note="bot_wall")], False)
    assert run.stopped_at == "blocked" and "bot protection" in run.error and not run.ok


def test_payment_screen_reached():
    run = _run()
    finish_run(run, [_step(0, "Home"), _step(1, "Phone ₹999"), _step(2, "Pay", total=1099)], True)
    assert run.reached_end and run.stopped_at == "payment" and run.ok


def test_tinyfish_reports_login_and_blocked():
    base = {"advertised_price": 999, "final_payable_total": 1049, "currency": "INR", "reached_payment_page": False,
            "steps": [{"name": "product", "price_lines": [{"label": "Price", "amount": 999}], "visible_texts": []},
                      {"name": "cart", "price_lines": [{"label": "Total", "amount": 1049}], "visible_texts": []}]}
    run = _run(); parse_agent_result({**base, "stopped_at": "login"}, run)
    assert run.stopped_at == "price_screen" and run.ok
    run = _run(); parse_agent_result({**base, "stopped_at": "blocked"}, run)
    assert run.stopped_at == "blocked" and not run.ok


async def test_blocked_site_reports_evidence_and_never_scores(tmp_path):
    """Every shopper blocked: an 'inconclusive' event with the block page as evidence, no report, no score."""
    from app.capture.store import AuditStore
    from app.pipeline import AuditInconclusive, run_audit

    order, events = [], []

    class Agent:
        name = "opensource"

        async def run_flow(self, spec, persona, audit_id, repeat):
            order.append(("start", repeat, spec.polite))
            run = _run()
            run.audit_id, run.stopped_at = audit_id, "blocked"
            run.error = "Blocked by the site's bot protection"
            run.steps = [Step(index=0, name="step0", note="bot_wall", screenshot=f"{audit_id}/shots/block{repeat}.jpg")]
            order.append(("end", repeat))
            return run

    class P:
        name, agent = "opensource", Agent()

        async def aclose(self):
            pass

    store = AuditStore(tmp_path)
    with pytest.raises(AuditInconclusive, match="does not get around bot protection"):
        await run_audit("shop.example.com", providers=P(), store=store, repeats=3, with_policy=False,
                        personas=[Persona(id="base")], on_event=events.append)
    inc = next(e for e in events if e["type"] == "inconclusive")
    assert inc["reason"] == "blocked" and len(inc["screenshots"]) == 3 and inc["runs"] == 3
    assert store.list_reports() == []
    # polite mode on a real site: each shopper finishes before the next one starts
    assert order == [("start", 0, True), ("end", 0), ("start", 1, True), ("end", 1), ("start", 2, True), ("end", 2)]


async def test_runs_are_interleaved_by_round_so_time_cannot_pose_as_personalisation(tmp_path):
    """Sequential (polite) runs go round by round across profiles, not profile after profile."""
    from app.capture.store import AuditStore
    from app.pipeline import run_audit

    order = []

    class Agent:
        name = "opensource"

        def supports_device(self, spec):
            return True

        async def run_flow(self, spec, persona, audit_id, repeat):
            order.append((persona.id, repeat))
            run = _run()
            run.audit_id, run.persona = audit_id, persona
            steps = [Step(index=0, name="step0", heading="Kurta", url="https://shop.in/p/k", text="Kurta ₹999"),
                     Step(index=1, name="step1", prices=[PriceItem(label="Total", amount=1049, kind="total")])]
            run.steps, run.advertised_price, run.final_price, run.stopped_at = steps, 999, 1049, "price_screen"
            run.meta.update(advertised_step=0, price_step=1)
            return run

    class P:
        name, agent = "opensource", Agent()

        async def aclose(self):
            pass

    personas = [Persona(id="base"), Persona(id="iphone", device="iphone"), Persona(id="android", device="android")]
    await run_audit("shop.in", providers=P(), store=AuditStore(tmp_path), repeats=3, with_policy=False, personas=personas)
    rounds = [r for _, r in order]
    assert rounds == sorted(rounds)  # every profile's run 0, then run 1, then run 2
    assert len(order) == 9 and {p for p, _ in order} == {"base", "iphone", "android"}
