"""Same product for every shopper: which product each run priced, pinning it, and leaving out runs that
priced a different item (a price gap between two different products says nothing about the shopper)."""
from app import assistant
from app.analysis import analyze, product_of, same_product
from app.capture.schema import Persona, PriceItem, RunRecord, Step
from app.capture.store import AuditStore
from app.ml.pattern_clf import get_classifier
from app.pipeline import run_audit
from app.providers.llm_agent import finish_run

IPHONE = Persona(id="iphone", device="iphone")


def _run(rid, product, adv, final, persona=Persona(id="base"), url=None):
    steps = [Step(index=0, name="step0", heading=product, url=url or f"https://shop.in/p/{product.lower().replace(' ', '-')}",
                  text=f"{product}\n₹{adv}"),
             Step(index=1, name="step1", prices=[PriceItem(label="Total", amount=final, kind="total")])]
    return RunRecord(run_id=rid, audit_id="a", site="shop.in", persona=persona, provider="opensource", steps=steps,
                     advertised_price=adv, final_price=final, stopped_at="price_screen",
                     meta={"advertised_step": 0, "price_step": 1})


def test_product_is_read_from_the_advertised_price_page():
    p = product_of(_run("r", "Blue Kurta", 999, 1099))
    assert p["name"] == "Blue Kurta" and p["url"].endswith("/p/blue-kurta")


def test_a_different_product_is_left_out_of_price_comparisons():
    runs = [_run("a", "Blue Kurta", 999, 1099), _run("b", "Blue Kurta", 999, 1099),
            _run("c", "Red Saree", 2499, 2599, IPHONE)]
    keep, summary, note = same_product(runs)
    assert [r.run_id for r in keep] == ["a", "b"]
    assert [(s["name"], s["runs"], s["used"]) for s in summary] == [("Blue Kurta", 2, True), ("Red Saree", 1, False)]
    assert "1 run(s) priced a different product" in note
    rep = analyze("a", "shop.in", "opensource", runs, get_classifier())
    assert rep.products[0]["name"] == "Blue Kurta" and any("different product" in n for n in rep.notes)
    # the iPhone run priced another item, so it must not create a fake "iPhone users pay more" finding
    assert not any(f.id == "personalization" for f in rep.findings)


def test_same_product_everywhere_needs_no_note():
    keep, summary, note = same_product([_run("a", "Blue Kurta", 999, 1099), _run("b", "Blue kurta ", 999, 1099, IPHONE)])
    assert len(keep) == 2 and note is None and summary[0]["runs"] == 2


def test_pinned_product_page_counts_its_own_price():
    run = RunRecord(run_id="r", audit_id="a", site="s", persona=Persona(id="base"), provider="opensource")
    steps = [Step(index=0, name="step0", heading="Blue Kurta", text="Blue Kurta ₹999"),
             Step(index=1, name="step1", prices=[PriceItem(label="Total", amount=1099, kind="total")])]
    run.steps = steps
    finish_run(run, steps, False, "login_wall", first_index=0)  # started on the product page itself
    assert (run.advertised_price, run.final_price, run.stopped_at) == (999, 1099, "price_screen")


async def test_every_shopper_is_pinned_to_the_baseline_product(tmp_path):
    seen, events = [], []

    class Agent:
        name = "opensource"

        def supports_device(self, spec):
            return True

        async def run_flow(self, spec, persona, audit_id, repeat):
            seen.append((persona.id, repeat, spec.product_url))
            return _run(f"{persona.id}{repeat}", "Blue Kurta", 999, 1099, persona, url="http://127.0.0.1:8100/p/blue-kurta")

    class P:
        name, agent = "opensource", Agent()

        async def aclose(self):
            pass

    rep = await run_audit("shop.in", providers=P(), store=AuditStore(tmp_path), repeats=2, with_policy=False,
                          personas=[Persona(id="base"), IPHONE], on_event=events.append)
    assert seen[0] == ("base", 0, None)  # the pilot picks the product
    assert all(url == "http://127.0.0.1:8100/p/blue-kurta" for _, _, url in seen[1:]) and len(seen) == 4
    assert any(e["type"] == "pinned" and e["product"]["name"] == "Blue Kurta" for e in events)
    assert all(e.get("product", {}).get("name") == "Blue Kurta" for e in events if e["type"] == "run")
    assert rep.products == [{"name": "Blue Kurta", "url": "http://127.0.0.1:8100/p/blue-kurta", "runs": 4,
                             "personas": ["IN/desktop/new", "IN/iphone/new"], "used": True}]  # real site: Indian shoppers


def test_assistant_names_the_product():
    rep = analyze("a", "shop.in", "opensource", [_run("a", "Blue Kurta", 999, 1099), _run("c", "Red Saree", 2499, 2599, IPHONE)],
                  get_classifier())
    t = assistant.local_answer(rep.model_dump(mode="json"), "Which product did you check?")["text"]
    assert "Blue Kurta" in t and "Red Saree" in t and "left out" in t


def test_real_sites_shop_as_a_local_indian_shopper():
    from app.personas import BASELINE, default_matrix, localise
    from app.providers.opensource import LOCALES
    loc = localise(default_matrix(), "IN")
    assert {p.country for p in loc} == {"IN", "DE", "JP"}  # foreign variations stay foreign
    assert [p.id for p in loc] == [p.id for p in default_matrix()]
    assert BASELINE.country == "US"  # the demo shop's matrix is unchanged
    assert LOCALES["IN"] == ("en-IN", "Asia/Kolkata")
