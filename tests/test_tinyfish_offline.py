"""TinyFish provider logic that can be verified without calling the API (no credits used)."""
from app.capture.schema import FlowSpec, Persona, RunRecord
from app.providers.tinyfish import AGENT_SCHEMA, TinyFishAgent, build_goal, parse_agent_result

SAMPLE = {
    "advertised_price": 999, "currency": "INR", "final_payable_total": 1277, "reached_payment_page": True,
    "clicks": 4,
    "steps": [
        {"name": "Event page", "page_url": "https://x/event/1",
         "price_lines": [{"label": "Price", "amount": 999}],
         "visible_texts": [{"text": "Only 2 tickets left!", "kind": "text", "checked": False}]},
        {"name": "Cart", "page_url": "https://x/cart",
         "price_lines": [{"label": "Convenience fee", "amount": 80},
                         {"label": "Ticket Protect insurance", "amount": 149, "preselected": True},
                         {"label": "Total", "amount": 1277}],
         "visible_texts": [{"text": "Add Ticket Protect", "kind": "label", "checked": True}]},
    ],
}


def _run(flow="purchase"):
    return RunRecord(run_id="r", audit_id="a", site="s", flow=flow, persona=Persona(id="base"), provider="tinyfish")


def test_parse_purchase_result():
    r = _run()
    parse_agent_result(SAMPLE, r)
    assert r.ok and r.advertised_price == 999 and r.final_price == 1277
    cart = r.steps[1]
    assert cart.name == "cart" and cart.prices[1].kind == "addon" and cart.prices[1].preselected
    assert cart.segments[0].near_checked and r.steps[0].segments[0].text.startswith("Only 2")


def test_parse_cancel_result():
    r = _run("cancel")
    parse_agent_result({"reached_payment_page": False, "cancellation_completed": True, "clicks": 7,
                        "clicks_to_signup": 1, "steps": []}, r)
    assert r.reached_end and r.clicks_total == 7 and r.meta["signup_clicks"] == 1


def test_empty_result_is_an_error_not_a_crash():
    r = _run()
    parse_agent_result(None, r)
    assert r.error and not r.ok


def test_goal_contains_safety_rules_and_schema_is_valid():
    g = build_goal(FlowSpec(site="s", base_url="https://x", intent="Book a flight"), Persona(id="base"))
    assert "NEVER type card" in g and "NEVER click anything that pays" in g and "VERBATIM" in g
    # TinyFish rejects these keywords (docs: structured-output)
    blob = str(AGENT_SCHEMA)
    for banned in ("oneOf", "'const'", "additionalProperties", "'example'"):
        assert banned not in blob
    assert AGENT_SCHEMA["type"] == "object"


def test_lane_selection():
    a = TinyFishAgent(api_key="test-key")
    assert a._lane_for(FlowSpec(site="s", base_url="https://x", script="ticketshop")) == "browser"
    assert a._lane_for(FlowSpec(site="s", base_url="https://x")) == "agent"
    assert a.supports_device(FlowSpec(site="s", base_url="https://x", script="ticketshop"))
    assert not a.supports_device(FlowSpec(site="s", base_url="https://x"))


async def test_agent_lane_falls_back_when_custom_step_limits_are_not_enabled(tmp_path):
    """Live finding (16 Oct 2026): accounts without the beta reject agent_config.max_steps. The lane must retry
    without it, and must use the standard browser profile, never the anti-detection one."""
    import httpx
    from tinyfish import BrowserProfile, PermissionDeniedError

    from app.capture.store import AuditStore

    calls = []

    class Resp:
        run_id, status, error = "r1", "COMPLETED", None
        result = {"advertised_price": 999, "final_payable_total": 1049, "reached_payment_page": True, "steps": []}

    class AgentApi:
        async def run(self, **kw):
            calls.append(kw)
            if "agent_config" in kw:
                raise PermissionDeniedError("not enabled", response=httpx.Response(403, request=httpx.Request("POST", "https://x")))
            return Resp()

    agent = TinyFishAgent.__new__(TinyFishAgent)
    agent.store, agent.api_key, agent.lane = AuditStore(tmp_path), "k", "agent"
    agent.client = type("C", (), {"agent": AgentApi()})()

    async def no_shots(*a):
        return None

    agent._attach_screenshots = no_shots
    run = RunRecord(run_id="x", audit_id="a", site="s", persona=Persona(id="base"), provider="tinyfish")
    await agent._agent_lane(FlowSpec(site="s", base_url="https://shop.in"), Persona(id="base"), run)
    assert len(calls) == 2 and "agent_config" not in calls[1]
    assert all(c["browser_profile"] == BrowserProfile.LITE for c in calls)
    assert run.final_price == 1049 and run.reached_end
