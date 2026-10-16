"""Step-by-step TinyFish check. Run this BEFORE the final demo.

  python -m scripts.tinyfish_smoke            # free calls only: Search + Fetch
  python -m scripts.tinyfish_smoke --browser  # + one Browser API session (uses credits; prints the cost)
  python -m scripts.tinyfish_smoke --agent    # + one tiny Agent API run (uses credits; prints the cost)

Credits are read from the wallet endpoint before and after, so you see exactly what each call cost.
"""
from __future__ import annotations

import argparse
import asyncio

from tinyfish import AsyncTinyFish

from app.config import settings


async def wallet(client) -> str:
    try:
        w = await client.wallet.get()
        return str(w.model_dump() if hasattr(w, "model_dump") else w)[:300]
    except Exception as e:
        return f"(wallet unavailable: {type(e).__name__})"


async def main(browser: bool, agent: bool) -> None:
    client = AsyncTinyFish(api_key=settings.tinyfish_api_key)
    print("wallet before:", await wallet(client))

    r = await client.search.query(query="online event ticketing hidden fees complaints")
    print(f"[Search] {len(r.results)} results; first: {r.results[0].title!r} -> {r.results[0].url}")

    f = await client.fetch.get_contents(urls=["https://www.tinyfish.ai/"], format="markdown")
    print(f"[Fetch] {len(f.results)} page(s); title={f.results[0].title!r}; {len(f.results[0].text)} chars; errors={f.errors}")

    if browser:
        from playwright.async_api import async_playwright
        from tinyfish import BrowserProxyConfig

        s = await client.browser.sessions.create(url="https://example.com", timeout_seconds=120,
                                                 proxy_config=BrowserProxyConfig(enabled=True, country_code="DE"))
        print("[Browser] session", s.session_id)
        async with async_playwright() as pw:
            b = await pw.chromium.connect_over_cdp(s.cdp_url)
            await asyncio.sleep(2)
            page = b.contexts[0].pages[0]
            await page.wait_for_load_state("domcontentloaded")
            ip = await page.evaluate("fetch('https://api.country.is').then(r => r.json()).catch(() => null)")
            print("[Browser] title:", await page.title(), "| exit IP country:", ip)
        import httpx

        async with httpx.AsyncClient() as h:
            await h.delete(f"https://api.browser.tinyfish.ai/{s.session_id}", headers={"X-API-Key": settings.tinyfish_api_key})
        print("[Browser] session closed")

    if agent:
        from tinyfish import AgentConfig

        res = await client.agent.run(
            goal="Return the page heading as JSON {\"heading\": string}. Do nothing else.", url="https://example.com",
            )  # default step limit (custom limits are a beta feature)
        print("[Agent] status:", res.status, "| steps:", res.num_of_steps, "| result:", res.result)

    print("wallet after: ", await wallet(client))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--agent", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.browser, a.agent))
