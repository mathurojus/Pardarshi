"""Single entry point for Gemini calls, with retries and a model fallback chain.

Free-tier Gemini models are intermittently overloaded (503) or out of quota (429), so no
feature should depend on one model name.
"""
from __future__ import annotations

import json
import os
import time

from google import genai
from google.genai import types

from app.config import settings

# Free-tier keys allow only ~20 requests/day PER MODEL, so the chain spreads load across several models.
DEFAULT_CHAIN = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.7-flash", "gemini-3-flash-preview",
                 "gemini-flash-latest", "gemini-3.5-flash"]


def model_chain() -> list[str]:
    env = os.getenv("GEMINI_MODELS")
    chain = [m.strip() for m in env.split(",")] if env else list(DEFAULT_CHAIN)
    if os.getenv("GEMINI_MODEL"):
        chain.insert(0, os.getenv("GEMINI_MODEL"))
    return chain


def available() -> bool:
    return bool(settings.gemini_api_key)


def _client() -> genai.Client:
    # An explicit timeout matters: the SDK default can hang for minutes on an overloaded model.
    return genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(timeout=60_000))


def generate(prompt: str | list, *, json_schema=None, temperature: float = 0.2, system: str | None = None,
             rounds: int = 2) -> str:
    """Return response text. Tries each model in the chain, `rounds` times, with backoff."""
    if not available():
        raise RuntimeError("GEMINI_API_KEY is not set")
    cfg = dict(temperature=temperature, system_instruction=system)
    if json_schema is not None:
        cfg.update(response_mime_type="application/json", response_schema=json_schema)
    config = types.GenerateContentConfig(**cfg)
    client, last = _client(), None
    for r in range(rounds):
        for model in model_chain():
            try:
                resp = client.models.generate_content(model=model, contents=prompt, config=config)
                if resp.text:
                    return resp.text
            except Exception as e:  # 429 / 503 / 404: try the next model
                last = e
        time.sleep(2 + 3 * r)
    raise RuntimeError(f"All Gemini models failed: {str(last)[:200]}")


def generate_json(prompt: str | list, *, json_schema, temperature: float = 0.2, system: str | None = None):
    return json.loads(generate(prompt, json_schema=json_schema, temperature=temperature, system=system))
