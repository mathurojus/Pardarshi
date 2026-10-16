"""TinyFish provider: the submitted path. Uses all four TinyFish APIs.

Browser API -> remote cloud browser with a country proxy, driven over CDP by our Playwright flows.
               Gives device emulation, countdown/reload probing, screenshots and DOM capture.
Agent API   -> goal-driven multi-step walk of a site we have no script for; typed `output_schema`
               returns the price waterfall and verbatim UI copy, which our classifier then judges.
Fetch API   -> terms / refund / cancellation pages as clean text for policy-vs-reality checks.
Search API  -> peer-site discovery and public complaint threads.

TINYFISH_LANE=auto|browser|agent. 'auto' = browser lane for scripted sites, agent lane otherwise.
"""
from __future__ import annotations

import asyncio
import base64
import os
import uuid

import httpx
from playwright.async_api import async_playwright
from tinyfish import AgentConfig, AsyncTinyFish, BrowserProfile, BrowserProxyConfig, PermissionDeniedError, ProxyConfig

from app.capture.schema import FlowSpec, Persona, PriceItem, RunRecord, Segment, Step, utcnow
from app.capture.store import AuditStore
from app.config import settings
from app.ml.price_extract import classify_kind
from app.providers.base import FetchedPage, Providers, SearchHit
from app.providers.opensource import LOCALES, ensure_allowed
from app.providers.scripts import execute

DEVICES = {"iphone": "iPhone 13", "android": "Pixel 7"}
API_BASE = "https://agent.tinyfish.ai/v1"
BROWSER_API = "https://api.browser.tinyfish.ai"

_NULLABLE_NUM = {"type": "number", "nullable": True}
_NULLABLE_STR = {"type": "string", "nullable": True}
AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "advertised_price": _NULLABLE_NUM,
        "currency": _NULLABLE_STR,
        "product_name": _NULLABLE_STR,
        "final_payable_total": _NULLABLE_NUM,
        "reached_payment_page": {"type": "boolean"},
        "stopped_at": {"type": "string", "enum": ["payment", "login", "blocked", "other"], "nullable": True},
        "cancellation_completed": {"type": "boolean", "nullable": True},
        "clicks": {"type": "integer", "nullable": True},
        "clicks_to_signup": {"type": "integer", "nullable": True},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "page_url": _NULLABLE_STR,
                    "price_lines": {"type": "array", "items": {"type": "object", "properties": {
                        "label": {"type": "string"}, "amount": {"type": "number"},
                        "preselected": {"type": "boolean", "nullable": True}}, "required": ["label", "amount"]}},
                    "visible_texts": {"type": "array", "items": {"type": "object", "properties": {
                        "text": {"type": "string"},
                        "kind": {"type": "string", "enum": ["button", "link", "label", "text", "heading"]},
                        "checked": {"type": "boolean", "nullable": True}}, "required": ["text", "kind"]}},
                },
                "required": ["name", "price_lines", "visible_texts"],
            },
        },
    },
    "required": ["reached_payment_page", "steps"],
}


def build_goal(spec: FlowSpec, persona: Persona) -> str:
    cancel = spec.flow == "cancel"
    return (
        "You are auditing a website for consumer protection. Behave like an ordinary shopper.\n"
        f"TASK: {spec.intent}.\n"
        "RULES: Read-only. NEVER type card, bank, personal or login details. NEVER click anything that pays "
        "or places an order. "
        + ("Complete the cancellation (this is the end). " if cancel else
           "Prefer the path that shows the full price breakdown (product page, add to cart, cart or checkout summary). "
           "Stop as soon as a page asks for card or payment details or shows payment methods: that page is the END "
           "(stopped_at=payment). If the site asks you to log in, enter an OTP, or give personal details first, STOP "
           "there (stopped_at=login) and report the furthest checkout total you saw. If a CAPTCHA or access-denied "
           "page appears, STOP and set stopped_at=blocked; never try to solve or get around it. ")
        + "\nAT EVERY PAGE you pass through, record: (1) every price line shown (label and amount) including "
        "fees, taxes, add-ons, discounts and totals; (2) for every checkbox whether it is already ticked; "
        "(3) copy VERBATIM every banner, badge, countdown, stock or availability claim, 'people viewing' or "
        "'recently bought' claim, discount or urgency text, decline or opt-out wording and button label. "
        "Do not summarise or judge the wording.\n"
        "ALSO RETURN: product_name = the exact name of the product you priced; "
        "advertised_price = the first price shown for the item before any fees; "
        f"final_payable_total = the total payable on the last page; clicks = how many clicks you made"
        + ("; clicks_to_signup = clicks needed to start the membership; cancellation_completed = true if cancelled."
           if cancel else ".")
    )


