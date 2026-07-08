"""Shared OpenRouteService env helpers for CLI and local tooling."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    load_dotenv(ROOT / ".env")


def resolve_api_key(explicit_key: str | None) -> str:
    api_key = explicit_key or os.environ.get("ORS_API_KEY")
    if not api_key:
        raise SystemExit(
            "OpenRouteService API key required. Add ORS_API_KEY to .env, "
            "set the env var, or pass --api-key."
        )
    return api_key
