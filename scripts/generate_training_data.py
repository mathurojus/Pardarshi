"""Generate a labelled UI-text corpus with Gemini (run once; output is cached on disk).

Why synthetic: the public dark-pattern datasets are small and not packaged for this task.
The generated labels are NOT trusted blindly: the model is evaluated on the human-written
HELDOUT set in app/ml/seed_data.py and on the demo shop's known ground truth.

Usage:  python -m scripts.generate_training_data
"""
from __future__ import annotations

import json
import random

from google.genai import types

from app import llm
from app.config import settings
from app.ml.taxonomy import CLASSES

DESCRIPTIONS = {
    "none": "Ordinary, honest UI copy with NO manipulation: navigation, buttons, neutral price rows (fees, totals, "
            "taxes), honest deadlines that state a real date, honest availability info, plain 'No thanks' buttons "
            "without guilt, legal footers, helpful policy text like 'free cancellation until 24h before', "
            "unchecked optional extras described neutrally.",
    "false_urgency": "Countdown timers or deadline pressure meant to rush a purchase: 'deal ends in 09:59', "
                     "'price rises at midnight', 'book within 5 minutes', 'flash sale ending'.",
    "scarcity": "Scarcity claims: 'only 2 left', 'last room at this price', 'selling fast', 'almost sold out', "
                "'94% booked', 'low stock'.",
    "fake_social_proof": "Unverifiable social proof used to pressure: 'N people are viewing this now', 'Priya from "
                         "Pune just booked', 'N bought in the last hour', 'trending now'.",
    "confirmshaming": "Opt-out/decline wording that guilt-trips or shames the user: 'No thanks, I don't want to "
                      "save money', 'I'd rather pay full price', 'I like taking risks'.",
    "preselected_addon": "Labels of PRE-TICKED paid extras the shopper did not ask for: 'Add travel insurance for "
                         "Rs 199', 'Yes, add priority boarding', 'Include premium support', 'Donate Rs 10'. "
                         "These are labels next to a checkbox that is already checked.",
    "forced_action": "Forcing an unrelated action before continuing: 'create an account to see the price', 'install "
                     "our app to continue', 'accept marketing emails to proceed', 'share contacts'.",
    "subscription_trap": "Text that makes subscriptions hard to exit or hides renewal: 'trial converts "
                         "automatically', 'to cancel call between 9 and 11', '30 days written notice', 'contact "
                         "support to end membership', tiny 'End membership' link.",
    "nagging": "Repeated interruptions and retention pop-ups: 'Wait! Don't leave', 'Are you sure you want to "
               "leave?', 'turn on notifications', 'rate us now', 'you left something in your cart'.",
}
DOMAINS = [
    "flight and train booking", "hotel booking", "food delivery", "event ticketing", "online fashion shopping",
    "electronics e-commerce", "SaaS subscriptions", "OTT/streaming subscriptions", "fintech and insurance",
    "cab and ride hailing", "grocery delivery", "telecom recharge", "online courses", "gym memberships",
]
ROLES = ["button", "link", "label", "text", "heading"]

SCHEMA = types.Schema(type=types.Type.ARRAY, items=types.Schema(
    type=types.Type.OBJECT,
    properties={"text": types.Schema(type=types.Type.STRING),
                "role": types.Schema(type=types.Type.STRING, enum=ROLES),
                "checked": types.Schema(type=types.Type.BOOLEAN)},
    required=["text", "role", "checked"]))


def generate(cls: str, domains: list[str], n: int) -> list[dict]:
    prompt = (
        f"You are building a labelled dataset of short website UI strings for a dark-pattern detector.\n"
        f"Class: {cls}\nDefinition: {DESCRIPTIONS[cls]}\n\n"
        f"Write {n} DIVERSE, realistic strings in this class as they would appear on websites in these domains: "
        f"{', '.join(domains)}. Mix Indian and global contexts (Rs, INR, $, EUR), mix tones (blunt, polite, "
        f"playful), vary length (2 to 25 words) and vary the numbers. Do not repeat phrasings. "
        f"For each give: text, role (button|link|label|text|heading), checked (true only if this is the label of "
        f"a pre-ticked checkbox; true for most preselected_addon rows, false otherwise)."
    )
    rows = llm.generate_json(prompt, json_schema=SCHEMA, temperature=1.0)
    return [{"text": r["text"].strip(), "role": r["role"], "checked": bool(r["checked"]), "label": cls}
            for r in rows if 3 <= len(r["text"].strip()) <= 300]


def main(per_call: int = 45, calls_per_class: int = 3, none_calls: int = 6) -> None:
    out_dir = settings.training_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "generated.jsonl"
    rng = random.Random(7)
    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for cls in CLASSES:
        for i in range(none_calls if cls == "none" else calls_per_class):
            doms = rng.sample(DOMAINS, 4)
            try:
                batch = generate(cls, doms, per_call)
            except Exception as e:
                print(f"  skipped {cls} call {i + 1}: {e}"[:160])
                continue
            fresh = [r for r in batch if (r["label"], r["text"].lower()) not in seen]
            seen.update((r["label"], r["text"].lower()) for r in fresh)
            rows.extend(fresh)
            print(f"{cls:18s} call {i + 1}: +{len(fresh)} (total {len(rows)})", flush=True)
            out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")  # keep progress
    print(f"wrote {len(rows)} rows to {out}", flush=True)


if __name__ == "__main__":
    main()
