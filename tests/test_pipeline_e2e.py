"""Full pipeline on the demo shop: browser personas -> ML + stats -> report. No Gemini needed."""
import pytest

from app.capture.store import AuditStore
from app.pipeline import run_audit
from app.providers import get_providers


@pytest.fixture
def patch_shop(shop_url, monkeypatch):
    # point the pipeline at the test shop instead of the default port
    from app import config, pipeline
    from app.providers import opensource

    new = config.Settings.__new__(config.Settings)
    object.__setattr__(new, "__dict__", {**config.settings.__dict__, "demo_shop_url": shop_url})
    monkeypatch.setattr(config, "settings", new)
    for mod in (pipeline, opensource):
        monkeypatch.setattr(mod, "settings", new)


async def _audit(site, tmp_path, events=None):
    store = AuditStore(tmp_path)
    providers = get_providers("opensource", store)
    try:
        return await run_audit(site, providers=providers, store=store, repeats=3, with_policy=False,
                               on_event=events.append if events is not None else None)
    finally:
        await providers.aclose()


async def test_dirty_shop_is_flagged(patch_shop, tmp_path):
    events = []
    rep = await _audit("dirty", tmp_path, events)
    ids = {f.id for f in rep.findings}
    assert {"drip_pricing", "basket_sneaking", "personalization", "friction", "pattern:false_urgency"} <= ids
    assert rep.trust_score < 35
    sig = {(e["factor"], e["level"]) for e in next(f for f in rep.findings if f.id == "personalization").metrics["effects"]}
    assert sig == {("device", "iphone"), ("country", "DE"), ("user_state", "returning")}
    assert all(e.screenshot for f in rep.findings[:3] for e in f.evidence)
    # live progress: the job list up front, then per-step and per-run events that name a known job
    keys = {j["key"] for j in events[0]["jobs"]}
    steps = [e for e in events if e["type"] == "step"]
    runs = [e for e in events if e["type"] == "run"]
    assert events[0]["type"] == "started" and len(keys) == events[0]["total"] == len(runs)
    assert steps and {e["key"] for e in steps} <= keys and {e["key"] for e in runs} == keys
    assert any(e["step"] == "payment" for e in steps)
    # the evidence exhibit uses one real run: its own prices and both of its screenshots
    run = rep.waterfalls[0]["run"]
    assert run["first_screenshot"] and rep.waterfalls[0]["screenshot"] and run["final"] > run["advertised"]
    assert abs(sum(i["amount"] for i in rep.waterfalls[0]["items"]) - run["final"]) < 1
    # the printable report's paperwork: every cited screenshot is found and hashed
    from app import dossier
    d = dossier.build(rep, AuditStore(tmp_path))
    assert len(d["files"]) > 2 and all(f["sha256"] for f in d["files"])


async def test_clean_shop_is_clean(patch_shop, tmp_path):
    rep = await _audit("clean", tmp_path)
    assert rep.findings == [], [f.title for f in rep.findings]
    assert rep.trust_score == 100.0


async def test_audit_with_no_completed_run_is_inconclusive_not_perfect(tmp_path, monkeypatch):
    """A site the agent cannot get through (login wall, block page) must not come back as 100/100."""
    from app.capture.schema import RunRecord
    from app.pipeline import AuditInconclusive

    class Blocked:
        name = "opensource"

        async def run_flow(self, spec, persona, audit_id, repeat):
            return RunRecord(run_id=f"r{repeat}", audit_id=audit_id, site=spec.site, persona=persona,
                             provider="opensource", error="login required")

    class P:
        name, agent = "opensource", Blocked()

        async def aclose(self):
            pass

    store = AuditStore(tmp_path)
    with pytest.raises(AuditInconclusive, match="login required"):
        await run_audit("dirty", providers=P(), store=store, repeats=1, flows=("purchase",), with_policy=False)
    assert store.list_reports() == []


async def test_own_session_runs_one_shopper_once_and_says_so(tmp_path):
    """A signed-in account means one shared cart: one shopper, one run, and the report must say so."""
    from app.capture.schema import PriceItem, RunRecord, Step

    seen = []

    class Agent:
        name = "opensource"

        async def run_flow(self, spec, persona, audit_id, repeat):
            seen.append((spec.session_file, persona.id, repeat))
            steps = [Step(index=0, name="step0", text="Phone ₹999"),
                     Step(index=1, name="step1", prices=[PriceItem(label="Total", amount=1049, kind="total")])]
            return RunRecord(run_id="r1", audit_id=audit_id, site=spec.site, persona=persona, provider="opensource",
                             steps=steps, advertised_price=999, final_price=1049, stopped_at="price_screen",
                             meta={"price_step": 1, "advertised_step": 0})

    class P:
        name, agent = "opensource", Agent()

        async def aclose(self):
            pass

    rep = await run_audit("shop.example.com", providers=P(), store=AuditStore(tmp_path), repeats=3,
                          with_policy=False, session_file="sess.json")
    assert seen == [("sess.json", "base", 0)]
    assert any("own signed-in session" in n for n in rep.notes)
    assert any("login or personal-details step" in n for n in rep.notes)
    drip = next(f for f in rep.findings if f.id == "drip_pricing")
    assert drip.title.endswith("the checkout summary")