def parse_agent_result(result: dict | None, run: RunRecord) -> None:
    """Fill a RunRecord from the Agent API's structured result. Pure function (unit-tested offline)."""
    if not result:
        run.error = run.error or "Agent returned no structured result"
        return
    run.advertised_price = result.get("advertised_price")
    run.final_price = result.get("final_payable_total")
    run.currency = result.get("currency") or ""
    if result.get("product_name"):
        run.meta["product_name"] = str(result["product_name"])[:160]
    run.clicks_total = int(result.get("clicks") or 0)
    if run.flow == "cancel":
        run.reached_end = bool(result.get("cancellation_completed"))
        run.meta["signup_clicks"] = int(result.get("clicks_to_signup") or 1)
    else:
        run.reached_end = bool(result.get("reached_payment_page"))
        stop = (result.get("stopped_at") or "").lower()
        if stop == "blocked":
            run.stopped_at = "blocked"
            run.error = run.error or "Blocked by the site's bot protection (CAPTCHA or access-denied page). Not bypassed."
        elif run.reached_end:
            run.stopped_at = "payment"
    clicks_done = 0
    for i, s in enumerate(result.get("steps") or []):
        items = []
        for line in s.get("price_lines") or []:
            kind = classify_kind(line["label"])
            amt = float(line["amount"])
            items.append(PriceItem(label=line["label"], amount=-abs(amt) if kind == "discount" else amt, kind=kind,
                                   currency=run.currency, preselected=bool(line.get("preselected"))))
        segs = [Segment(text=t["text"], role=t.get("kind", "text"), near_checked=bool(t.get("checked")))
                for t in s.get("visible_texts") or [] if t.get("text")]
        clicks_done = min(run.clicks_total, clicks_done + 1)
        run.steps.append(Step(index=i, name=(s.get("name") or f"step{i}").lower().replace(" ", "_"),
                              url=s.get("page_url") or "", prices=items, segments=segs, clicks_so_far=clicks_done))
    if not run.final_price and run.steps:
        totals = [p for p in run.steps[-1].prices if p.kind == "total"]
        run.final_price = totals[-1].amount if totals else None
    if run.flow == "purchase" and not run.stopped_at:
        # Stopped at a login/details wall (or elsewhere) after a checkout total was shown: still measured.
        later_total = any(p.kind == "total" for st in run.steps[1:] for p in st.prices)
        if run.advertised_price and run.final_price and later_total:
            run.stopped_at = "price_screen"
        elif (result.get("stopped_at") or "").lower() == "login":
            run.stopped_at = "login"


