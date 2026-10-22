"""Provider-independent data model for everything an audit captures.

Both the open-source (Playwright) lane and the TinyFish lane produce RunRecords, so
the ML / stats layer never needs to know which one ran.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Device = Literal["desktop", "iphone", "android"]
UserState = Literal["new", "returning"]
PriceKind = Literal["base", "fee", "tax", "addon", "discount", "total", "other"]
FlowKind = Literal["purchase", "cancel", "signup"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Persona(BaseModel):
    id: str
    country: str = "US"
    device: Device = "desktop"
    user_state: UserState = "new"

    @property
    def label(self) -> str:
        return f"{self.country}/{self.device}/{self.user_state}"


class PriceItem(BaseModel):
    label: str
    amount: float
    kind: PriceKind = "other"
    currency: str = ""
    preselected: bool = False


class Signal(BaseModel):
    """A non-textual observation from the DOM (live countdown, pre-ticked box...)."""

    kind: Literal["countdown", "timer_resets", "preselected", "strikethrough"]
    text: str = ""
    detail: dict = Field(default_factory=dict)


class Segment(BaseModel):
    """One visible piece of UI text, with just enough context for the classifier."""

    text: str
    tag: str = ""
    role: Literal["button", "link", "label", "text", "heading"] = "text"
    near_checked: bool = False


class Step(BaseModel):
    index: int
    name: str
    url: str = ""
    title: str = ""
    heading: str = ""  # the page's main heading (product name on a product page)
    text: str = ""
    screenshot: str | None = None  # path relative to the runs dir
    html: str | None = None
    prices: list[PriceItem] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    clicks_so_far: int = 0
    note: str = ""


class RunRecord(BaseModel):
    run_id: str
    audit_id: str
    site: str
    flow: FlowKind = "purchase"
    persona: Persona
    provider: str
    repeat: int = 0
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    steps: list[Step] = Field(default_factory=list)
    advertised_price: float | None = None
    final_price: float | None = None
    currency: str = ""
    clicks_total: int = 0
    reached_end: bool = False  # purchase: reached the payment screen; cancel: cancellation confirmed
    # Where a purchase run ended: "payment" (card or payment-method screen), "price_screen" (a login or
    # personal-details wall came first, but a checkout total had already been shown), "login" (a wall
    # before any total), "blocked" (CAPTCHA / access denied) or "" (unknown / scripted).
    stopped_at: str = ""
    error: str | None = None
    meta: dict = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Measured: reached payment, or captured a checkout total before a login/details wall."""
        return self.error is None and (self.reached_end or self.stopped_at == "price_screen")

    def price_step(self) -> Step | None:
        """The step whose total is this run's payable price (payment screen, else the furthest total shown)."""
        named = next((s for s in self.steps if s.name == "payment"), None)
        if named:
            return named
        idx = self.meta.get("price_step")
        if idx is not None:
            hit = next((s for s in self.steps if s.index == idx), None)
            if hit:
                return hit
        with_total = [s for s in self.steps if any(p.kind == "total" for p in s.prices)]
        return with_total[-1] if with_total else (self.steps[-1] if self.steps else None)


class FlowSpec(BaseModel):
    """What to audit: a site, a flow and the intent a real user would have."""

    site: str  # short name, e.g. "dirty"
    base_url: str
    flow: FlowKind = "purchase"
    intent: str = "Buy one ticket for the first event listed"
    script: str | None = None  # name of a built-in deterministic script, if any
    session_file: str | None = None  # the account holder's own saved sign-in (open-source lane only)
    product_url: str | None = None  # pinned product page: every shopper checks this exact product
    product_index: int = 0  # which listed product this spec checks (0 = first); several run side by side
    polite: bool = False  # real sites: one shopper at a time, human-like pauses between actions


class Evidence(BaseModel):
    run_id: str = ""
    step_name: str = ""
    text: str = ""
    screenshot: str | None = None


class Finding(BaseModel):
    id: str
    type: str  # drip_pricing | basket_sneaking | personalization | friction | pattern:<name> | policy
    title: str
    detail: str
    ccpa: str = ""
    severity: Literal["low", "medium", "high"] = "medium"
    penalty: float = 0.0
    evidence: list[Evidence] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)


class AuditReport(BaseModel):
    audit_id: str
    site: str
    provider: str
    created_at: datetime = Field(default_factory=utcnow)
    runs_total: int = 0
    runs_ok: int = 0
    trust_score: float = 100.0
    findings: list[Finding] = Field(default_factory=list)
    waterfalls: list[dict] = Field(default_factory=list)  # per-persona median waterfall
    personalization: dict = Field(default_factory=dict)
    friction: dict = Field(default_factory=dict)
    score_breakdown: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Which product the purchase runs priced: [{name, url, runs, personas, used}], most-checked first.
    products: list[dict] = Field(default_factory=list)
