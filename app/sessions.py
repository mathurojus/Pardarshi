"""The account holder's own saved sign-ins, for auditing a site behind its login.

How it works: the account holder runs `python -m scripts.connect_account <their pardarshi email> <host>`,
signs in to the shop themselves in a visible browser, and Pardarshi saves that browser's cookies (a
Playwright storage-state file). Audits that ask for it reuse the session on the open-source provider only,
so the sign-in never leaves this machine. Agents still never pay, place an order or type personal details.

Files live under <data dir>/sessions/<user id>/<host>.json: one user's session is never used for another.
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from app.config import settings

_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")


def clean_host(host: str) -> str:
    h = host.strip().lower()
    h = re.sub(r"^https?://", "", h).split("/", 1)[0].split(":", 1)[0]
    if not _HOST.match(h) or ".." in h:
        raise ValueError(f"Not a valid hostname: {host!r}")
    return h


def user_dir(user_id: int) -> Path:
    return settings.data_dir / "sessions" / str(int(user_id))


def path_for(user_id: int, host: str) -> Path:
    return user_dir(user_id) / f"{clean_host(host)}.json"


def find(user_id: int, host: str) -> Path | None:
    """The saved session for this user and host (bare host or with/without 'www.'), if any."""
    h = clean_host(host)
    for cand in (h, h[4:] if h.startswith("www.") else f"www.{h}"):
        p = path_for(user_id, cand)
        if p.is_file():
            return p
    return None


def list_for(user_id: int) -> list[dict]:
    d = user_dir(user_id)
    if not d.is_dir():
        return []
    return [{"host": p.stem, "saved_at": int(p.stat().st_mtime)} for p in sorted(d.glob("*.json"))]


def forget(user_id: int, host: str) -> bool:
    p = path_for(user_id, host)
    if p.is_file():
        p.unlink()
        return True
    return False


def forget_all(user_id: int) -> None:
    shutil.rmtree(user_dir(user_id), ignore_errors=True)


def age_days(p: Path) -> float:
    return (time.time() - p.stat().st_mtime) / 86400
