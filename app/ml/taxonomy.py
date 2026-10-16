"""Dark-pattern classes the model predicts, and how they map to India's CCPA categories.

Source for the category names: the Guidelines for Prevention and Regulation of Dark Patterns
(2023), which list 13 specified patterns. The mapping below is INDICATIVE, to help a
consumer describe what they saw. It is not legal advice or a legal finding.
"""
from __future__ import annotations

CLASSES = [
    "none",
    "false_urgency",
    "scarcity",
    "fake_social_proof",
    "confirmshaming",
    "preselected_addon",
    "forced_action",
    "subscription_trap",
    "nagging",
]

PATTERN_CLASSES = [c for c in CLASSES if c != "none"]

# class -> (human title, CCPA category, base penalty on the 0-100 trust score)
INFO: dict[str, tuple[str, str, float]] = {
    "false_urgency": ("False urgency (countdown / deadline pressure)", "False urgency", 8),
    "scarcity": ("Scarcity pressure ('only N left')", "False urgency", 6),
    "fake_social_proof": ("Unverified social proof ('N people viewing')", "False urgency", 5),
    "confirmshaming": ("Confirm-shaming (guilt-trip wording)", "Confirm shaming", 6),
    "preselected_addon": ("Pre-selected paid add-on", "Basket sneaking", 10),
    "forced_action": ("Forced action (must do X to continue)", "Forced action", 8),
    "subscription_trap": ("Subscription trap (hard to exit)", "Subscription trap", 10),
    "nagging": ("Nagging / retention interruptions", "Nagging", 4),
}

COMPUTED_INFO = {
    "drip_pricing": ("Drip pricing (price grows during checkout)", "Drip pricing"),
    "basket_sneaking": ("Basket sneaking (paid add-on pre-added)", "Basket sneaking"),
    "personalization": ("Price differs by persona beyond noise", "Unfair trade practice (personalised pricing)"),
    "friction": ("Cancellation harder than sign-up", "Subscription trap"),
}


def title(cls: str) -> str:
    return INFO[cls][0]


def ccpa(cls: str) -> str:
    return INFO[cls][1]
