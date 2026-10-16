"""Render the logo files from preview.html: run  python docs/brand/render.py"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).parent


async def main() -> None:
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        p = await b.new_page(device_scale_factor=4)
        await p.goto((HERE / "preview.html").as_uri())
        await p.evaluate("document.fonts.ready")
        await p.screenshot(path=str(HERE / "preview.png"), full_page=True, scale="css")
        for theme in ("light", "dark"):  # transparent PNG of the lockup, 4x
            row = p.locator(f"#lockup-{theme}")
            await row.evaluate("e => e.style.background = 'transparent'")
            await p.evaluate("document.body.style.background = 'transparent'")
            await row.locator(".lockup").screenshot(path=str(HERE / f"pardarshi-logo-{theme}.png"), omit_background=True)
        mark = await b.new_page(device_scale_factor=1, viewport={"width": 512, "height": 512})
        await mark.goto((HERE / "preview.html").as_uri())
        await mark.evaluate("""() => { document.body.style.background = 'transparent';
            document.body.innerHTML = '<img id="m" src="pardarshi-mark.svg" width="512" height="512" style="display:block">'; }""")
        await mark.wait_for_function("document.getElementById('m').complete")
        await mark.locator("#m").screenshot(path=str(HERE / "pardarshi-mark-512.png"), omit_background=True)
        await b.close()


asyncio.run(main())
