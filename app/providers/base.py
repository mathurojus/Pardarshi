"""Provider interfaces. The audit pipeline only talks to these.

Two implementations exist for each: TinyFish (app/providers/tinyfish.py) and an
open-source stack (app/providers/opensource.py). PROVIDER=tinyfish|opensource picks one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from app.capture.schema import FlowSpec, Persona, RunRecord


class FetchedPage(BaseModel):
    url: str
    title: str = ""
    text: str = ""
    error: str | None = None


class SearchHit(BaseModel):
    title: str
    url: str
    snippet: str = ""


class AgentProvider(Protocol):
    name: str

    async def run_flow(self, spec: FlowSpec, persona: Persona, audit_id: str, repeat: int) -> RunRecord: ...

    async def aclose(self) -> None: ...


class FetchProvider(Protocol):
    async def fetch(self, urls: list[str]) -> list[FetchedPage]: ...


class SearchProvider(Protocol):
    async def search(self, query: str, n: int = 8) -> list[SearchHit]: ...


@dataclass
class Providers:
    name: str
    agent: AgentProvider
    fetch: FetchProvider
    search: SearchProvider

    async def aclose(self) -> None:
        await self.agent.aclose()
