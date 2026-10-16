"""The audit assistant must answer from the audit's own numbers. Uses the shipped sample reports."""
import asyncio
import json

import pytest

from app import assistant
from app.config import ROOT


@pytest.fixture(scope="module")
def dirty():
    return json.loads((ROOT / "samples" / "dirty" / "report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def clean():
    return json.loads((ROOT / "samples" / "clean" / "report.json").read_text(encoding="utf-8"))


def a(r, q):
    return assistant.local_answer(r, q)["text"]


def rupees(x):
    return f"₹{round(x):,}"


def test_summary_states_the_score_and_top_findings(dirty):
    t = a(dirty, "Give me a summary of this audit")
    assert f"{round(dirty['trust_score'])} out of 100" in t and "poor" in t and "Drip pricing" in t


def test_hidden_fees_use_the_real_numbers(dirty):
    wf = dirty["waterfalls"][0]
    t = a(dirty, "What are the hidden fees?")
    assert rupees(wf["advertised"]) in t and rupees(wf["final"]) in t and f"+{wf['drip_pct']:.1f}%" in t
    assert "Convenience fee" in t and "pre-ticked" in t


def test_personalization_names_groups_and_noise(dirty):
    eff = next(e for e in dirty["personalization"]["advertised"]["effects"] if e["level"] == "iphone")
    t = a(dirty, "Does the price depend on my device?")
    assert "iPhone" in t and f"+{eff['effect_pct']:.1f}%" in t
    assert "iPhone" in a(dirty, "Do iPhone users pay more?") and "real difference" in a(dirty, "Do iPhone users pay more?")
    if not any(w["persona_id"] == "android" for w in dirty["waterfalls"]):  # the sample is a quick run: iPhone only
        assert "not tested in this audit" in a(dirty, "Do android users pay more?")


def test_friction_and_policy(dirty):
    clicks = round(dirty["friction"]["cancel_clicks"])
    assert f"{clicks} clicks" in a(dirty, "How hard is it to cancel?") and "1 click to sign up" in a(dirty, "How hard is it to cancel?")
    n = sum(f["type"] == "policy" for f in dirty["findings"])
    if n:  # policy checks need Gemini; a sample made without it has none
        t = a(dirty, "Do the written policies match?")
        assert f"{n} written promise" in t


def test_patterns_and_specific_pattern(dirty):
    assert "dark patterns" in a(dirty, "What dark patterns were found?")
    assert "countdown" in a(dirty, "Is the countdown timer real?").lower()


def test_formula_and_advice_and_complaint(dirty):
    assert "100 times e to the power" in a(dirty, "How is the score calculated?")
    assert "pre-selected add-ons" in a(dirty, "What should I do?")
    assert "Draft a complaint" in a(dirty, "I want to file a complaint")


def test_clean_site_gets_honest_good_news(clean):
    assert "100 out of 100" in a(clean, "summary") and "Nothing was flagged" in a(clean, "summary")
    assert "no hidden cost" in a(clean, "What are the hidden fees?")
    assert "not test" in a(clean, "How hard is it to cancel?") or "clicks" in a(clean, "How hard is it to cancel?")


def test_unknown_question_is_honest_not_invented(dirty):
    t = a(dirty, "What colour is the CEO's car?")
    assert "could not find that in this audit" in t


def test_suggestions_are_offered(dirty):
    assert len(assistant.local_answer(dirty, "summary")["suggestions"]) >= 2


def test_context_contains_the_whole_audit(dirty):
    ctx = assistant.build_context(dirty)
    for needle in ("TRANSPARENCY SCORE: 17/100", "Drip pricing", "iphone", "CANCELLATION: 8.0 clicks", "Ticket Protect"):
        assert needle in ctx


def test_ask_routes_short_known_questions_locally_and_survives_llm_failure(dirty, monkeypatch):
    out = asyncio.run(assistant.ask(dirty, "What are the hidden fees?"))
    assert out["method"] == "local"
    # a long open-ended question goes to Gemini; if Gemini fails the local engine still answers
    monkeypatch.setattr(assistant.llm, "available", lambda: True)
    monkeypatch.setattr(assistant.llm, "generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")))
    long_q = "Considering everything you know about this site, can you walk me through how the fees and the cancellation maze affect me as a student?"
    out = asyncio.run(assistant.ask(dirty, long_q))
    assert out["method"] == "local" and out["text"]
    monkeypatch.setattr(assistant.llm, "generate", lambda *a, **k: "Gemini says hello.")
    assert asyncio.run(assistant.ask(dirty, long_q))["method"] == "gemini"
