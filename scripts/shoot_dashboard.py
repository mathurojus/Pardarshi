"""Screenshot the dashboard (light + dark) for the README. Needs the API on :8000.
   python -m scripts.shoot_dashboard
"""
import asyncio

from playwright.async_api import async_playwright

from app.config import ROOT


async def main() -> None:
    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for scheme, name in (("light", "dashboard.png"), ("dark", "dashboard-dark.png")):
            ctx = await b.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme)
            pg = await ctx.new_page()
            await pg.goto("http://127.0.0.1:8000/", wait_until="networkidle")
            await pg.wait_for_timeout(1800)  # the page opens on the latest audit by itself
            await pg.screenshot(path=str(out / name), full_page=True)
            await ctx.close()
        await b.close()
    print("saved docs/dashboard.png and docs/dashboard-dark.png")


asyncio.run(main())
