"""Provider factory: PROVIDER=opensource | tinyfish."""
from __future__ import annotations

from app.capture.store import AuditStore
from app.config import settings
from app.providers.base import Providers


def get_providers(name: str | None = None, store: AuditStore | None = None) -> Providers:
    name = (name or settings.provider).lower()
    if name == "tinyfish":
        from app.providers.tinyfish import make_tinyfish

        return make_tinyfish(store)
    from app.providers.opensource import DdgSearch, HttpFetch, PlaywrightAgent

    return Providers(name="opensource", agent=PlaywrightAgent(store), fetch=HttpFetch(), search=DdgSearch())
