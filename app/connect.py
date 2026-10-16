"""Connect a shop account from the console: the account holder signs in to the shop in their own Chrome.

Pardarshi starts the installed Chrome (or Edge) as an ordinary browser window, with a separate profile kept
for Pardarshi. Nothing drives that window while the person signs in: they type their password or OTP
themselves, exactly as on any other day. When they click "Save sign-in", Pardarshi reads that window's cookies
once (through the local debugging port, which listens on 127.0.0.1 only), saves them like
scripts.connect_account does, and closes the window.

This runs on the machine that hosts Pardarshi, so the window opens on that machine's screen.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from app import sessions

PENDING: dict[tuple[int, str], dict] = {}  # (user id, host) -> {proc, port, started}
MAX_AGE = 30 * 60  # an abandoned sign-in window is closed after 30 minutes


def browser_path() -> str | None:
    """The installed Chrome or Edge (a real browser, not Playwright's test build)."""
    env = os.environ.get("PARDARSHI_BROWSER")
    cands = [env] if env else []
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            cands += [os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                      os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")]
    cands += [shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "microsoft-edge")]
    cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return next((c for c in cands if c and os.path.isfile(c)), None)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def profile_dir(user_id: int, host: str) -> Path:
    return sessions.user_dir(user_id) / "profiles" / sessions.clean_host(host)


def _stop(key: tuple[int, str]) -> None:
    job = PENDING.pop(key, None)
    if job and job["proc"].poll() is None:
        job["proc"].terminate()
        try:
            job["proc"].wait(5)
        except subprocess.TimeoutExpired:
            job["proc"].kill()


def _reap() -> None:
    for key, job in list(PENDING.items()):
        if job["proc"].poll() is not None or time.time() - job["started"] > MAX_AGE:
            _stop(key)


def start(user_id: int, host: str) -> dict:
    """Open the shop's home page in a normal Chrome window for the account holder to sign in."""
    _reap()
    host = sessions.clean_host(host)
    exe = browser_path()
    if not exe:
        raise RuntimeError("No Chrome or Edge found on this machine. Install Chrome, or set PARDARSHI_BROWSER to its path.")
    key = (user_id, host)
    _stop(key)
    prof = profile_dir(user_id, host)
    prof.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    proc = subprocess.Popen(
        [exe, f"--user-data-dir={prof}", f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
         "--no-first-run", "--no-default-browser-check", "--new-window", f"https://{host}/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    PENDING[key] = {"proc": proc, "port": port, "started": time.time()}
    return {"host": host, "browser": Path(exe).stem}


async def save(user_id: int, host: str) -> dict:
    """Save the sign-in window's cookies for this host, then close the window."""
    from playwright.async_api import async_playwright

    host = sessions.clean_host(host)
    key = (user_id, host)
    job = PENDING.get(key)
    if not job or job["proc"].poll() is not None:
        PENDING.pop(key, None)
        raise RuntimeError("The sign-in window is closed. Click Connect again and keep the window open until you save.")
    path = sessions.path_for(user_id, host)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{job['port']}", timeout=15000)
        ctx = browser.contexts[0]
        state = await ctx.storage_state(path=str(path))
        await browser.close()
    _stop(key)
    bare = host[4:] if host.startswith("www.") else host
    n = sum(1 for c in state.get("cookies", []) if c.get("domain", "").lstrip(".").endswith(bare))
    return {"host": host, "cookies": n}


def cancel(user_id: int, host: str) -> None:
    _stop((user_id, sessions.clean_host(host)))
