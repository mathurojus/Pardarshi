"""Live progress: lets a provider report each captured step without changing the provider interface.

The pipeline sets STEP_HOOK inside each run's own asyncio task, so concurrent runs never see each other's hook.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

STEP_HOOK: ContextVar[Callable[[str], None] | None] = ContextVar("STEP_HOOK", default=None)


def step(name: str) -> None:
    hook = STEP_HOOK.get()
    if hook:
        try:
            hook(name)
        except Exception:
            pass  # progress is cosmetic; never let it break a run
