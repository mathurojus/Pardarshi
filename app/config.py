"""Central settings, loaded once from the environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    provider: str = os.getenv("PROVIDER", "opensource").lower()
    tinyfish_api_key: str = os.getenv("TINYFISH_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    data_dir: Path = Path(os.getenv("PARDARSHI_DATA_DIR", str(ROOT / "data")))
    demo_shop_url: str = os.getenv("DEMO_SHOP_URL", "http://127.0.0.1:8100").rstrip("/")
    headless: bool = _bool("HEADLESS", True)
    # Payment guard: any page that looks like a card form ends the run.
    max_steps: int = int(os.getenv("MAX_AGENT_STEPS", "25"))
    # Polite mode (real sites): one shopper at a time and a human-like pause between actions.
    polite_delay: tuple[float, float] = (float(os.getenv("POLITE_MIN_DELAY", "1.5")), float(os.getenv("POLITE_MAX_DELAY", "4.0")))
    # Parallel shopper agents: browser sessions allowed at once (demo shops run every shopper concurrently).
    max_parallel: int = int(os.getenv("MAX_PARALLEL", "20"))
    home_country: str = os.getenv("HOME_COUNTRY", "IN").upper()  # where real-site audits on this machine are seen from
    # Comma-separated hostnames the auditor may visit. The demo shop is always allowed.
    allowed_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            h.strip().lower() for h in os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()
        )
    )

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def training_dir(self) -> Path:
        return self.data_dir / "training"


settings = Settings()
