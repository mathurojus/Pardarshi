import random

from app.capture.schema import Persona, RunRecord
from app.ml import personalization
from app.ml.price_extract import classify_kind, drip_ratio, extract_items, final_total, headline_price


def test_extract_items_same_line_and_split_lines():
    text = "Ticket x 1\n₹999\nConvenience fee  ₹80\nTicket Protect insurance ₹149\nTotal\n₹1,277\n"
    items = {i.label: i for i in extract_items(text)}
    assert items["Ticket x 1"].amount == 999 and items["Ticket x 1"].kind == "base"
    assert items["Convenience fee"].kind == "fee"
    assert items["Ticket Protect insurance"].kind == "addon"
    assert final_total(list(items.values())) == 1277


def test_total_payable_preferred_over_total():
    items = extract_items("Total ₹100\nTotal payable ₹120\n")
    assert final_total(items) == 120


def test_discount_and_currencies():
    items = extract_items("Promo code SAVE  -₹50\nSubtotal $1,200.50\n")
    assert items[0].kind == "discount" and items[0].amount == -50
    assert items[1].amount == 1200.5 and items[1].currency == "USD"
    assert headline_price("hello\nOnly €45.00 today")[0] == 45.0


def test_prose_is_not_a_price_row():
    long_line = "Cancel any time within the first 30 days of your membership for a full refund of ₹199 as described in section 4 of the terms"
    assert extract_items(long_line) == []


def test_totals_without_a_currency_symbol():
    # real shops often print the total as a bare number, on the same line or the next one
    assert final_total(extract_items("Products\nTotal\n360\nPlace Order")) == 360
    assert final_total(extract_items("Place order\nTotal: 1,299.50\nName:")) == 1299.5
    assert final_total(extract_items("Subtotal\n2 items\nTotal\n")) is None  # no number, no total
    assert extract_items("Seats left\n12") == []  # a bare number after a non-total label is not a price


def test_kind_and_drip():
    assert classify_kind("Processing fee") == "fee"
    assert classify_kind("GST 18%") == "tax"
    assert round(drip_ratio(1000, 1250), 2) == 0.25
    assert drip_ratio(None, 100) is None


def _runs(bias: dict[str, float], reps=3, noise=0.015, seed=0):
    rnd = random.Random(seed)
    personas = [
        Persona(id="base"),
        Persona(id="iphone", device="iphone"),
        Persona(id="android", device="android"),
        Persona(id="ret", user_state="returning"),
        Persona(id="de", country="DE"),
        Persona(id="jp", country="JP"),
    ]
    out, i = [], 0
    for p in personas:
        for r in range(reps):
            m = 1 + bias.get(p.device, 0) * (p.device != "desktop") + bias.get(p.country, 0) + bias.get(p.user_state, 0)
            price = 999 * m * rnd.uniform(1 - noise, 1 + noise)
            out.append(RunRecord(run_id=str(i), audit_id="t", site="s", persona=p, provider="x", repeat=r,
                                 final_price=price, reached_end=True))
            i += 1
    return out


def test_detects_real_bias_and_ignores_noise():
    res = personalization.analyse(_runs({"iphone": 0.10, "DE": 0.06, "returning": 0.04}))
    sig = {(e["factor"], e["level"]) for e in res["effects"] if e["significant"]}
    assert ("device", "iphone") in sig and ("country", "DE") in sig
    assert ("device", "android") not in sig and ("country", "JP") not in sig


def test_false_positive_rate_is_controlled():
    flagged = 0
    for seed in range(200):
        res = personalization.analyse(_runs({}, seed=seed))
        flagged += res["any_significant"]
    assert flagged / 200 <= 0.08  # alpha=0.05 with BH + a 2% effect floor


def test_too_few_runs_is_graceful():
    res = personalization.analyse(_runs({}, reps=1)[:3])
    assert res["effects"] == [] and "Not enough" in res["note"]
