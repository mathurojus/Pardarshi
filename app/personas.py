"""Persona matrix: one-factor-at-a-time around a baseline.

A full factorial (7 countries x 3 devices x 2 states) is far too expensive for real
agent runs. A baseline plus one variation per factor, repeated K times, is enough for the
regression in ml/personalization.py to separate each factor's effect from noise.
"""
from __future__ import annotations

from app.capture.schema import Persona

BASELINE = Persona(id="base", country="US", device="desktop", user_state="new")


def default_matrix(countries: list[str] | None = None) -> list[Persona]:
    countries = countries or ["DE", "JP"]
    personas = [
        BASELINE,
        Persona(id="iphone", country="US", device="iphone", user_state="new"),
        Persona(id="android", country="US", device="android", user_state="new"),
        Persona(id="returning", country="US", device="desktop", user_state="returning"),
    ]
    for c in countries:
        personas.append(Persona(id=f"c_{c.lower()}", country=c, device="desktop", user_state="new"))
    return personas


def localise(personas: list[Persona], country: str) -> list[Persona]:
    """The same matrix for shoppers at home in `country`: every US persona becomes local (language, timezone),
    the foreign-country variations stay as they are (they need a country proxy, and are dropped without one)."""
    return [p.model_copy(update={"country": country}) if p.country == BASELINE.country else p for p in personas]


def quick_matrix() -> list[Persona]:
    """Small matrix for smoke tests and cheap live checks."""
    return [BASELINE, Persona(id="iphone", country="US", device="iphone", user_state="new")]
