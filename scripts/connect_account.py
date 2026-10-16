"""Connect YOUR OWN shop account so Pardarshi can audit behind the login.

  python -m scripts.connect_account you@example.com www.flipkart.com           # sign in, then press Enter
  python -m scripts.connect_account you@example.com www.flipkart.com --forget  # delete the saved sign-in

A visible browser opens on the site. Sign in yourself (password, OTP, whatever the site asks). Pardarshi
never sees what you type; it only saves the browser's cookies afterwards, under your Pardarshi account.
Audits that use it run on this machine (open-source provider), one shopper, one run. They still never pay,
place an order or type personal details.

Treat the saved file like a password: anyone with it is signed in as you. Use --forget when you are done.
The host must be on ALLOWED_HOSTS.
"""
from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

from app import auth, sessions
from app.providers.opensource import ensure_allowed


async def connect(user_id: int, host: str) -> None:
    path = sessions.path_for(user_id, host)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 860})
        page = await ctx.new_page()
        await page.goto(f"https://{host}/", wait_until="domcontentloaded")
        print(f"\nA browser window is open on {host}.")
        print("1. Sign in to YOUR account there (Pardarshi does not see what you type).")
        print("2. Optional: empty your cart, so audits start clean.")
        await asyncio.to_thread(input, "3. Come back here and press Enter to save the sign-in... ")
        await ctx.storage_state(path=str(path))
        await browser.close()
    print(f"\nSaved. Audits of {host} can now use your session (tick 'Use my signed-in session').")
    print(f"To remove it: python -m scripts.connect_account <email> {host} --forget")


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        print(__doc__)
        sys.exit(1)
    email, host = argv[0].strip().lower(), sessions.clean_host(argv[1])
    u = auth.q("SELECT id, is_guest FROM users WHERE email=?", (email,)).fetchone()
    if not u or u["is_guest"]:
        sys.exit(f"No Pardarshi account with email {email}. Create one in the app first (demo sessions cannot connect accounts).")
    if "--forget" in argv:
        print("Removed." if sessions.forget(u["id"], host) else "Nothing saved for that host.")
        return
    try:
        ensure_allowed(f"https://{host}")
    except PermissionError as e:
        sys.exit(str(e))
    asyncio.run(connect(u["id"], host))


if __name__ == "__main__":
    main(sys.argv[1:])
