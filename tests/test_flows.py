import pytest

from app.capture.schema import FlowSpec, Persona
from app.capture.store import AuditStore
from app.providers.opensource import PlaywrightAgent, ensure_allowed


@pytest.fixture
async def agent(tmp_path):
    a = PlaywrightAgent(AuditStore(tmp_path), concurrency=2)
    yield a
    await a.aclose()


def spec(shop_url, profile, flow="purchase"):
    return FlowSpec(site=profile, base_url=f"{shop_url}/{profile}", flow=flow, script="ticketshop")


async def test_dirty_purchase_captures_drip_and_signals(agent, shop_url):
    run = await agent.run_flow(spec(shop_url, "dirty"), Persona(id="base"), "t1", 0)
    assert run.error is None and run.reached_end
    assert run.advertised_price and run.final_price and run.final_price > run.advertised_price * 1.2
    assert [s.name for s in run.steps] == ["event", "cart", "payment"]
    kinds = {sig.kind for s in run.steps for sig in s.signals}
    assert {"countdown", "timer_resets", "preselected", "strikethrough"} <= kinds
    cart = run.steps[1]
    assert any(i.kind == "addon" and i.preselected for i in cart.prices)
    assert any("No thanks" in s.text for s in cart.segments)
    assert run.steps[0].screenshot and (agent.store.root / run.steps[0].screenshot).exists()


async def test_clean_purchase_has_no_drip_and_no_signals(agent, shop_url):
    run = await agent.run_flow(spec(shop_url, "clean"), Persona(id="base"), "t2", 0)
    assert run.reached_end
    assert run.final_price == pytest.approx(run.advertised_price, abs=1)
    assert not any(sig.kind in ("countdown", "preselected") for s in run.steps for sig in s.signals)


async def test_persona_changes_price_on_dirty_only(agent, shop_url):
    base = [await agent.run_flow(spec(shop_url, "dirty"), Persona(id="b"), "t3", i) for i in range(3)]
    iph = [await agent.run_flow(spec(shop_url, "dirty"), Persona(id="i", device="iphone"), "t3", i) for i in range(3)]
    mean = lambda rs: sum(r.advertised_price for r in rs) / len(rs)
    assert mean(iph) > mean(base) * 1.05


async def test_cancel_friction(agent, shop_url):
    dirty = await agent.run_flow(spec(shop_url, "dirty", "cancel"), Persona(id="base"), "t4", 0)
    clean = await agent.run_flow(spec(shop_url, "clean", "cancel"), Persona(id="base"), "t4", 0)
    assert dirty.reached_end and clean.reached_end, (dirty.error, clean.error)
    assert dirty.clicks_total >= 7 and clean.clicks_total == 2
    assert dirty.meta["signup_clicks"] == 1


async def test_allowlist_blocks_unknown_hosts():
    with pytest.raises(PermissionError):
        ensure_allowed("https://www.example-airline.com/book")