class TinyFishAgent:
    name = "tinyfish"

    def __init__(self, store: AuditStore | None = None, api_key: str | None = None, lane: str | None = None,
                 concurrency: int = 4):
        self.store = store or AuditStore()
        self.api_key = api_key or settings.tinyfish_api_key
        if not self.api_key:
            raise RuntimeError("TINYFISH_API_KEY is not set")
        self.lane = (lane or os.getenv("TINYFISH_LANE", "auto")).lower()
        self.client = AsyncTinyFish(api_key=self.api_key)
        self._sem = asyncio.Semaphore(concurrency)

    # country proxies exist for every persona we use; device emulation needs our own Playwright (browser lane)
    def supports_country(self, spec: FlowSpec) -> bool:
        return True

    def supports_device(self, spec: FlowSpec) -> bool:
        return self._lane_for(spec) == "browser"

    def _lane_for(self, spec: FlowSpec) -> str:
        return self.lane if self.lane in ("browser", "agent") else ("browser" if spec.script else "agent")

    async def run_flow(self, spec: FlowSpec, persona: Persona, audit_id: str, repeat: int) -> RunRecord:
        ensure_allowed(spec.base_url)
        lane = self._lane_for(spec)
        run = RunRecord(run_id=uuid.uuid4().hex[:10], audit_id=audit_id, site=spec.site, flow=spec.flow,
                        persona=persona, provider=self.name, repeat=repeat)
        run.meta.update(lane=lane, country_effective=persona.country)
        if spec.session_file:  # a saved sign-in never leaves this machine
            run.error = "Own-account sessions run only on the open-source provider."
            self.store.save_run(run)
            return run
        async with self._sem:
            try:
                await (self._browser_lane if lane == "browser" else self._agent_lane)(spec, persona, run)
            except Exception as e:
                run.error = f"{type(e).__name__}: {e}"[:300]
            run.finished_at = run.finished_at or utcnow()
        self.store.save_run(run)
        return run

    # -- Browser API lane -------------------------------------------------------------------
    async def _browser_lane(self, spec: FlowSpec, persona: Persona, run: RunRecord) -> None:
        session = await self.client.browser.sessions.create(
            timeout_seconds=300, proxy_config=BrowserProxyConfig(enabled=True, country_code=persona.country))
        run.meta["browser_session"] = session.session_id
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(session.cdp_url)
                await asyncio.sleep(2)
                ctx = browser.contexts[0]
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await self._apply_persona(pw, ctx, page, persona)
                await execute(page, spec, persona, run, self.store)
        finally:
            async with httpx.AsyncClient(timeout=20) as h:  # free the session (and its billing) immediately
                try:
                    await h.delete(f"{BROWSER_API}/{session.session_id}", headers={"X-API-Key": self.api_key})
                except Exception:
                    pass

    @staticmethod
    async def _apply_persona(pw, ctx, page, persona: Persona) -> None:
        cdp = await ctx.new_cdp_session(page)
        locale, tz = LOCALES.get(persona.country, LOCALES["US"])
        await cdp.send("Emulation.setTimezoneOverride", {"timezoneId": tz})
        await cdp.send("Emulation.setLocaleOverride", {"locale": locale})
        if persona.device in DEVICES:
            d = pw.devices[DEVICES[persona.device]]
            vp = d["viewport"]
            await cdp.send("Network.setUserAgentOverride", {"userAgent": d["user_agent"], "acceptLanguage": locale})
            await cdp.send("Emulation.setDeviceMetricsOverride", {
                "width": vp["width"], "height": vp["height"], "deviceScaleFactor": d.get("device_scale_factor", 2),
                "mobile": True})
            await cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": True})

    # -- Agent API lane ---------------------------------------------------------------------
    async def _agent_lane(self, spec: FlowSpec, persona: Persona, run: RunRecord) -> None:
        kw = dict(goal=build_goal(spec, persona), url=spec.product_url or spec.base_url, output_schema=AGENT_SCHEMA,
                  # the standard profile, not the anti-detection one: blocks are reported, never worked around
                  browser_profile=BrowserProfile.LITE,
                  proxy_config=ProxyConfig(enabled=True, country_code=persona.country))
        try:
            resp = await asyncio.wait_for(
                self.client.agent.run(**kw, agent_config=AgentConfig(max_steps=settings.max_steps)), timeout=600)
        except PermissionDeniedError:  # custom step limits are a beta feature on some accounts: use TinyFish's default
            resp = await asyncio.wait_for(self.client.agent.run(**kw), timeout=600)
        run.meta["tinyfish_run_id"] = resp.run_id
        if str(resp.status) != "RunStatus.COMPLETED" and str(getattr(resp.status, "value", resp.status)) != "COMPLETED":
            run.error = (resp.error.message if resp.error else f"Run ended with status {resp.status}")[:300]
        parse_agent_result(resp.result, run)
        run.finished_at = utcnow()
        if resp.run_id:
            await self._attach_screenshots(resp.run_id, run)

    async def _attach_screenshots(self, tf_run_id: str, run: RunRecord) -> None:
        """Best effort: pull per-step screenshots of the agent's run and attach them as evidence."""
        try:
            async with httpx.AsyncClient(timeout=60) as h:
                r = await h.get(f"{API_BASE}/runs/{tf_run_id}", params={"screenshots": "base64", "html": "none"},
                                headers={"X-API-Key": self.api_key})
                r.raise_for_status()
                shots = [s["screenshot"] for s in r.json().get("steps", []) if s.get("screenshot")]
        except Exception:
            return
        if not shots or not run.steps:
            return
        d = self.store.shots_dir(run.audit_id)
        for i, step in enumerate(run.steps):
            uri = shots[min(len(shots) - 1, round(i * (len(shots) - 1) / max(1, len(run.steps) - 1)))]
            try:
                p = d / f"{run.run_id}_{i}_{step.name}.jpg"
                p.write_bytes(base64.b64decode(uri.split(",", 1)[-1]))
                step.screenshot = self.store.rel(p)
            except Exception:
                continue

    async def aclose(self) -> None:
        await self.client.close() if asyncio.iscoroutinefunction(self.client.close) else self.client.close()


class TinyFishFetch:
    def __init__(self, client: AsyncTinyFish):
        self.client = client

    async def fetch(self, urls: list[str]) -> list[FetchedPage]:
        out: list[FetchedPage] = []
        for i in range(0, len(urls), 10):  # the API takes at most 10 URLs per request
            resp = await self.client.fetch.get_contents(urls=urls[i:i + 10], format="markdown", purpose="Read terms, refund and cancellation policy")
            out += [FetchedPage(url=r.final_url or r.url, title=r.title or "", text=(r.text or "")[:20000]) for r in resp.results]
            out += [FetchedPage(url=getattr(e, "url", ""), error=str(getattr(e, "error", None) or getattr(e, "reason", e))[:200])
                    for e in (resp.errors or [])]
        return out


class TinyFishSearch:
    def __init__(self, client: AsyncTinyFish):
        self.client = client

    async def search(self, query: str, n: int = 8) -> list[SearchHit]:
        resp = await self.client.search.query(query=query, purpose="Find peer sites and public consumer complaints")
        return [SearchHit(title=r.title or "", url=r.url, snippet=r.snippet or "") for r in resp.results[:n]]


def make_tinyfish(store: AuditStore | None = None) -> Providers:
    agent = TinyFishAgent(store)
    return Providers(name="tinyfish", agent=agent, fetch=TinyFishFetch(agent.client), search=TinyFishSearch(agent.client))
