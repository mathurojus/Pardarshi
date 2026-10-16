"""Open-source provider: local Playwright for browsing, httpx+BeautifulSoup for fetch, DDG for search.

This is the development / insurance lane. The TinyFish provider (tinyfish.py) is the
submitted path and implements the same interfaces.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from app.capture.schema import FlowSpec, Persona, RunRecord
from app.capture.store import AuditStore
from app.config import settings
from app.providers.base import FetchedPage, SearchHit
from app.providers.dom import host_of
from app.providers.scripts import execute

DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")
DEVICES = {"iphone": "iPhone 13", "android": "Pixel 7"}
LOCALES = {"US": ("en-US", "America/New_York"), "GB": ("en-GB", "Europe/London"), "CA": ("en-CA", "America/Toronto"),
           "DE": ("de-DE", "Europe/Berlin"), "FR": ("fr-FR", "Europe/Paris"), "JP": ("ja-JP", "Asia/Tokyo"),
           "AU": ("en-AU", "Australia/Sydney"), "IN": ("en-IN", "Asia/Kolkata")}


def ensure_allowed(url: str) -> None:
    """Safety guard: only audit hosts that were explicitly allowed (demo shop is allowed by default)."""
    host = host_of(url)
    if host not in settings.allowed_hosts and host != host_of(settings.demo_shop_url):
        raise PermissionError(
            f"Host '{host}' is not on the allowlist. Add it to ALLOWED_HOSTS to audit it "
            "(read-only audits only; the auditor never enters payment details)."
        )


class PlaywrightAgent:
    name = "opensource"

    def __init__(self, store: AuditStore | None = None, concurrency: int = 4):
        self.store = store or AuditStore()
        self._sem = asyncio.Semaphore(concurrency)
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()

    def supports_country(self, spec: FlowSpec) -> bool:
        """Locally we cannot change our IP, but the demo shop reads a simulation header."""
        return host_of(spec.base_url) == host_of(settings.demo_shop_url)

    def supports_device(self, spec: FlowSpec) -> bool:
        return True

    async def _browser_(self):
        async with self._lock:
            if self._browser is None:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=settings.headless)
        return self._browser

    def _context_kwargs(self, persona: Persona, spec: FlowSpec) -> dict:
        kw: dict = {}
        if persona.device in DEVICES:
            kw.update(self._pw.devices[DEVICES[persona.device]])
            kw.pop("default_browser_type", None)
        else:
            kw.update(viewport={"width": 1366, "height": 768}, user_agent=DESKTOP_UA)
        locale, tz = LOCALES.get(persona.country, LOCALES["US"])
        kw.update(locale=locale, timezone_id=tz)
        if self.supports_country(spec):
            kw["extra_http_headers"] = {"X-Demo-Country": persona.country}
        if spec.session_file:  # the account holder's own saved sign-in (never sent anywhere else)
            kw["storage_state"] = spec.session_file
        return kw

    async def run_flow(self, spec: FlowSpec, persona: Persona, audit_id: str, repeat: int) -> RunRecord:
        ensure_allowed(spec.base_url)
        run = RunRecord(run_id=uuid.uuid4().hex[:10], audit_id=audit_id, site=spec.site, flow=spec.flow,
                        persona=persona, provider=self.name, repeat=repeat)
        run.meta["country_effective"] = persona.country if self.supports_country(spec) else "local"
        async with self._sem:
            browser = await self._browser_()
            ctx = await browser.new_context(**self._context_kwargs(persona, spec))
            ctx.set_default_timeout(15000)
            try:
                page = await ctx.new_page()
                await execute(page, spec, persona, run, self.store)
            finally:
                await ctx.close()
        self.store.save_run(run)
        return run

    async def aclose(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._browser = self._pw = None


class HttpFetch:
    async def fetch(self, urls: list[str]) -> list[FetchedPage]:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers={"User-Agent": DESKTOP_UA}) as c:
            async def one(u: str) -> FetchedPage:
                try:
                    r = await c.get(u)
                    soup = BeautifulSoup(r.text, "lxml")
                    for t in soup(["script", "style", "noscript"]):
                        t.decompose()
                    text = " ".join(soup.get_text(" ").split())
                    return FetchedPage(url=str(r.url), title=(soup.title.string or "").strip() if soup.title else "", text=text[:20000])
                except Exception as e:
                    return FetchedPage(url=u, error=f"{type(e).__name__}: {e}"[:200])

            return await asyncio.gather(*(one(u) for u in urls))


class DdgSearch:
    async def search(self, query: str, n: int = 8) -> list[SearchHit]:
        try:
            from ddgs import DDGS
        except ImportError:
            return []

        def run():
            with DDGS() as d:
                return list(d.text(query, max_results=n))

        try:
            rows = await asyncio.to_thread(run)
        except Exception:
            return []
        return [SearchHit(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", "")) for r in rows]
